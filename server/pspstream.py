#!/usr/bin/env python3
"""PSPStream - servidor.

Envia a tela do PC ao PSP como MJPEG usando o modelo "pull" sobre TCP: o PSP
pede um frame, o servidor responde com o mais recente e descarta os antigos.
Assim nunca se forma fila na rede, e a latência fica perto de um frame.
"""
import argparse
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import protocol
from protocol import REQ_FRAME, REQ_HELLO, Request
from stats import SessionStats, now_ms

log = logging.getLogger("pspstream")

# Sem frame novo por este tempo, reenvia o último para a conexão não morrer
# (no Wayland o compositor só manda frames quando a tela muda).
KEEPALIVE_S = 1.0
# Sem nenhuma mensagem do PSP por este tempo, a conexão é dada como morta.
IDLE_TIMEOUT_S = 10.0


def recv_exact(conn: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("PSP fechou a conexão")
        buf += chunk
    return bytes(buf)


def local_ip() -> str:
    """IP da interface usada para sair para a rede (nenhum pacote é enviado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class Session:
    def __init__(self, conn, addr, source, args, injector=None):
        self.conn = conn
        self.addr = addr
        self.source = source
        self.args = args
        self.injector = injector
        self.stats = SessionStats(args.stats_interval)
        self.cond = threading.Condition()
        self.pending = 0
        self.alive = True
        self.frame_no = 0
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(IDLE_TIMEOUT_S)

    def run(self) -> None:
        log.info("PSP conectado: %s:%d", *self.addr)
        reader = threading.Thread(target=self._reader, name="reader", daemon=True)
        reader.start()
        try:
            self._sender()
        except OSError as exc:
            if self.alive:
                log.info("envio falhou: %s", exc)
        finally:
            self.close()
            if self.injector:
                self.injector.release_all()
            log.info("PSP desconectado (%d frames, %.1f MB enviados)",
                     self.stats.total_frames, self.stats.total_bytes / 1e6)

    def close(self) -> None:
        with self.cond:
            if not self.alive:
                return
            self.alive = False
            self.cond.notify_all()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()

    def _reader(self) -> None:
        try:
            while self.alive:
                req = Request.unpack(recv_exact(self.conn, protocol.REQ_STRUCT.size))
                self._on_request(req)
        except socket.timeout:
            log.info("PSP ficou %.0f s sem responder", IDLE_TIMEOUT_S)
        except (OSError, ConnectionError, ValueError) as exc:
            if self.alive:
                log.info("leitura terminou: %s", exc)
        finally:
            self.close()

    def _on_request(self, req: Request) -> None:
        if req.flags & REQ_HELLO:
            log.info("PSP iniciou o stream")
        if self.injector:
            self.injector.update(req.buttons, req.lx, req.ly)
        if req.ack_frame:
            self.stats.on_ack(req, now_ms())
        if req.flags & REQ_FRAME:
            with self.cond:
                self.pending += 1
                self.cond.notify_all()

    def _next_frame(self, last_seq: int):
        """Frame mais novo que last_seq; após KEEPALIVE_S reenvia o último."""
        if self.source.repeat:
            return self.source.latest()
        deadline = time.monotonic() + KEEPALIVE_S
        while self.alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.source.latest()
            got = self.source.wait_newer(last_seq, min(remaining, 0.1))
            if got:
                return got
        return None

    def _sender(self) -> None:
        last_seq = 0
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.pending > 0 or not self.alive)
                if not self.alive:
                    return
                self.pending -= 1
            got = self._next_frame(last_seq)
            if got is None:
                return
            seq, jpeg, ready_t = got
            if jpeg is None:  # fonte ainda não produziu nada
                with self.cond:
                    self.pending += 1
                time.sleep(0.01)
                continue
            last_seq = seq
            if len(jpeg) > protocol.MAX_JPEG:
                log.warning("frame de %d KB excede o limite de %d KB; descartado",
                            len(jpeg) // 1024, protocol.MAX_JPEG // 1024)
                with self.cond:
                    self.pending += 1
                continue
            self.frame_no += 1
            send_ms = now_ms()
            age_ms = (time.monotonic() - ready_t) * 1000 if not self.source.repeat else 0.0
            self.conn.sendall(protocol.pack_frame_header(self.frame_no, len(jpeg), send_ms) + jpeg)
            self.stats.on_send(self.frame_no, send_ms, age_ms, len(jpeg))
            self.stats.maybe_report(self.source.quality)


def parse_size(text: str):
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("use LARGURAxALTURA, ex.: 480x272") from None
    if not (16 <= w <= 480 and 16 <= h <= 272):
        raise argparse.ArgumentTypeError("o PSP exibe no máximo 480x272")
    return w, h


def build_source(args):
    w, h = args.size
    if args.source == "static":
        from jpeginfo import jpeg_info
        from sources import StaticSource
        data = Path(args.image).read_bytes()
        try:
            ok = data[:2] == b"\xff\xd8" and not jpeg_info(data).problems(w, h)
        except ValueError:
            ok = False
        if not ok:
            from gst_source import transcode_image
            log.info("convertendo %s para %dx%d 4:2:0", args.image, w, h)
            data = transcode_image(args.image, w, h, args.quality, not args.stretch)
        return StaticSource(data)

    from gst_source import SOURCES, GstSource
    keepalive = None
    if args.source == "portal":
        from portal import open_screencast
        keepalive = open_screencast(window=args.window, cursor=not args.no_cursor,
                                    remember=not args.forget)
        src = keepalive.gst_source()
    elif args.source == "gst":
        if not args.gst_src:
            raise SystemExit("--source gst precisa de --gst-src \"<elementos GStreamer>\"")
        src = args.gst_src
    else:
        src = SOURCES[args.source]
    return GstSource(src, w, h, args.fps, args.quality, args.scale, not args.stretch, keepalive)


def parse_args(argv=None):
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="PSPStream: transmite a tela do PC para o PSP (MJPEG).")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT, help="porta TCP (padrão %(default)s)")
    p.add_argument("--bind", default="0.0.0.0", help="endereço local (padrão %(default)s)")
    p.add_argument("--source", choices=["portal", "test", "x11", "gst", "static"], default="portal",
                   help="portal = tela no Wayland (padrão); test = padrão animado com relógio; "
                        "x11 = sessão X11; gst = pipeline próprio (--gst-src); static = uma imagem")
    p.add_argument("--image", default=str(here.parent / "assets" / "testcard.jpg"),
                   help="imagem do modo static (padrão: assets/testcard.jpg)")
    p.add_argument("--gst-src", help="elementos GStreamer da fonte para --source gst")
    p.add_argument("--size", type=parse_size, default=(480, 272), help="resolução enviada (padrão 480x272)")
    p.add_argument("--fps", type=int, default=60,
                   help="taxa máxima de captura (padrão %(default)s). Capturar acima do que o PSP "
                        "exibe reduz a idade do frame enviado")
    p.add_argument("-q", "--quality", type=int, default=70, help="qualidade JPEG 1-100 (padrão %(default)s)")
    p.add_argument("--scale", default="bilinear", choices=["nearest-neighbour", "bilinear", "lanczos"],
                   help="filtro de redução (lanczos = texto mais nítido, ~5 ms a mais por frame)")
    p.add_argument("--stretch", action="store_true", help="esticar em vez de manter a proporção")
    p.add_argument("--window", action="store_true", help="portal: escolher uma janela em vez de um monitor")
    p.add_argument("--no-cursor", action="store_true", help="portal: não desenhar o cursor")
    p.add_argument("--forget", action="store_true", help="portal: não reutilizar/guardar a escolha de tela")
    p.add_argument("--stats-interval", type=float, default=2.0, help="segundos entre linhas de estatística")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        source = build_source(args)
        source.start()
    except Exception as exc:  # erros de portal/GStreamer: mensagem curta, sem traceback
        if args.verbose:
            raise
        log.error("não foi possível iniciar a captura: %s", exc)
        return 1

    srv = socket.create_server((args.bind, args.port))
    log.info("aguardando o PSP em %s:%d (coloque este IP no server.txt)", local_ip(), args.port)
    srv.settimeout(0.5)
    current = None
    try:
        while True:
            if getattr(source, "failed", None):
                log.error("captura parou: %s", source.failed)
                return 1
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            # Um PSP por vez. Se ele reconectar (app reiniciado), a sessão
            # antiga, provavelmente meio-aberta, é derrubada.
            if current is not None:
                current[0].close()
                current[1].join(timeout=2)
            session = Session(conn, addr, source, args)
            thread = threading.Thread(target=session.run, name="session", daemon=True)
            thread.start()
            current = (session, thread)
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        if current is not None:
            current[0].close()
        source.stop()
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
