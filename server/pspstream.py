#!/usr/bin/env python3
"""PSPStream - servidor.

Envia a tela do PC ao PSP como MJPEG no modelo "pull": o PSP pede um frame, o
servidor responde com o mais recente e descarta os antigos. Assim nunca se
forma fila na rede, e a latência fica perto de um frame. O transporte pode ser
TCP ou UDP (o PSP escolhe no server.txt); o servidor atende os dois na mesma
porta.
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
from stats import SessionStats, format_summary, now_ms
from transports import TcpTransport, UdpTransport, parse_datagram

log = logging.getLogger("pspstream")

# Sem frame novo por este tempo, reenvia o último para a conexão não morrer
# (no Wayland o compositor só manda frames quando a tela muda).
KEEPALIVE_S = 1.0


def local_ip() -> str:
    """IP da interface usada para sair para a rede (nenhum pacote é enviado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class Session:
    """Um PSP conectado. O transporte (TCP/UDP) entrega pedidos e envia frames."""

    def __init__(self, transport, source, args, injector=None):
        self.transport = transport
        transport.session = self  # antes de qualquer pedido chegar (UDP entrega na hora)
        self.source = source
        self.args = args
        self.injector = injector
        adaptive = None
        if args.adaptive and not args.bench and source.quality is not None:
            from adaptive import AdaptiveQuality
            adaptive = AdaptiveQuality(source, args.target_fps, args.q_min, args.q_max)
        self.stats = SessionStats(args.stats_interval, adaptive, source, transport)
        self.cond = threading.Condition()
        self.pending = False   # há um pedido de frame esperando resposta
        self.arrived = 0.0     # quando esse pedido chegou
        self.alive = True
        self.frame_no = 0
        self.hello_seen = False
        self.wifi = None  # (sinal %, flags) informados pelo PSP
        self.ping = None  # (select, polling, usando polling) medidos pelo PSP no início
        self.h264_warned = False

    def run(self) -> None:
        log.info("PSP conectado via %s: %s:%d", self.transport.name.upper(), *self.transport.addr)
        self.transport.start(self)
        if self.args.bench:
            threading.Thread(target=self._bench, name="bench", daemon=True).start()
        try:
            self._sender()
        except OSError as exc:
            if self.alive:
                log.info("envio falhou: %s", exc)
        finally:
            self.close()
            if self.injector:
                self.injector.release_all()
            saved = getattr(self.transport, "hdr_saved", 0)
            log.info("PSP desconectado (%d frames, %.1f MB enviados%s)",
                     self.stats.total_frames, self.stats.total_bytes / 1e6,
                     f", {saved / 1024:.0f} KB de cabeçalho JPEG economizados" if saved else "")

    def close(self) -> None:
        with self.cond:
            if not self.alive:
                return
            self.alive = False
            self.cond.notify_all()
        self.transport.close()

    def on_request(self, req: Request) -> None:
        if req.flags & protocol.REQ_BYE:
            log.info("PSP saiu")
            self.close()
            return
        if req.flags & REQ_HELLO:
            if not self.hello_seen:
                log.info("PSP iniciou o stream")
            else:
                log.debug("HELLO repetido (PSP achou que o stream parou)")
            self.hello_seen = True
        if self.injector:
            self.injector.update(req.buttons, req.lx, req.ly)
        if req.signal:
            self._wifi(req.signal, req.wflags)
        if self.args.codec == "h264" and not req.wflags & protocol.CAP_H264 and not self.h264_warned:
            self.h264_warned = True
            log.warning("o PSP não decodifica H.264 (EBOOT anterior à v0.5, ou h264=0 no server.txt): "
                        "atualize o EBOOT ou rode o servidor com --codec jpeg")
        ping = (req.ping_select, req.ping_poll, req.wflags & protocol.WIFI_RX_POLL)
        if ping[:2] != (0, 0) and ping != self.ping:
            self.ping = ping
            log.info("ida e volta pura PSP <-> PC (pacote pequeno, rede parada): %s",
                     format_ping(*ping))
        if req.ack_frame:
            self.stats.on_ack(req, now_ms())
        if req.flags & REQ_FRAME:
            with self.cond:
                # No máximo um pedido pendente: pedidos repetidos (o PSP reenvia
                # no UDP se a resposta demora) não viram uma rajada de frames.
                if not self.pending:
                    self.pending = True
                    self.arrived = time.monotonic()
                self.cond.notify_all()

    def _wifi(self, signal: int, flags: int) -> None:
        old = self.wifi
        self.wifi = (signal, flags)
        power_save = flags & protocol.WIFI_POWER_SAVE
        if old is None or (old[1] & protocol.WIFI_POWER_SAVE) != power_save or abs(old[0] - signal) >= 15:
            log.info("Wi-Fi do PSP: sinal %d%%, economia de energia WLAN %s", signal,
                     "LIGADA" if power_save else "desligada")
            if power_save:
                log.warning("a economia de energia WLAN do PSP segura os pacotes no roteador e aumenta "
                            "muito a latência: desligue em Ajustes > Ajustes de economia de energia")

    def _next_frame(self, last_seq: int):
        """Frame mais novo que last_seq; após KEEPALIVE_S reenvia o último.
        Devolve (seq, jpeg, ready_t, reenvio) ou None."""
        if self.source.repeat:
            return (*self.source.latest(), False)
        deadline = time.monotonic() + KEEPALIVE_S
        while self.alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return (*self.source.latest(), True)
            got = self.source.wait_newer(last_seq, min(remaining, 0.1))
            if got:
                return (*got, False)
        return None

    def _sender(self) -> None:
        last_seq = 0
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.pending or not self.alive)
                if not self.alive:
                    return
                arrived = self.arrived
            got = self._next_frame(last_seq)
            if got is None:
                return
            seq, jpeg, ready_t, resend = got
            if jpeg is None:  # fonte ainda não produziu nada
                time.sleep(0.01)
                continue
            if len(jpeg) > protocol.MAX_JPEG:
                log.warning("frame de %d KB excede o limite de %d KB; descartado",
                            len(jpeg) // 1024, protocol.MAX_JPEG // 1024)
                last_seq = seq
                continue
            with self.cond:
                self.pending = False
            last_seq = seq
            self.frame_no += 1
            send_ms = now_ms()
            age_ms = (time.monotonic() - ready_t) * 1000 if not self.source.repeat else 0.0
            wait_ms = (time.monotonic() - arrived) * 1000
            sent = self.transport.send_frame(self.frame_no, jpeg, send_ms)
            self.stats.on_send(self.frame_no, send_ms, age_ms, sent, wait_ms, self.source.capture_ms, resend)
            self.stats.maybe_report(self.source.quality)

    def _bench(self) -> None:
        """Varre qualidades fixas e imprime uma tabela (números do hardware)."""
        qualities = [int(q) for q in self.args.bench.split(",")]
        rows = []
        for q in qualities:
            self.source.set_quality(q)
            log.info("benchmark: qualidade %d (%.0f s)", q, self.args.bench_seconds)
            time.sleep(2)  # aquecimento: frames da qualidade anterior saem do caminho
            if not self.alive:
                return
            self.stats.start_phase()
            time.sleep(self.args.bench_seconds)
            if not self.alive:
                return
            summary = self.stats.phase_summary(self.source.quality)
            log.info("benchmark q%s: %s", q, format_summary(summary))
            rows.append(summary)
        table = [
            "| q | KB/frame | FPS | fonte (fps) | Wi-Fi (KB/s) | latência média (ms) | p95 (ms) | rede (ms) "
            "| 1º pedaço (ms) | ping no stream (ms) | rajada (ms) | vazão na rajada (KB/s) "
            "| espera por frame novo (ms) | tempo morto entre frames (ms) | pedido antecipado (KB) "
            "| decode (ms) | PSP recebido->exibido (ms) | reenvios 1 s | pedaços reenviados | frames perdidos |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ] + [
            f"| {r['quality']} | {r['kb_per_frame']:.1f} | {r['fps']:.1f} | "
            f"{'-' if r['source_fps'] is None else format(r['source_fps'], '.1f')} | "
            f"{r['wifi_kbps']:.0f} | {r['latency_ms']:.1f} | {r['latency_p95_ms']:.1f} | {r['transfer_ms']:.1f} | "
            f"{r['first_ms']:.1f} (mín {r['first_min_ms']:.1f}, mediana {r['first_med_ms']:.1f}) | "
            f"{r['ping_ms']:.1f} (mín {r['ping_min_ms']:.1f}) | {r['burst_ms']:.1f} | "
            f"{r['burst_kbps']:.0f} | "
            f"{r['wait_ms']:.1f} | {'-' if r['idle_ms'] is None else format(r['idle_ms'], '+.1f')} | "
            f"{format(r['early_kb'], '.1f') if r['early_kb'] else 'no fim'} | "
            f"{r['decode_ms']:.1f} | {r['local_ms']:.1f} | {r['keepalive']} | "
            f"{r['resent_pct']:.1f}% | {r['lost']} |"
            for r in rows
        ]
        wifi = self.wifi or (0, 0)
        table.append("")
        table.append(f"Wi-Fi do PSP: sinal {wifi[0]}%, economia de energia WLAN "
                     f"{'LIGADA' if wifi[1] & protocol.WIFI_POWER_SAVE else 'desligada'}")
        if self.ping:
            table.append(f"Ida e volta pura (ping no início do stream): {format_ping(*self.ping)}")
        if isinstance(self.transport, UdpTransport):
            table.append(f"Cache do cabeçalho JPEG: {'ligado' if self.transport.hdr_cache else 'desligado'}; "
                         f"DSCP: {self.args.dscp}")
        out = Path(f"bench_{time.strftime('%Y%m%d_%H%M%S')}.md")
        out.write_text(f"Fonte: {self.args.source} {self.args.size[0]}x{self.args.size[1]}, "
                       f"codec: {self.args.codec.upper()}, "
                       f"transporte: {self.transport.name.upper()}\n\n" + "\n".join(table) + "\n")
        log.info("benchmark concluído, tabela salva em %s:\n%s", out, "\n".join(table))


def format_ping(select_t: int, poll_t: int, polling: int) -> str:
    """Valores do PSP em 0,1 ms."""
    parts = []
    if select_t:
        parts.append(f"{select_t / 10:.1f} ms esperando com select()")
    if poll_t:
        parts.append(f"{poll_t / 10:.1f} ms consultando o socket")
    return ", ".join(parts) + f"; PSP usando {'consulta' if polling else 'select()'}"


DSCP = {"ef": 0xB8, "cs5": 0xA0, "af41": 0x88, "0": 0}


def set_dscp(sock: socket.socket, name: str) -> None:
    """Marca os pacotes do servidor (WMM): com EF, a placa Wi-Fi do PC e o
    roteador usam a fila de voz, que disputa o ar com prioridade."""
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP[name])
    except OSError as exc:
        log.debug("DSCP não aplicado: %s", exc)


