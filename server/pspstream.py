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
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import protocol
from capture import build_source, open_injector, resolve_codec, start_audio, start_source  # noqa: F401
from netcheck import local_ip
from protocol import REQ_FRAME, REQ_HELLO, Request
import settings
from stats import SessionStats, Window, format_summary, now_ms
import transports
import wolf_api
from transports import DSCP, TcpTransport, UdpTransport, parse_datagram, set_dscp
import i18n
from i18n import N_, tr

log = logging.getLogger("pspstream")

WINDOWS = sys.platform == "win32"

# Sem frame novo por este tempo, reenvia o último para a conexão não morrer
# (no Wayland o compositor só manda frames quando a tela muda).
VERSION = "1.1"
KEEPALIVE_S = 1.0
# Frames que o PSP pode autorizar além do último enviado. Com frames P e
# prefetch=auto, o PSP autoriza o N+2 quando o decode pega o N: o pedido fica
# esperando aqui e o frame sai na hora da captura, sem depender da ida e volta
# daquele instante. Teto para um pedido estranho (PSP de outra sessão) não
# virar uma enxurrada de frames.
MAX_CREDIT = 2
WEB_DEFAULT = "127.0.0.1:5124"
P_PACKET_START = b"\x00\x00\x00\x01\x09"  # pacote de frames P: começa com um AUD (h264.AUD)


def p_packet_is_idr(packet: bytes) -> bool:
    """A NAL depois do AUD é SPS ou IDR (decoder_h264_packet no decode.c do PSP)."""
    i = packet.find(b"\x00\x00\x01", 5, 64)
    return 0 <= i and i + 3 < len(packet) and packet[i + 3] & 0x1F in (5, 7)


