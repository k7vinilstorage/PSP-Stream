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
from capture import build_source, open_injector, resolve_codec, start_audio, start_source  # noqa: F401
from netcheck import local_ip
from protocol import REQ_FRAME, REQ_HELLO, Request
import settings
from stats import SessionStats, Window, format_summary, now_ms
import transports
from transports import DSCP, TcpTransport, UdpTransport, parse_datagram, set_dscp

log = logging.getLogger("pspstream")

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
        log.info("PSP conectado via %s: %s:%d", self.transport.name.upper(), *self.transport.addr)
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
                log.info("envio falhou: %s", exc)
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
            log.info("PSP desconectado (%d frames, %.1f MB enviados%s)",
                     self.stats.total_frames, self.stats.total_bytes / 1e6,
                     f", {saved / 1024:.0f} KB de cabeçalho JPEG economizados" if saved else "")

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
        self.p_capable = bool(req.wflags & protocol.CAP_H264P)
        self._audio_wanted(bool(req.wflags & protocol.CAP_AUDIO))
        if req.flags & (protocol.REQ_IDR | REQ_HELLO):
            self.idr_wanted = True
        if self.args.codec in ("h264", "h264p") and not req.wflags & protocol.CAP_H264 and not self.h264_warned:
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
                log.info("som: desligado no PSP")
        elif self.audio is None:
            log.info("som: o PSP pediu, mas o servidor está sem som (--no-audio, ou a captura não abriu)")
        else:
            log.info("som: ligado no PSP (%d Hz, %s, ~%.0f KB/s)", self.audio.rate,
                     "estéreo" if self.audio.channels == 2 else "mono", self.audio.kbps)

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
            log.info("Wi-Fi do PSP: sinal %d%%, economia de energia WLAN %s", signal,
                     "LIGADA" if power_save else "desligada")
            if power_save:
                log.warning("a economia de energia WLAN do PSP segura os pacotes no roteador e aumenta "
                            "muito a latência: desligue em Ajustes > Ajustes de economia de energia")

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
                log.warning("frame de %d KB excede o limite de %d KB; descartado",
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
                    log.info("H.264: frames P (IDR só quando o PSP pede; a qualidade muda sem IDR)")
                else:
                    log.info("H.264: frames P (IDR só quando o PSP pede; qualidade nova no máximo a cada %.0f s)",
                             self.encoder.qp_change_min_s)
            else:
                self.encoder = h264.H264Encoder(w, h, quality)
                log.warning("o EBOOT do PSP não aceita frames P (anterior à v0.9, ou h264p=0 no server.txt): "
                            "mandando todo frame IDR")
            self.idr_wanted = False  # encoder novo: o primeiro frame já é IDR
        self.encoder.set_quality(quality)
        if self.encoder_p and self.idr_wanted:
            self.idr_wanted = False
            if self.encoder.request_idr():
                log.debug("IDR pedido pelo PSP")
        return self.encoder.encode(i420)

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


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="PSPStream: transmite a tela do PC para o PSP (H.264 ou MJPEG).")
    p.add_argument("--version", action="version", version=f"PSPStream {VERSION}")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT,
                   help="porta TCP e UDP (padrão %(default)s)")
    p.add_argument("--codec", choices=["auto", "jpeg", "h264", "h264p"], default="auto",
                   help="h264p: H.264 com frames P, ~10x menos bytes por frame (EBOOT v0.9+; um EBOOT antigo "
                        "recebe todo frame IDR). h264: todo frame IDR (EBOOT v0.5+). auto (padrão) = h264p se "
                        "o openh264 estiver instalado, senão jpeg")
    p.add_argument("--h264-encoder", choices=["auto", "openh264", "gstreamer"], default="auto",
                   help="frames P e imagem estática: auto (padrão) = libopenh264 direto, com o openh264enc do "
                        "GStreamer de reserva; openh264 ou gstreamer forçam um dos dois")
    p.add_argument("--udp-pace", type=float, default=0, metavar="KB/s",
                   help="UDP: limitar a taxa de envio dos pedaços (0 = sem limite, padrão)")
    p.add_argument("--p-redundancy-ms", type=float, default=transports.REDUNDANCY_S * 1000, metavar="MS",
                   help="frames P por UDP: o último pedaço de cada frame vai de novo depois de MS ms, e a perda "
                        "dele não para o stream esperando o NACK (padrão %(default).0f; 0 = desliga)")
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
    p.add_argument("--no-audio", action="store_true", help="não capturar nem mandar o som")
    p.add_argument("--audio-device", default="monitor", metavar="NOME",
                   help="som: fonte do PipeWire/PulseAudio (pactl list short sources); monitor (padrão) = o que "
                        "sai nas caixas; test = tom de 440 Hz")
    p.add_argument("--audio-rate", type=int, default=44100, choices=[22050, 32000, 44100, 48000],
                   help="som: taxa (padrão %(default)s Hz, a do PSP; IMA ADPCM estéreo ~ taxa/1000 KB/s)")
    p.add_argument("--audio-mono", action="store_true", help="som: mono (metade dos bytes)")
    p.add_argument("--no-input", action="store_true", help="não injetar os controles do PSP no PC")
    p.add_argument("--input-dry-run", action="store_true",
                   help="só mostrar no log as teclas/movimentos que seriam injetados")
    p.add_argument("--keymap", default=str(here / "keymap.json"), help="arquivo de mapeamento (padrão keymap.json)")
    p.add_argument("--profile", default="jogo",
                   help="perfil do keymap: jogo, desktop, setas (teclado e mouse); xbox, xbox-camera, "
                        "xbox-ombros (controle de Xbox 360 virtual). Padrão %(default)s")
    p.add_argument("--mouse-speed", type=float, default=1.0, help="multiplica a velocidade do mouse do perfil")
    p.add_argument("--input-timeout", type=float, default=0.5, metavar="S",
                   help="solta todas as teclas se o PSP ficar S segundos sem mandar nada enquanto algo está "
                        "segurado (padrão %(default)s; o PSP reafirma o estado a cada ~100 ms)")
    p.add_argument("--stats-interval", type=float, default=2.0, help="segundos entre linhas de estatística")
    p.add_argument("--bench", metavar="Q1,Q2,...", nargs="?", const="30,50,70,90",
                   help="benchmark: quando o PSP conectar, roda cada qualidade por --bench-seconds e salva "
                        "uma tabela em bench_*.md (padrão 30,50,70,90)")
    p.add_argument("--bench-seconds", type=float, default=10)
    p.add_argument("--web", default=WEB_DEFAULT, metavar="HOST:PORTA",
                   help="interface web das configurações (padrão %(default)s, só neste PC; 0.0.0.0:5124 abre "
                        "para a rede local, sem senha)")
    p.add_argument("--no-web", action="store_true", help="sem a interface web")
    p.add_argument("--config", default=str(settings.default_path()), metavar="ARQUIVO",
                   help="configurações gravadas pela interface web (padrão %(default)s). As opções da linha de "
                        "comando valem mais que o arquivo")
    p.add_argument("--check", action="store_true",
                   help="conferir as dependências desta máquina e mostrar o comando para instalar o que falta "
                        "(apt, dnf, pacman ou zypper), sem iniciar o servidor")
    p.add_argument("--setup", action="store_true",
                   help="preparar esta máquina: instala o que o --check aponta (pacotes, uinput, firewall, "
                        "captura KMS), mostrando cada comando e pedindo confirmação antes")
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
        log.info("configuração: %s (%s)", store.path, ", ".join(f"{k} = {store.values[k]}" for k in from_file))
    if overridden:
        log.info("configuração: a linha de comando vale mais que o arquivo para %s", ", ".join(overridden))
    return store, explicit, from_file