class Server:
    """Um PSP por vez. Uma conexão nova (TCP ou HELLO por UDP) derruba a
    anterior: o PSP pode ter reiniciado o app e deixado a sessão velha pendurada."""

    def __init__(self, source, args, injector):
        self.source = source
        self.args = args
        self.injector = injector
        self.lock = threading.Lock()
        self.current = None  # (Session, Thread)
        self.running = True
        self.old_warned = set()  # endereços de PSPs com EBOOT antigo já avisados

    def replace(self, transport):
        """None se o servidor está fechando (um pedido que chegou junto com o Ctrl+C)."""
        with self.lock:
            if not self.running:
                transport.close()
                return None
            old = self.current
            if old is not None:
                old[0].close()
            session = Session(transport, self.source, self.args, self.injector)
            thread = threading.Thread(target=session.run, name="session", daemon=True)
            self.current = (session, thread)
        if old is not None:
            old[1].join(timeout=2)
        thread.start()
        return session

    def serve_udp(self, sock: socket.socket) -> None:
        sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                req, nack = parse_datagram(data)
            except protocol.OldEbootError as exc:
                if addr not in self.old_warned:  # sem isso, o PSP só fica sem imagem
                    self.old_warned.add(addr)
                    log.warning("%s:%d: %s", *addr, exc)
                continue
            except (ValueError, Exception):  # lixo na porta: ignora
                continue
            if req.flags & protocol.REQ_PING:  # responde já, sem passar pela sessão
                try:
                    sock.sendto(protocol.pack_pong(req.echo_ts), addr)
                except OSError:
                    pass
                continue
            with self.lock:
                cur = self.current[0] if self.current else None
            same = cur is not None and cur.alive and isinstance(cur.transport, UdpTransport) \
                and cur.transport.addr == addr
            if not same:
                # Só um HELLO (ou um PSP sem sessão nenhuma ativa) abre sessão:
                # datagramas atrasados de um cliente antigo são ignorados.
                if req.flags & protocol.REQ_BYE or not (req.flags & REQ_HELLO or cur is None or not cur.alive):
                    continue
                cur = self.replace(UdpTransport(sock, addr, self.args.udp_pace, self.args.hdr_cache))
                if cur is None:
                    return
            try:
                cur.transport.feed(req, nack)
            except Exception:  # um datagrama ruim não pode derrubar a thread do UDP
                log.exception("erro tratando pedido UDP")

    def close(self):
        with self.lock:
            self.running = False
            if self.current is not None:
                self.current[0].close()