class Session:
    """Um PSP conectado. O transporte (TCP/UDP) entrega pedidos e envia frames."""

    def __init__(self, transport, source, args, injector=None, audio=None):
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
        self.want_upto = 0     # o PSP aceita frames até este número (pedidos de frame)
        self.arrived = 0.0     # quando o pedido ainda não atendido chegou
        self.alive = True
        self.frame_no = 0
        self.hello_seen = False
        self.wifi = None  # (sinal %, flags) informados pelo PSP
        self.ping = None  # (select, polling, usando polling) medidos pelo PSP no início
        self.h264_warned = False
        self.encoder = None       # --codec h264p: codifica na hora de enviar (frames P)
        self.encoder_p = None     # o encoder atual faz frames P (o PSP aceita)?
        self.p_capable = False    # o PSP informou PS_CAP_H264P
        self.idr_wanted = True    # o PSP pediu IDR (ou a sessão acabou de começar)
        self.audio = audio        # AudioCapture (som do PC) ou None
        self.audio_on = None      # o PSP pede som (CAP_AUDIO)? None = ainda não disse
        self.audio_listening = False  # a sessão manda som (UDP, rodando)
        self.started = time.monotonic()

    def run(self) -> None:
        log.info(tr("PSP connected over %s: %s:%d"), self.transport.name.upper(), *self.transport.addr)
        self.transport.start(self)
        audio_listening = hasattr(self.transport, "send_audio")  # o som só vai pelo UDP
        with self.cond:
            if self.audio is not None and audio_listening:
                self.audio.add_listener(self._on_audio)
            self.audio_listening = audio_listening
        if self.args.bench:
            threading.Thread(target=self._bench, name="bench", daemon=True).start()
        try:
            self._sender()
        except OSError as exc:
            if self.alive:
                log.info(tr("sending failed: %s"), exc)
        finally:
            self.close()
            with self.cond:
                self.audio_listening = False
                if self.audio is not None:
                    self.audio.remove_listener(self._on_audio)
            if self.encoder is not None:
                self.encoder.close()
            if self.injector:
                self.injector.release_all()
            saved = getattr(self.transport, "hdr_saved", 0)
            log.info(tr("PSP disconnected (%d frames, %.1f MB sent%s)"),
                     self.stats.total_frames, self.stats.total_bytes / 1e6,
                     tr(", {kb:.0f} KB of JPEG headers saved").format(kb=saved / 1024) if saved else "")

    def set_source(self, source) -> None:
        """Captura nova com o PSP conectado (interface web): a numeração dos
        frames continua, e o próximo frame P é um IDR (o encoder recomeça)."""
        with self.cond:
            self.source = source
            self.stats.source = source
            self.stats.window = Window(source, self.transport)
            self.stats.phase = Window(source, self.transport)
            if self.stats.adaptive is not None:
                self.stats.adaptive.source = source
            self.cond.notify_all()

    def set_audio(self, audio) -> None:
        """Captura de som nova (ou None) com o PSP conectado."""
        with self.cond:
            if self.audio is not None:
                self.audio.remove_listener(self._on_audio)
            self.audio = audio
            if audio is not None and self.audio_listening:
                audio.add_listener(self._on_audio)

    def close(self) -> None:
        with self.cond:
            if not self.alive:
                return
            self.alive = False
            self.cond.notify_all()
        self.transport.close()

    def on_request(self, req: Request) -> None:
        if req.flags & protocol.REQ_BYE:
            log.info(tr("the PSP left"))
            self.close()
            return
        if req.flags & REQ_HELLO:
            if not self.hello_seen:
                log.info(tr("the PSP started the stream"))
            else:
                log.debug(tr("repeated HELLO (the PSP thought the stream stopped)"))
            self.hello_seen = True
        if self.injector:
            self.injector.update(req.buttons, req.lx, req.ly)
        if req.signal:
            self._wifi(req.signal, req.wflags)
        self.p_capable = bool(req.wflags & protocol.CAP_H264P)
        self._audio_wanted(bool(req.wflags & protocol.CAP_AUDIO))
        if req.flags & (protocol.REQ_IDR | REQ_HELLO):
            self.idr_wanted = True
        if self.args.codec in ("h264", "h264p") and not req.wflags & protocol.CAP_H264 and not self.h264_warned:
            self.h264_warned = True
            log.warning(tr("the PSP does not decode H.264 (EBOOT older than v0.5, or h264=0 in server.txt): "
                           "update the EBOOT or run the server with --codec jpeg"))
        ping = (req.ping_select, req.ping_poll, req.wflags & protocol.WIFI_RX_POLL)
        if ping[:2] != (0, 0) and ping != self.ping:
            self.ping = ping
            log.info(tr("pure PSP <-> PC round trip (small packet, idle network): %s"),
                     format_ping(*ping))
        if req.ack_frame:
            self.stats.on_ack(req, now_ms())
        if req.flags & REQ_FRAME:
            with self.cond:
                # Pedido simples = o próximo frame: pedidos repetidos (o PSP
                # reenvia no UDP se a resposta demora) não viram uma rajada de
                # frames. Frames P: o pedido diz até qual frame (want_frame),
                # e as cópias do mesmo pedido não somam.
                upto = min(req.want_frame or self.frame_no + 1, self.frame_no + MAX_CREDIT)
                if upto > self.want_upto:
                    if self.want_upto <= self.frame_no:
                        self.arrived = time.monotonic()
                    self.want_upto = upto
                self.cond.notify_all()

    def _audio_wanted(self, on: bool) -> None:
        """O PSP liga e desliga o som (audio= no server.txt, tela de configuração
        ou SELECT + START + cima): sem CAP_AUDIO, nenhum pacote de som sai."""
        if on == self.audio_on:
            return
        self.audio_on = on
        if not hasattr(self.transport, "send_audio"):
            return  # TCP: o PSP nem pede som (só vai pelo UDP)
        if not on:
            if self.audio is not None:
                log.info(tr("audio: turned off on the PSP"))
        elif self.audio is None:
            log.info(tr("audio: the PSP asked, but the server has no audio (--no-audio, or the capture did not open)"))
        else:
            log.info(tr("audio: turned on on the PSP (%d Hz, %s, ~%.0f KB/s)"), self.audio.rate,
                     tr("stereo") if self.audio.channels == 2 else "mono", self.audio.kbps)

    def _on_audio(self, seq, pos, rate, channels, samples, block) -> None:
        """Thread da captura de som: um bloco para o PSP, se ele quer som."""
        if self.alive and self.audio_on:
            self.stats.on_audio(self.transport.send_audio(
                protocol.pack_audio(seq, pos, rate, channels, samples, block)))

    def _wifi(self, signal: int, flags: int) -> None:
        old = self.wifi
        self.wifi = (signal, flags)
        power_save = flags & protocol.WIFI_POWER_SAVE
        if old is None or (old[1] & protocol.WIFI_POWER_SAVE) != power_save or abs(old[0] - signal) >= 15:
            log.info(tr("PSP Wi-Fi: signal %d%%, WLAN power save ON") if power_save else
                     tr("PSP Wi-Fi: signal %d%%, WLAN power save off"), signal)
            if power_save:
                log.warning(tr("the PSP's WLAN power save holds the packets at the router and raises the latency a "
                               "lot: turn it off in Settings > Power Save Settings"))

    def _next_frame(self, source, last_seq: int):
        """Frame de `source` mais novo que last_seq; após KEEPALIVE_S reenvia o
        último. Devolve (seq, jpeg, ready_t, reenvio), False se a captura foi
        trocada no meio da espera, ou None (sessão encerrada)."""
        if source.repeat:
            return (*source.latest(), False)
        deadline = time.monotonic() + KEEPALIVE_S
        while self.alive:
            if self.source is not source:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return (*source.latest(), True)
            got = source.wait_newer(last_seq, min(remaining, 0.1))
            if got:
                return (*got, False)
        return None

    def _sender(self) -> None:
        last_seq = 0
        sending_from = self.source
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.frame_no < self.want_upto or not self.alive)
                if not self.alive:
                    return
                arrived = self.arrived
                source = self.source
            if source is not sending_from:  # captura trocada (interface web)
                sending_from, last_seq = source, 0
                if self.encoder is not None:  # o encoder recomeça: o próximo frame P é um IDR
                    self.encoder.close()
                    self.encoder = None
            got = self._next_frame(source, last_seq)
            if got is False:
                continue
            if got is None:
                return
            seq, jpeg, ready_t, resend = got
            if jpeg is None:  # fonte ainda não produziu nada
                time.sleep(0.01)
                continue
            if source.raw_i420:
                jpeg = self._encode(jpeg)
            if len(jpeg) > protocol.MAX_JPEG:
                log.warning(tr("a %d KB frame exceeds the %d KB limit; dropped"),
                            len(jpeg) // 1024, protocol.MAX_JPEG // 1024)
                last_seq = seq
                continue
            with self.cond:
                self.frame_no += 1  # usa o pedido; um pedido simples a partir daqui é do seguinte
                if self.frame_no < self.want_upto:
                    self.arrived = time.monotonic()  # o próximo já está pedido
            last_seq = seq
            send_ms = now_ms()
            age_ms = (time.monotonic() - ready_t) * 1000 if not source.repeat else 0.0
            wait_ms = (time.monotonic() - arrived) * 1000
            # frames P (o pacote começa com AUD): cópia do último pedaço no UDP
            p_packet = jpeg[:5] == P_PACKET_START
            sent = self.transport.send_frame(self.frame_no, jpeg, send_ms, redundant=p_packet)
            self.stats.on_send(self.frame_no, send_ms, age_ms, sent, wait_ms, source.capture_ms, resend,
                               idr=p_packet and p_packet_is_idr(jpeg))
            self.stats.maybe_report(source.quality)

    def _encode(self, i420: bytes) -> bytes:
        """--codec h264p: frames P se o PSP aceita, senão todo frame IDR (EBOOT antigo)."""
        import h264
        quality = self.source.quality or self.args.quality
        if self.encoder is None or self.encoder_p != self.p_capable:
            if self.encoder is not None:
                self.encoder.close()
            w, h = self.args.size
            self.encoder_p = self.p_capable
            if self.p_capable:
                # no benchmark cada fase troca a qualidade de propósito: aplica já
                self.encoder = h264.H264PEncoder(w, h, quality, 0.0 if self.args.bench else h264.QP_CHANGE_MIN_S)
                if self.encoder.live_qp:
                    log.info(tr("H.264: P frames (IDR only when the PSP asks; quality changes without IDR)"))
                else:
                    log.info(tr("H.264: P frames (IDR only when the PSP asks; new quality at most every %.0f s)"),
                             self.encoder.qp_change_min_s)
            else:
                self.encoder = h264.H264Encoder(w, h, quality)
                log.warning(tr("the PSP's EBOOT does not take P frames (older than v0.9, or h264p=0 in server.txt): "
                               "sending every frame as IDR"))
            self.idr_wanted = False  # encoder novo: o primeiro frame já é IDR
        self.encoder.set_quality(quality)
        if self.encoder_p and self.idr_wanted:
            self.idr_wanted = False
            if self.encoder.request_idr():
                log.debug(tr("IDR requested by the PSP"))
        return self.encoder.encode(i420)

    def _bench(self) -> None:
        """Varre qualidades fixas e imprime uma tabela (números do hardware)."""
        qualities = [int(q) for q in self.args.bench.split(",")]
        rows = []
        for q in qualities:
            self.source.set_quality(q)
            log.info(tr("benchmark: quality %d (%.0f s)"), q, self.args.bench_seconds)
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
            tr("| q | KB/frame | FPS | source (fps) | Wi-Fi (KB/s) | average latency (ms) | p95 (ms) | network (ms) "
               "| 1st chunk (ms) | in-stream ping (ms) | burst (ms) | burst throughput (KB/s) "
               "| wait for a new frame (ms) | dead time between frames (ms) | early request (KB) "
               "| decode (ms) | PSP received->shown (ms) | resends 1 s | chunks resent | frames lost |"),
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ] + [
            f"| {r['quality']} | {r['kb_per_frame']:.1f} | {r['fps']:.1f} | "
            f"{'-' if r['source_fps'] is None else format(r['source_fps'], '.1f')} | "
            f"{r['wifi_kbps']:.0f} | {r['latency_ms']:.1f} | {r['latency_p95_ms']:.1f} | {r['transfer_ms']:.1f} | "
            + tr("{first:.1f} (min {min:.1f}, median {median:.1f}) | ").format(
                first=r["first_ms"], min=r["first_min_ms"], median=r["first_med_ms"])
            + tr("{ping:.1f} (min {min:.1f}) | ").format(ping=r["ping_ms"], min=r["ping_min_ms"])
            + f"{r['burst_ms']:.1f} | "
            f"{r['burst_kbps']:.0f} | "
            f"{r['wait_ms']:.1f} | {'-' if r['idle_ms'] is None else format(r['idle_ms'], '+.1f')} | "
            f"{format(r['early_kb'], '.1f') if r['early_kb'] else tr('at the end')} | "
            f"{r['decode_ms']:.1f} | {r['local_ms']:.1f} | {r['keepalive']} | "
            f"{r['resent_pct']:.1f}% | {r['lost']} |"
            for r in rows
        ]
        wifi = self.wifi or (0, 0)
        table.append("")
        table.append((tr("PSP Wi-Fi: signal {signal}%, WLAN power save ON") if wifi[1] & protocol.WIFI_POWER_SAVE
                      else tr("PSP Wi-Fi: signal {signal}%, WLAN power save off")).format(signal=wifi[0]))
        if self.ping:
            table.append(tr("Pure round trip (ping at the start of the stream): {ping}").format(ping=format_ping(*self.ping)))
        if isinstance(self.transport, UdpTransport):
            table.append((tr("JPEG header cache: on; DSCP: {dscp}") if self.transport.hdr_cache
                          else tr("JPEG header cache: off; DSCP: {dscp}")).format(dscp=self.args.dscp))
        out = Path(f"bench_{time.strftime('%Y%m%d_%H%M%S')}.md")
        out.write_text(tr("Source: {source} {width}x{height}, codec: {codec}, transport: {transport}").format(
            source=self.args.source, width=self.args.size[0], height=self.args.size[1],
            codec=self.args.codec.upper(), transport=self.transport.name.upper()) + "\n\n" + "\n".join(table) + "\n")
        log.info(tr("benchmark done, table saved to %s:\n%s"), out, "\n".join(table))