def main(argv=None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(argv)
    if args.check or args.setup:
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

    capture_keys = [k for k in from_file if settings.BY_KEY[k].apply == "capture"]
    err = resolve_codec(args)
    if err and "codec" in from_file:
        log.warning("%s; o codec do arquivo de configuração foi ignorado (mude na interface web)", err)
        args.codec = args.codec_choice = parser.get_default("codec")
        err = resolve_codec(args)
    if err:
        log.error("%s", err)
        return 1
    if args.dmabuf and args.source != "portal":
        log.warning("--dmabuf só vale para --source portal; ignorado")
        args.dmabuf = False
    try:
        source = start_source(args)
    except Exception as exc:  # erros de portal/GStreamer: mensagem curta, sem traceback
        if args.verbose and not capture_keys:
            raise
        if not capture_keys:
            log.error("não foi possível iniciar a captura: %s", exc)
            return 1
        # A captura escolhida na interface web não subiu (ex.: KMS sem o
        # auxiliar): volta para a da linha de comando, e a interface continua
        # acessível para trocar de novo.
        log.warning("a captura do arquivo de configuração não subiu (%s); usando a padrão", exc)
        for key in capture_keys:
            setting = settings.BY_KEY[key]
            if key != "codec":  # o codec já foi conferido acima
                setattr(args, setting.dest, parser.get_default(setting.dest))
        try:
            source = start_source(args)
        except Exception as exc2:
            log.error("não foi possível iniciar a captura: %s", exc2)
            return 1

    injector, input_note = None, "desligados (--no-input)"
    if not args.no_input:
        try:
            injector, input_note = open_injector(args)
        except RuntimeError as exc:
            input_note = f"desativados: {exc}"
            log.warning("controles desativados: %s", exc)

    audio, audio_note = None, "desligado (--no-audio)"
    if not args.no_audio:
        audio = start_audio(args)
        if audio is None:
            audio_note = "a captura não abriu (veja o log)"

    srv = socket.create_server((args.bind, args.port))
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    set_dscp(udp, args.dscp)
    udp.bind((args.bind, args.port))
    server = Server(source, args, injector, audio)
    threading.Thread(target=server.serve_udp, args=(udp,), name="udp", daemon=True).start()
    log.info("PSPStream %s: aguardando o PSP em %s:%d, TCP e UDP (no PSP: 'Procurar o PC na rede', ou este IP "
             "no server.txt)", VERSION, local_ip(), args.port)

    from control import Controller
    ctl = Controller(args, store, server, udp, explicit, VERSION, local_ip, input_note, audio_note)
    web = None
    if not args.no_web:
        from web import WebServer, parse_addr
        try:
            host, port = parse_addr(args.web)
            web = WebServer(ctl, host, port, ring)
            web.start()
            ctl.web_url = web.url
            log.info("configurações: %s%s", web.url,
                     " (aberto para a rede local, sem senha)" if web.public else "")
        except (OSError, ValueError) as exc:
            log.warning("interface web desativada (%s): %s", args.web, exc)
            web = None

    from netcheck import check_pc_wifi
    check_pc_wifi(local_ip())
    srv.settimeout(0.5)
    try:
        while True:
            failed = getattr(server.source, "failed", None)  # a interface web pode trocar a captura
            if failed:
                log.error("captura parou: %s", failed)
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