def parse_size(text: str):
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("use LARGURAxALTURA, ex.: 480x272") from None
    if not (16 <= w <= 480 and 16 <= h <= 272):
        raise argparse.ArgumentTypeError("o PSP exibe no máximo 480x272")
    return w, h


def build_source(args, portal=None):
    """portal: sessão do portal já aberta (refazer o pipeline sem novo diálogo)."""
    w, h = args.size
    if args.source == "static":
        from sources import StaticSource
        try:
            from gst_source import transcode_image
        except (ImportError, ValueError):
            # Sem GStreamer: envia o arquivo como está (precisa ser JPEG 4:2:0
            # de até 480x272) e a qualidade não muda.
            return StaticSource(Path(args.image).read_bytes())

        if args.codec == "h264":
            from h264 import H264Encoder, image_to_i420
            raw = image_to_i420(args.image, w, h, not args.stretch, args.scale)
            enc = H264Encoder(w, h, args.quality)

            def reencode(q):
                enc.set_quality(q)
                return enc.encode(raw)
        else:
            def reencode(q):
                return transcode_image(args.image, w, h, q, not args.stretch, args.scale)

        return StaticSource(reencode(args.quality), reencode, args.quality)

    if args.source == "kms":
        from kms import KmsSource
        return KmsSource(w, h, args.fps, args.quality, args.scale, not args.stretch, args.codec,
                         args.kms_card, args.kms_monitor)

    from gst_source import SOURCES, GstSource
    keepalive, gpu_from = portal, None
    if args.source == "portal":
        if keepalive is None:
            keepalive = open_portal(args)
        if args.dmabuf:
            gpu_from = tuple(keepalive.size) if keepalive.size else (w, h)
            if not keepalive.size and not args.stretch:
                log.warning("--dmabuf: o portal não disse o tamanho da tela; a imagem pode sair esticada")
        src = keepalive.gst_source(dmabuf=args.dmabuf)
    elif args.source == "gst":
        if not args.gst_src:
            raise SystemExit("--source gst precisa de --gst-src \"<elementos GStreamer>\"")
        src = args.gst_src
    else:
        src = SOURCES[args.source]
    return GstSource(src, w, h, args.fps, args.quality, args.scale, not args.stretch, keepalive, args.codec,
                     gpu_from)