def format_ping(select_t: int, poll_t: int, polling: int) -> str:
    """Valores do PSP em 0,1 ms."""
    parts = []
    if select_t:
        parts.append(tr("{ms:.1f} ms waiting with select()").format(ms=select_t / 10))
    if poll_t:
        parts.append(tr("{ms:.1f} ms polling the socket").format(ms=poll_t / 10))
    return ", ".join(parts) + tr("; PSP using {mode}").format(mode=tr("polling") if polling else "select()")


class Server:
    """Um PSP por vez. Uma conexão nova (TCP ou HELLO por UDP) derruba a
    anterior: o PSP pode ter reiniciado o app e deixado a sessão velha pendurada."""

    def __init__(self, source, args, injector, audio=None):
        self.source = source
        self.args = args
        self.injector = injector
        self.audio = audio
        self.lock = threading.Lock()
        self.current = None  # (Session, Thread)
        self.running = True
        self.old_warned = set()  # endereços de PSPs com EBOOT antigo já avisados

    def session(self):
        """A sessão ativa, ou None."""
        with self.lock:
            cur = self.current[0] if self.current else None
        return cur if cur is not None and cur.alive else None

    def set_source(self, source) -> None:
        with self.lock:
            self.source = source
            cur = self.current[0] if self.current else None
        if cur is not None:
            cur.set_source(source)

    def set_audio(self, audio) -> None:
        with self.lock:
            self.audio = audio
            cur = self.current[0] if self.current else None
        if cur is not None:
            cur.set_audio(audio)

    def set_injector(self, injector) -> None:
        with self.lock:
            self.injector = injector
            cur = self.current[0] if self.current else None
        if cur is not None:
            cur.injector = injector

    def replace(self, transport):
        """None se o servidor está fechando (um pedido que chegou junto com o Ctrl+C)."""
        with self.lock:
            if not self.running:
                transport.close()
                return None
            old = self.current
            if old is not None:
                old[0].close()
            session = Session(transport, self.source, self.args, self.injector, self.audio)
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
            except ConnectionResetError:
                # Windows: um ICMP "porta inalcançável" de um envio anterior (o PSP saiu) chega
                # aqui como erro do recvfrom; o socket continua bom.
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
                cur = self.replace(UdpTransport(sock, addr, self.args.udp_pace, self.args.hdr_cache,
                                                 self.args.p_redundancy_ms / 1000))
                if cur is None:
                    return
            try:
                cur.transport.feed(req, nack)
            except Exception:  # um datagrama ruim não pode derrubar a thread do UDP
                log.exception(tr("error handling a UDP request"))

    def close(self):
        with self.lock:
            self.running = False
            if self.current is not None:
                self.current[0].close()


