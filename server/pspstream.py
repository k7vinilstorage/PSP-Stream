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


def build_source(args):
    if args.source == "static":
        from sources import StaticSource
        return StaticSource(Path(args.image).read_bytes())
    raise SystemExit(f"fonte desconhecida: {args.source}")


def parse_args(argv=None):
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="PSPStream: transmite a tela do PC para o PSP (MJPEG).")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT, help="porta TCP (padrão %(default)s)")
    p.add_argument("--bind", default="0.0.0.0", help="endereço local (padrão %(default)s)")
    p.add_argument("--source", choices=["static"], default="static",
                   help="de onde vêm os frames (padrão %(default)s)")
    p.add_argument("--image", default=str(here.parent / "assets" / "testcard.jpg"),
                   help="imagem JPEG do modo static (padrão: assets/testcard.jpg)")
    p.add_argument("--stats-interval", type=float, default=2.0, help="segundos entre linhas de estatística")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    source = build_source(args)
    source.start()

    srv = socket.create_server((args.bind, args.port))
    log.info("aguardando o PSP em %s:%d (coloque este IP no server.txt)", local_ip(), args.port)
    current = None
    try:
        while True:
            conn, addr = srv.accept()
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