DMABUF_FIRST_FRAME_S = 5


def open_portal(args):
    from portal import open_screencast
    return open_screencast(window=args.window, cursor=not args.no_cursor, remember=not args.forget)


def start_source(args, portal=None):
    """--dmabuf é experimental: se o pipeline não sobe ou não sai frame em
    alguns segundos (DMA-BUF ou OpenGL indisponível), volta para a captura
    pela memória comum na mesma sessão do portal (sem outro diálogo)."""
    if args.source == "portal" and portal is None:
        portal = open_portal(args)
    if not args.dmabuf:
        source = build_source(args, portal)
        source.start()
        return source
    source = None
    try:
        source = build_source(args, portal)
        source.start()
        deadline = time.monotonic() + DMABUF_FIRST_FRAME_S
        got = None
        while not got and not source.failed and time.monotonic() < deadline:
            got = source.wait_newer(0, 0.1)
        reason = source.failed or (None if got else f"nenhum frame em {DMABUF_FIRST_FRAME_S} s")
    except Exception as exc:  # pipeline que não monta (GLib.Error) ou não inicia
        reason = str(exc)
    if reason is None:
        log.info("captura: DMA-BUF + redução na GPU (OpenGL), --dmabuf")
        return source
    log.warning("--dmabuf não funcionou (%s); voltando para a captura pela memória comum", reason)
    if source is not None:
        source.stop()
    args.dmabuf = False
    fallback = build_source(args, portal)
    fallback.start()
    return fallback