def parse_size(text: str):
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(tr("use WIDTHxHEIGHT, e.g. 480x272")) from None
    if not (16 <= w <= 480 and 16 <= h <= 272):
        raise argparse.ArgumentTypeError(tr("the PSP shows at most 480x272"))
    return w, h


def parse_pin(text: str) -> str:
    if not text.isdigit() or len(text) > 16:
        raise argparse.ArgumentTypeError(tr("the PIN is digits only"))
    return text


def parse_lang(text: str) -> str:
    try:
        return i18n.normalize(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def linux_only(text: str) -> str:
    """Ajuda de uma opção que só faz sentido no Linux: no Windows, some do --help."""
    return argparse.SUPPRESS if WINDOWS else text


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=tr("PSPStream: streams the PC screen to the PSP (H.264 or MJPEG)."))
    p.add_argument("--version", action="version", version=f"PSPStream {VERSION}")
    p.add_argument("--lang", type=parse_lang, choices=i18n.LANGS, default=i18n.from_argv([]),
                   help=tr("language of the messages and of the web interface: en (default) or pt. Default also in "
                           "PSPSTREAM_LANG"))
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT,
                   help=tr("TCP and UDP port (default %(default)s)"))
    p.add_argument("--codec", choices=["auto", "jpeg", "h264", "h264p"], default="auto",
                   help=tr("h264p: H.264 with P frames, ~10x fewer bytes per frame (EBOOT v0.9+; an old EBOOT gets "
                           "every frame as IDR). h264: every frame IDR (EBOOT v0.5+). auto (default) = h264p if "
                           "openh264 is installed, otherwise jpeg"))
    p.add_argument("--h264-encoder", choices=["auto", "openh264", "gstreamer"], default="auto",
                   help=tr("P frames and still image: auto (default) = libopenh264 directly, with GStreamer's "
                           "openh264enc as a fallback; openh264 or gstreamer force one of them"))
    p.add_argument("--udp-pace", type=float, default=0, metavar="KB/s",
                   help=tr("UDP: limit the rate the chunks are sent at (0 = no limit, default)"))
    p.add_argument("--p-redundancy-ms", type=float, default=transports.REDUNDANCY_S * 1000, metavar="MS",
                   help=tr("P frames over UDP: the last chunk of each frame is sent again after MS ms, and losing it "
                           "does not stall the stream waiting for the NACK (default %(default).0f; 0 = off)"))
    p.add_argument("--no-hdr-cache", dest="hdr_cache", action="store_false",
                   help=tr("UDP: send the JPEG header with every frame (to compare; the default sends it only when "
                           "it changes)"))
    p.add_argument("--dscp", choices=list(DSCP), default="ef",
                   help=tr("marking of the server's packets for the Wi-Fi priority queue (WMM): ef = voice "
                           "(default), cs5/af41 = video, 0 = none"))
    p.add_argument("--bind", default="0.0.0.0", help=tr("local address (default %(default)s)"))
    if WINDOWS:
        p.add_argument("--source", choices=["screen", "test", "gst", "static"], default="screen",
                       help=tr("screen = the monitor through Desktop Duplication (default); test = animated "
                               "pattern; gst = your own GStreamer elements (--gst-src); static = an image"))
    else:
        p.add_argument("--source", choices=["portal", "kms", "test", "x11", "gst", "static", "wolf"], default="portal",
                       help=tr("portal = screen on Wayland (default); kms = straight from the graphics card, without "
                               "GNOME 50's ~40 fps limit (the helper needs permission to read the screen: --setup "
                               "gives it); test = animated pattern with a clock; x11 = X11 session; gst = your own "
                               "pipeline (--gst-src); static = an image; wolf = what runs in Wolf (Games on Whales), "
                               "through its API (PSPStream wiki, Wolf page)"))
    p.add_argument("--monitor", type=int, default=0, metavar="N",
                   help=tr("screen: which monitor (0 = the main one)") if WINDOWS else argparse.SUPPRESS)
    p.add_argument("--kms-monitor", type=int, default=0, metavar="N",
                   help=linux_only(tr("kms: which connected monitor (0 = the first; the log shows how many there are)")))
    p.add_argument("--kms-card", metavar="/dev/dri/cardN",
                   help=linux_only(tr("kms: graphics card (default: searches all of them)")))
    testcard = here.parent / "assets" / "testcard.jpg"
    if not testcard.exists():  # pspstream.exe (PyInstaller): os arquivos ficam ao lado dos módulos
        testcard = here / "assets" / "testcard.jpg"
    p.add_argument("--image", default=str(testcard),
                   help=tr("image for the static mode (default: assets/testcard.jpg)"))
    p.add_argument("--gst-src", help=tr("GStreamer elements of the source for --source gst"))
    p.add_argument("--wolf-socket", default=wolf_api.default_socket(), metavar=tr("PATH"),
                   help=linux_only(tr("wolf: the Wolf API socket (default: WOLF_SOCKET_PATH or %(default)s). It "
                                      "gives full control of Wolf: mount it only in the PSPStream container and never "
                                      "expose it over TCP")))
    p.add_argument("--wolf-target", default=os.environ.get("PSPSTREAM_WOLF_TARGET", ""), metavar="ID",
                   help=linux_only(tr("wolf: what to mirror: lobby id or name, or session id (default: the only "
                                      "open lobby; with several, the log lists the options). Default also in "
                                      "PSPSTREAM_WOLF_TARGET")))
    p.add_argument("--wolf-video-convert", default=os.environ.get("PSPSTREAM_VIDEO_CONVERT") or "auto",
                   metavar=tr("auto|nvidia|va|cpu|ELEMENTS"),
                   help=linux_only(tr("wolf: how Wolf brings the image down to regular memory at 480x272. nvidia = "
                                      "CUDA (Wolf's default with NVIDIA); va = Intel/AMD; cpu = Wolf with "
                                      "WOLF_USE_ZERO_COPY=FALSE; auto (default) tries them in that order. Or "
                                      "GStreamer elements that deliver I420 at the sent resolution. Default also in "
                                      "PSPSTREAM_VIDEO_CONVERT")))
    p.add_argument("--wolf-pin", type=parse_pin, metavar=tr("DIGITS"),
                   default=os.environ.get("PSPSTREAM_WOLF_PIN") or None,
                   help=linux_only(tr("wolf: the lobby's PIN, if it asks for one (for the controls to join the "
                                      "lobby). Default: PSPSTREAM_WOLF_PIN")))
    p.add_argument("--wolf-rtp-port", type=int,
                   default=wolf_api.env_port("WOLF_VIDEO_PING_PORT", wolf_api.VIDEO_PING_PORT), metavar=tr("PORT"),
                   help=linux_only(tr("wolf: Wolf's UDP port for the video ping (default: WOLF_VIDEO_PING_PORT or "
                                      "%(default)s)")))
    p.add_argument("--wolf-audio-rtp-port", type=int,
                   default=wolf_api.env_port("WOLF_AUDIO_PING_PORT", wolf_api.AUDIO_PING_PORT), metavar=tr("PORT"),
                   help=linux_only(tr("wolf: Wolf's UDP port for the audio ping (default: WOLF_AUDIO_PING_PORT or "
                                      "%(default)s)")))
    p.add_argument("--size", type=parse_size, default=(480, 272), help=tr("sent resolution (default 480x272)"))
    p.add_argument("--fps", type=int, default=60,
                   help=tr("maximum capture rate (default %(default)s). Capturing above what the PSP shows makes "
                           "the sent frame younger"))
    p.add_argument("-q", "--quality", type=int, default=60,
                   help=tr("JPEG quality 1-100: starting (adaptive) or fixed (--fixed-quality). Default %(default)s"))
    p.add_argument("--fixed-quality", dest="adaptive", action="store_false",
                   help=tr("do not adapt the quality to the measured bandwidth"))
    p.add_argument("--target-fps", type=float, default=20,
                   help=tr("adaptive: FPS the bandwidth must sustain (default %(default)s, measured on the PSP-3000: "
                           "~q55 at ~48 ms; 30 is out of reach on 802.11b and drops the quality to the minimum). "
                           "Lower = more quality and more latency per frame"))
    p.add_argument("--q-min", type=int, default=25, help=tr("adaptive: minimum quality (default %(default)s)"))
    p.add_argument("--q-max", type=int, default=90, help=tr("adaptive: maximum quality (default %(default)s)"))
    p.add_argument("--scale", default="bilinear2",
                   choices=["nearest-neighbour", "bilinear", "bilinear2", "lanczos", "mitchell", "catrom"],
                   help=tr("scaling filter. bilinear2 (default) does not alias and makes frames ~27%% smaller than "
                           "bilinear; lanczos = slightly sharper text, ~2 ms more"))
    p.add_argument("--stretch", action="store_true", help=tr("stretch instead of keeping the aspect ratio"))
    p.add_argument("--window", action="store_true",
                   help=linux_only(tr("portal: choose a window instead of a monitor")))
    p.add_argument("--dmabuf", action="store_true",
                   help=linux_only(tr("portal, experimental: receive the screen in GPU memory (DMA-BUF) and scale it "
                                      "to 480x272 in OpenGL; only the small image comes to the CPU. If it does not "
                                      "work, it falls back to the normal mode on its own")))
    p.add_argument("--no-cursor", action="store_true",
                   help=tr("screen: do not draw the cursor") if WINDOWS else tr("portal: do not draw the cursor"))
    p.add_argument("--forget", action="store_true",
                   help=linux_only(tr("portal: do not reuse/save the screen choice")))
    p.add_argument("--no-audio", action="store_true", help=tr("do not capture or send the audio"))
    p.add_argument("--audio-device", default="monitor", metavar=tr("NAME"),
                   help=tr("audio: monitor (default) = what plays on the speakers (WASAPI loopback); test = 440 Hz "
                           "tone") if WINDOWS else
                   tr("audio: PipeWire/PulseAudio source (pactl list short sources); monitor (default) = what "
                      "plays on the speakers (with --source wolf, the target's audio in Wolf); wolf = Wolf's "
                      "audio; test = 440 Hz tone"))
    p.add_argument("--audio-rate", type=int, default=44100, choices=[22050, 32000, 44100, 48000],
                   help=tr("audio: rate (default %(default)s Hz, the PSP's; stereo IMA ADPCM ~ rate/1000 KB/s)"))
    p.add_argument("--audio-mono", action="store_true", help=tr("audio: mono (half the bytes)"))
    p.add_argument("--no-input", action="store_true", help=tr("do not inject the PSP controls on the PC"))
    p.add_argument("--input-dry-run", action="store_true",
                   help=tr("only show in the log the keys/movements that would be injected"))
    p.add_argument("--keymap", default=str(here / "keymap.json"), help=tr("mapping file (default keymap.json)"))
    p.add_argument("--profile", default=os.environ.get("PSPSTREAM_PROFILE") or "game",
                   help=tr("keymap profile: game, desktop, arrows (keyboard and mouse); xbox, xbox-camera, "
                           "xbox-shoulders (virtual Xbox 360 controller; with --source wolf, only these). The old "
                           "names jogo, setas and xbox-ombros still work. Default %(default)s"))
    p.add_argument("--mouse-speed", type=float, default=1.0, help=tr("multiplies the profile's mouse speed"))
    p.add_argument("--input-timeout", type=float, default=0.5, metavar="S",
                   help=tr("releases every key if the PSP sends nothing for S seconds while something is held "
                           "(default %(default)s; the PSP restates the state every ~100 ms)"))
    p.add_argument("--stats-interval", type=float, default=2.0, help=tr("seconds between statistics lines"))
    p.add_argument("--bench", metavar="Q1,Q2,...", nargs="?", const="30,50,70,90",
                   help=tr("benchmark: when the PSP connects, runs each quality for --bench-seconds and saves a "
                           "table to bench_*.md (default 30,50,70,90)"))
    p.add_argument("--bench-seconds", type=float, default=10)
    p.add_argument("--web", default=os.environ.get("PSPSTREAM_WEB") or WEB_DEFAULT, metavar=tr("HOST:PORT"),
                   help=tr("settings web interface (default %(default)s, this PC only; 0.0.0.0:5124 opens it to the "
                           "local network). The password comes from the PSPSTREAM_WEB_PASSWORD variable (without it, "
                           "anyone who reaches the port changes the settings). Default also in PSPSTREAM_WEB"))
    p.add_argument("--web-allow-host", action="append", metavar=tr("NAME"),
                   default=[h for h in os.environ.get("PSPSTREAM_WEB_HOSTS", "").replace(",", " ").split() if h],
                   help=tr("name accepted in the web interface address, besides localhost, the PC's name and IPs "
                           "(e.g. one from the router's DNS); can repeat. Default: PSPSTREAM_WEB_HOSTS, comma "
                           "separated"))
    p.add_argument("--no-web", action="store_true", help=tr("no web interface"))
    p.add_argument("--config", default=str(settings.default_path()), metavar=tr("FILE"),
                   help=tr("settings saved by the web interface (default %(default)s). Command-line options win over "
                           "the file"))
    p.add_argument("--check", action="store_true",
                   help=tr("check this machine (GStreamer, openh264, firewall) without starting the server")
                   if WINDOWS else
                   tr("check this machine's dependencies and show the command to install what is missing (apt, "
                      "dnf, pacman or zypper), without starting the server"))
    p.add_argument("--setup", action="store_true",
                   help=tr("prepare this machine: firewall rule and the openh264 library, asking before each step")
                   if WINDOWS else
                   tr("prepare this machine: installs what --check points out (packages, uinput, firewall, KMS "
                      "capture), showing each command and asking before"))
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def parse_args(argv=None):
    return build_parser().parse_args(argv)


def load_config(parser, args, argv):
    """Junta o arquivo de configuração aos args: padrão < arquivo < linha de comando.
    Devolve (store, opções dadas na linha de comando, chaves que vieram do arquivo)."""
    explicit = settings.explicit_dests(parser, argv)
    defaults = parser.parse_args([])
    defaults.codec_choice = defaults.codec
    store = settings.ConfigStore(args.config, {s.key: settings.arg_value(s, defaults) for s in settings.SETTINGS})
    from_file, overridden = [], []
    for key, value in store.load().items():
        setting = settings.BY_KEY[key]
        if settings.cli_dest(setting) in explicit:
            overridden.append(setting.flag)
            continue
        settings.set_arg(setting, args, value)
        from_file.append(key)
    if "codec" in from_file:
        args.codec = args.codec_choice
    if from_file:
        log.info(tr("settings: %s (%s)"), store.path, ", ".join(f"{k} = {store.values[k]}" for k in from_file))
    if overridden:
        log.info(tr("settings: the command line wins over the file for %s"), ", ".join(overridden))
    return store, explicit, from_file


def _terminate(signum, frame):
    raise KeyboardInterrupt


def no_udp_connreset(sock: socket.socket) -> None:
    """Windows: sem isto, o ICMP "porta inalcançável" de um envio anterior vira erro no recvfrom
    seguinte (SIO_UDP_CONNRESET; o socket.ioctl do Python não tem essa opção)."""
    if not WINDOWS:
        return
    try:
        import ctypes
        ws2 = ctypes.WinDLL("ws2_32")
        ws2.WSAIoctl.argtypes = [ctypes.c_size_t, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                 ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p, ctypes.c_void_p]
        off, ret = ctypes.c_int(0), ctypes.c_ulong(0)
        ws2.WSAIoctl(sock.fileno(), 0x9800000C, ctypes.byref(off), 4, None, 0, ctypes.byref(ret), None, None)
    except (OSError, AttributeError):
        pass