def parse_args(argv=None):
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="PSPStream: transmite a tela do PC para o PSP (MJPEG).")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT,
                   help="porta TCP e UDP (padrão %(default)s)")
    p.add_argument("--codec", choices=["auto", "jpeg", "h264"], default="auto",
                   help="h264: todo frame IDR, decodificado pelo hardware do PSP (EBOOT v0.5+); no PSP-3000, "
                        "23-30%% dos bytes do JPEG e 1,5-3x o FPS na mesma qualidade. auto (padrão) = h264 se "
                        "o openh264enc estiver instalado, senão jpeg")
    p.add_argument("--udp-pace", type=float, default=0, metavar="KB/s",
                   help="UDP: limitar a taxa de envio dos pedaços (0 = sem limite, padrão)")
    p.add_argument("--no-hdr-cache", dest="hdr_cache", action="store_false",
                   help="UDP: mandar o cabeçalho JPEG em todo frame (para comparar; o padrão manda só "
                        "quando muda)")
    p.add_argument("--dscp", choices=list(DSCP), default="ef",
                   help="marcação dos pacotes do servidor para a fila de prioridade do Wi-Fi (WMM): "
                        "ef = voz (padrão), cs5/af41 = vídeo, 0 = nenhuma")
    p.add_argument("--bind", default="0.0.0.0", help="endereço local (padrão %(default)s)")
    p.add_argument("--source", choices=["portal", "kms", "test", "x11", "gst", "static"], default="portal",
                   help="portal = tela no Wayland (padrão); kms = direto da placa de vídeo, sem o limite de "
                        "~40 fps do GNOME 50 (precisa de make -C tools/kms e make -C tools/kms cap); "
                        "test = padrão animado com relógio; x11 = sessão X11; gst = pipeline próprio "
                        "(--gst-src); static = uma imagem")
    p.add_argument("--kms-monitor", type=int, default=0, metavar="N",
                   help="kms: qual monitor ligado (0 = o primeiro; o log mostra quantos há)")
    p.add_argument("--kms-card", metavar="/dev/dri/cardN", help="kms: placa de vídeo (padrão: procura em todas)")
    p.add_argument("--image", default=str(here.parent / "assets" / "testcard.jpg"),
                   help="imagem do modo static (padrão: assets/testcard.jpg)")
    p.add_argument("--gst-src", help="elementos GStreamer da fonte para --source gst")
    p.add_argument("--size", type=parse_size, default=(480, 272), help="resolução enviada (padrão 480x272)")
    p.add_argument("--fps", type=int, default=60,
                   help="taxa máxima de captura (padrão %(default)s). Capturar acima do que o PSP "
                        "exibe reduz a idade do frame enviado")
    p.add_argument("-q", "--quality", type=int, default=60,
                   help="qualidade JPEG 1-100: inicial (adaptativo) ou fixa (--fixed-quality). Padrão %(default)s")
    p.add_argument("--fixed-quality", dest="adaptive", action="store_false",
                   help="não adaptar a qualidade à banda medida")
    p.add_argument("--target-fps", type=float, default=20,
                   help="adaptativo: FPS que a banda precisa sustentar (padrão %(default)s, medido no "
                        "PSP-3000: ~q55 com ~48 ms; 30 é inalcançável no 802.11b e derruba a qualidade "
                        "para o mínimo). Menor = mais qualidade e mais latência por frame")
    p.add_argument("--q-min", type=int, default=25, help="adaptativo: qualidade mínima (padrão %(default)s)")
    p.add_argument("--q-max", type=int, default=90, help="adaptativo: qualidade máxima (padrão %(default)s)")
    p.add_argument("--scale", default="bilinear2",
                   choices=["nearest-neighbour", "bilinear", "bilinear2", "lanczos", "mitchell", "catrom"],
                   help="filtro de redução. bilinear2 (padrão) não serrilha e gera frames ~27%% menores que "
                        "bilinear; lanczos = texto um pouco mais nítido, ~2 ms a mais")
    p.add_argument("--stretch", action="store_true", help="esticar em vez de manter a proporção")
    p.add_argument("--window", action="store_true", help="portal: escolher uma janela em vez de um monitor")
    p.add_argument("--dmabuf", action="store_true",
                   help="portal, experimental: receber a tela na memória da GPU (DMA-BUF) e reduzir para "
                        "480x272 no OpenGL; só a imagem pequena vem para a CPU. Se não funcionar, volta "
                        "sozinho para o modo normal")
    p.add_argument("--no-cursor", action="store_true", help="portal: não desenhar o cursor")
    p.add_argument("--forget", action="store_true", help="portal: não reutilizar/guardar a escolha de tela")
    p.add_argument("--no-input", action="store_true", help="não injetar os controles do PSP no PC")
    p.add_argument("--input-dry-run", action="store_true",
                   help="só mostrar no log as teclas/movimentos que seriam injetados")
    p.add_argument("--keymap", default=str(here / "keymap.json"), help="arquivo de mapeamento (padrão keymap.json)")
    p.add_argument("--profile", default="jogo", help="perfil do keymap: jogo, desktop, setas... (padrão %(default)s)")
    p.add_argument("--mouse-speed", type=float, default=1.0, help="multiplica a velocidade do mouse do perfil")
    p.add_argument("--input-timeout", type=float, default=0.5, metavar="S",
                   help="solta todas as teclas se o PSP ficar S segundos sem mandar nada enquanto algo está "
                        "segurado (padrão %(default)s; o PSP reafirma o estado a cada ~100 ms)")
    p.add_argument("--stats-interval", type=float, default=2.0, help="segundos entre linhas de estatística")
    p.add_argument("--bench", metavar="Q1,Q2,...", nargs="?", const="30,50,70,90",
                   help="benchmark: quando o PSP conectar, roda cada qualidade por --bench-seconds e salva "
                        "uma tabela em bench_*.md (padrão 30,50,70,90)")
    p.add_argument("--bench-seconds", type=float, default=10)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # A thread de envio acorda mais rápido quando outra thread Python tem o GIL.
    sys.setswitchinterval(0.001)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    want = args.codec
    if want in ("auto", "h264"):
        try:
            import h264
            ok = h264.available()
        except (ImportError, ValueError):
            ok = False
        if want == "auto":
            args.codec = "h264" if ok and tuple(args.size) == (480, 272) else "jpeg"
            if args.codec == "jpeg":
                log.info("codec: JPEG (%s)", "sem o openh264enc: sudo dnf install gstreamer1-plugin-openh264"
                         if not ok else "--size diferente de 480x272")
    if args.codec == "h264":
        if not ok:
            log.error("--codec h264 precisa do openh264enc do GStreamer. No Fedora: "
                      "sudo dnf install gstreamer1-plugin-openh264 (repositório fedora-cisco-openh264)")
            return 1
        if tuple(args.size) != (480, 272):
            log.error("--codec h264 só funciona em 480x272 (o decoder do PSP escreve a tela inteira)")
            return 1
        log.info("codec: H.264 (todo frame IDR, decoder de hardware do PSP)")
    if args.dmabuf and args.source != "portal":
        log.warning("--dmabuf só vale para --source portal; ignorado")
        args.dmabuf = False
    try:
        source = start_source(args)
    except Exception as exc:  # erros de portal/GStreamer: mensagem curta, sem traceback
        if args.verbose:
            raise
        log.error("não foi possível iniciar a captura: %s", exc)
        return 1

    injector = None
    if not args.no_input:
        from inject import Injector, load_profile
        try:
            injector = Injector(load_profile(args.keymap, args.profile), args.input_dry_run, args.mouse_speed,
                                args.input_timeout)
            log.info("controles: perfil '%s'%s", args.profile, " (dry-run)" if args.input_dry_run else "")
        except RuntimeError as exc:
            log.warning("controles desativados: %s", exc)

    srv = socket.create_server((args.bind, args.port))
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    set_dscp(udp, args.dscp)
    udp.bind((args.bind, args.port))
    server = Server(source, args, injector)
    threading.Thread(target=server.serve_udp, args=(udp,), name="udp", daemon=True).start()
    log.info("aguardando o PSP em %s:%d, TCP e UDP (coloque este IP no server.txt)", local_ip(), args.port)
    from netcheck import check_pc_wifi
    check_pc_wifi(local_ip())
    srv.settimeout(0.5)
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
            set_dscp(conn, args.dscp)
            server.replace(TcpTransport(conn, addr))
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        server.close()
        if injector:
            injector.close()
        source.stop()
        srv.close()
        udp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