def utf8_console() -> None:
    """Windows: o log com acentos não pode derrubar o servidor num console cp1252/cp850."""
    if not WINDOWS:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    utf8_console()
    i18n.set_language(i18n.from_argv(argv))  # o --help já sai no idioma pedido
    parser = build_parser()
    args = parser.parse_args(argv)
    i18n.set_language(args.lang)
    if args.check or args.setup:
        if WINDOWS:
            import win_doctor as doctor
        else:
            import doctor
        return (doctor.setup if args.setup else doctor.main)(args.port, VERSION)
    # A thread de envio acorda mais rápido quando outra thread Python tem o GIL.
    sys.setswitchinterval(0.001)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from web import LogRing
    ring = LogRing()
    ring.setLevel(logging.INFO)
    logging.getLogger().addHandler(ring)
    args.codec_choice = args.codec
    store, explicit, from_file = load_config(parser, args, argv)
    i18n.set_language(args.lang)  # o server.json pode ter outro
    from inject import canonical_profile
    args.profile = canonical_profile(args.profile)  # nomes antigos (jogo, setas, xbox-ombros)

    capture_keys = [k for k in from_file if settings.BY_KEY[k].apply == "capture"]
    err = resolve_codec(args)
    if err and "codec" in from_file:
        log.warning(tr("%s; the codec from the settings file was ignored (change it in the web interface)"), err)
        args.codec = args.codec_choice = parser.get_default("codec")
        err = resolve_codec(args)
    if err:
        log.error("%s", err)
        return 1
    if args.dmabuf and args.source != "portal":
        log.warning(tr("--dmabuf only works with --source portal; ignored"))
        args.dmabuf = False
    try:
        source = start_source(args)
    except Exception as exc:  # erros de portal/GStreamer: mensagem curta, sem traceback
        if args.verbose and not capture_keys:
            raise
        if not capture_keys:
            log.error(tr("could not start the capture: %s"), exc)
            return 1
        # A captura escolhida na interface web não subiu (ex.: KMS sem o
        # auxiliar): volta para a da linha de comando, e a interface continua
        # acessível para trocar de novo.
        log.warning(tr("the capture from the settings file did not start (%s); using the default one"), exc)
        for key in capture_keys:
            setting = settings.BY_KEY[key]
            if key != "codec":  # o codec já foi conferido acima
                setattr(args, setting.dest, parser.get_default(setting.dest))
        try:
            source = start_source(args)
        except Exception as exc2:
            log.error(tr("could not start the capture: %s"), exc2)
            return 1

    injector, input_note = None, N_("off (--no-input)")  # o status traduz na hora
    if not args.no_input:
        try:
            injector, input_note = open_injector(args)
        except RuntimeError as exc:
            input_note = tr("disabled: {error}").format(error=exc)
            log.warning(tr("controls disabled: %s"), exc)

    audio, audio_note = None, N_("off (--no-audio)")
    if not args.no_audio:
        audio = start_audio(args)
        if audio is None:
            audio_note = N_("the capture did not open (see the log)")

    srv = socket.create_server((args.bind, args.port))
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    no_udp_connreset(udp)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    set_dscp(udp, args.dscp)
    udp.bind((args.bind, args.port))
    server = Server(source, args, injector, audio)
    threading.Thread(target=server.serve_udp, args=(udp,), name="udp", daemon=True).start()
    log.info(tr("PSPStream %s: waiting for the PSP at %s:%d, TCP and UDP (on the PSP: 'Find the PC on the network', "
                "or this IP in server.txt)"), VERSION, local_ip(), args.port)

    from control import Controller
    ctl = Controller(args, store, server, udp, explicit, VERSION, local_ip, input_note, audio_note)
    web = None
    if not args.no_web:
        from web import WebServer, parse_addr
        try:
            host, port = parse_addr(args.web)
            web = WebServer(ctl, host, port, ring, os.environ.get("PSPSTREAM_WEB_PASSWORD") or None,
                            args.web_allow_host)
            web.start()
            ctl.web_url = web.url
            access = tr("with a password") if web.password else tr("without a password")
            log.info(tr("settings: %s%s"), web.url,
                     tr(" (open to the local network, {access})").format(access=access) if web.public else
                     (tr(" ({access}; allowed names: {names})").format(access=access,
                                                                      names=", ".join(args.web_allow_host))
                      if args.web_allow_host else ""))
        except (OSError, ValueError) as exc:
            log.warning(tr("web interface disabled (%s): %s"), args.web, exc)
            web = None

    from netcheck import check_pc_wifi
    check_pc_wifi(local_ip())
    srv.settimeout(0.5)
    if threading.current_thread() is threading.main_thread():
        # docker stop e systemctl stop (SIGTERM) encerram como o Ctrl+C: a captura para direito
        # (a fonte do Wolf encerra a sessão dela no Wolf).
        signal.signal(signal.SIGTERM, _terminate)
    try:
        while True:
            failed = getattr(server.source, "failed", None)  # a interface web pode trocar a captura
            if failed:
                log.error(tr("capture stopped: %s"), failed)
                return 1
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            set_dscp(conn, args.dscp)
            server.replace(TcpTransport(conn, addr))
    except KeyboardInterrupt:
        log.info(tr("shutting down"))
    finally:
        if web is not None:
            web.close()
        server.close()
        if server.injector:
            server.injector.close()
        if server.audio is not None:
            server.audio.stop()
        server.source.stop()
        srv.close()
        udp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
