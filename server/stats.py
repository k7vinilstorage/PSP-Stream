"""Estatísticas da sessão: FPS, KB/frame, vazão do Wi-Fi e latência."""
import logging
import statistics
import time

import protocol
from i18n import N_, tr

log = logging.getLogger("pspstream.stats")

# Engasgo: 50 ms ou mais entre dois frames enviados (3 frames a 60 fps). A
# causa provável sai do que o servidor viu no intervalo; o PSP só pede o
# próximo frame depois de receber o atual, então uma espera no PSP também
# aparece aqui.
HITCH_MS = 50
# Nomes internos (status da interface web); o texto mostrado passa por tr().
HITCH_CAUSES = ("loss", "IDR", "late request", "capture")
HITCH_TEXT = {"loss": N_("loss"), "IDR": "IDR", "late request": N_("late request"), "capture": N_("capture")}


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def _avg(values):
    return sum(values) / len(values) if values else 0.0


def _p95(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


class Window:
    """Acumuladores de um intervalo (linha de log ou fase do benchmark)."""

    def __init__(self, source=None, transport=None):
        self.t0 = time.monotonic()
        self.source = source
        self.transport = transport
        self.seq0 = source.latest()[0] if source is not None else 0
        self.chunks0 = (transport.sent_chunks, transport.resent_chunks) if transport is not None else (0, 0)
        self.frames = 0
        self.bytes = 0
        self.keepalive = 0  # reenvios do último frame por falta de frame novo (fora da latência)
        self.latency = []  # captura no PC -> PSP exibido (ms)
        self.capture = []  # captura -> JPEG pronto, no GStreamer (ms)
        self.age = []      # frame pronto -> enviado (ms)
        self.wait = []     # pedido chegou -> frame enviado (ms): espera por frame novo
        self.net = []      # pedido -> frame recebido, medido no PSP (ms)
        self.transfer = [] # net - espera: tempo de rede de fato (ms)
        self.rate = []     # KB/s estimados por frame (tamanho / transferência)
        self.local = []    # recebido -> exibido, no PSP (ms)
        self.decode = []   # só decode (ms)
        self.first = []    # pedido -> primeiro pedaço, no PSP (ms): ida e volta
        self.burst = []    # primeiro -> último pedaço (ms)
        self.burst_rate = []  # KB/s dentro da rajada: vazão real do enlace
        self.ping = []     # ping durante o stream, informado pelo PSP (ms)
        self.ping_min = []
        self.idle = []     # fim do frame anterior -> 1º pedaço deste, no PSP (ms; < 0 = chegou em fila)
        self.early = []    # bytes que faltavam quando o PSP pediu o próximo (0 = só no fim)
        self.lost0 = None  # contador de frames perdidos do PSP no início da janela
        self.lost1 = 0
        self.audio_bytes = 0  # pacotes de som enviados
        self.hitches = []  # (intervalo ms, causa) dos engasgos
        self.idrs = 0      # frames IDR enviados no modo P (pedidos pelo PSP ou encoder refeito)

    def summary(self, quality=None) -> dict:
        elapsed = max(1e-6, time.monotonic() - self.t0)
        source_fps = None  # fonte estática: não se aplica
        if self.source is not None and not self.source.repeat:
            source_fps = (self.source.latest()[0] - self.seq0) / elapsed
        resent_pct = 0.0
        if self.transport is not None:
            sent = self.transport.sent_chunks - self.chunks0[0]
            resent = self.transport.resent_chunks - self.chunks0[1]
            resent_pct = 100.0 * resent / sent if sent else 0.0
        return {
            "fps": self.frames / elapsed,
            "source_fps": source_fps,
            "keepalive": self.keepalive,
            "resent_pct": resent_pct,
            "kb_per_frame": self.bytes / self.frames / 1024 if self.frames else 0.0,
            "send_kbps": self.bytes / elapsed / 1024,
            "wifi_kbps": statistics.median(self.rate) if self.rate else 0.0,
            "latency_ms": _avg(self.latency),
            "latency_p95_ms": _p95(self.latency),
            "capture_ms": _avg(self.capture),
            "age_ms": _avg(self.age),
            "wait_ms": _avg(self.wait),
            "transfer_ms": _avg(self.transfer),
            "local_ms": _avg(self.local),
            "decode_ms": _avg(self.decode),
            "first_ms": _avg(self.first),
            "first_min_ms": min(self.first) if self.first else 0.0,
            "first_med_ms": statistics.median(self.first) if self.first else 0.0,
            "burst_ms": _avg(self.burst),
            "burst_kbps": statistics.median(self.burst_rate) if self.burst_rate else 0.0,
            "ping_ms": _avg(self.ping),
            "ping_min_ms": min(self.ping_min) if self.ping_min else 0.0,
            "idle_ms": statistics.median(self.idle) if self.idle else None,
            "early_kb": statistics.median(self.early) / 1024 if self.early else 0.0,
            "lost": (self.lost1 - self.lost0) if self.lost0 is not None else 0,
            "quality": quality,
            "frames": self.frames,
            "hitches": len(self.hitches),
            "hitch_max_ms": max((g for g, _ in self.hitches), default=0.0),
            "hitch_causes": {c: n for c in HITCH_CAUSES if (n := sum(1 for _, k in self.hitches if k == c))},
            "idrs": self.idrs,
            "audio_kbps": self.audio_bytes / elapsed / 1024,
        }


def format_summary(s: dict) -> str:
    source = tr(" (source {fps:4.1f})").format(fps=s["source_fps"]) if s["source_fps"] is not None else ""
    line = tr("{fps:5.1f} fps{source} | {kb:5.1f} KB/frame | Wi-Fi {wifi:5.0f} KB/s | "
              "latency {latency:5.1f} ms (p95 {p95:5.1f}) ~ capture {capture:4.1f} + age {age:4.1f} + "
              "network {network:5.1f} + psp {psp:5.1f} | network = 1st chunk {first:4.1f} + burst {burst:4.1f} ms "
              "({burst_kbps:4.0f} KB/s) | decode {decode:4.1f} ms | wait for a new frame {wait:4.1f} ms").format(
        fps=s["fps"], source=source, kb=s["kb_per_frame"], wifi=s["wifi_kbps"], latency=s["latency_ms"],
        p95=s["latency_p95_ms"], capture=s["capture_ms"], age=s["age_ms"], network=s["transfer_ms"],
        psp=s["local_ms"], first=s["first_ms"], burst=s["burst_ms"], burst_kbps=s["burst_kbps"],
        decode=s["decode_ms"], wait=s["wait_ms"])
    if s["idle_ms"] is not None:
        line += tr(" | dead time {ms:+5.1f} ms").format(ms=s["idle_ms"])
        if s["early_kb"]:
            line += tr(" (early by {kb:.1f} KB)").format(kb=s["early_kb"])
    if s["ping_ms"]:
        line += tr(" | in-stream ping {ms:4.1f} ms (min {min:4.1f})").format(ms=s["ping_ms"], min=s["ping_min_ms"])
    if s["quality"] is not None:
        line += f" | q {s['quality']}"
    if s["keepalive"]:
        line += tr(" | {n} resends (still screen)").format(n=s["keepalive"])
    if s["resent_pct"]:
        line += tr(" | {pct:.1f}% UDP chunks resent").format(pct=s["resent_pct"])
    if s["lost"]:
        line += tr(" | {n} frames lost").format(n=s["lost"])
    if s.get("audio_kbps"):
        line += tr(" | audio {kbps:.0f} KB/s").format(kbps=s["audio_kbps"])
    if s.get("idrs"):
        line += f" | {s['idrs']} IDR"
    if s.get("hitches"):
        causes = ", ".join(f"{n} {tr(HITCH_TEXT.get(c, c))}" for c, n in s["hitch_causes"].items())
        line += tr(" | hitches {n} (worst {ms:.0f} ms: {causes})").format(n=s["hitches"], ms=s["hitch_max_ms"],
                                                                        causes=causes)
    return line


class SessionStats:
    def __init__(self, interval: float, adaptive=None, source=None, transport=None):
        self.interval = interval
        self.adaptive = adaptive
        self.source = source
        self.transport = transport
        self.sent = {}  # frame_no -> (send_ms, age_ms, size, wait_ms, capture_ms, reenvio)
        self.total_frames = 0
        self.total_bytes = 0
        self.window = Window(source, transport)
        self.phase = Window(source, transport)
        self.last_summary = None
        self.last_send_ms = None  # envio anterior (engasgos)
        self.resent_mark = 0

    def _resent_total(self) -> int:
        t = self.transport
        return (t.resent_chunks + getattr(t, "retry_resends", 0)) if t is not None else 0

    def on_send(self, frame_no: int, send_ms: int, age_ms: float, size: int, wait_ms: float,
                capture_ms: float = 0.0, resend: bool = False, idr: bool = False) -> None:
        """wait_ms: pedido chegou -> envio (espera por frame novo + encode). idr:
        o pacote é um IDR do modo P."""
        resent = self._resent_total()
        if self.last_send_ms is not None and not resend:
            gap = send_ms - self.last_send_ms
            if gap >= HITCH_MS:
                if idr:
                    cause = "IDR"  # o PSP perdeu a corrente (ou o encoder foi refeito)
                elif resent != self.resent_mark:
                    cause = "loss"  # pedaço ou frame reenviado no intervalo
                elif wait_ms >= gap / 2:
                    cause = "capture"  # o pedido esperou o PC ter frame novo
                else:
                    cause = "late request"  # o pedido demorou a chegar: Wi-Fi ou PSP
                for w in (self.window, self.phase):
                    w.hitches.append((gap, cause))
        # reenvio por tela parada: o intervalo seguinte não é engasgo
        self.last_send_ms = None if resend else send_ms
        self.resent_mark = resent
        self.sent[frame_no] = (send_ms, age_ms, size, wait_ms, capture_ms, resend)
        if len(self.sent) > 256:
            for old in sorted(self.sent)[:128]:
                del self.sent[old]
        for w in (self.window, self.phase):
            w.frames += 1
            w.bytes += size
            w.keepalive += resend
            w.idrs += idr
        self.total_frames += 1
        self.total_bytes += size

    def on_audio(self, nbytes: int) -> None:
        for w in (self.window, self.phase):
            w.audio_bytes += nbytes

    def on_ack(self, req, recv_ms: int) -> None:
        for w in (self.window, self.phase):
            if w.lost0 is None:
                w.lost0 = req.lost
            w.lost1 = req.lost
            if req.ping_live:
                w.ping.append(req.ping_live / 10)
                w.ping_min.append(req.ping_live_min / 10)
        meta = self.sent.pop(req.ack_frame, None)
        if meta is None:
            return
        _, age_ms, size, wait_ms, capture_ms, resend = meta
        # Relógio só do servidor: envio -> exibição -> (ack sobe pela rede).
        path = ((recv_ms - req.echo_ts) & 0xFFFFFFFF) - req.since_t / 10
        net = req.net_t / 10
        transfer = max(0.0, net - wait_ms)
        decode = req.decode_t / 10
        for w in (self.window, self.phase):
            # Um reenvio por tela parada tem "idade" de ~1 s, mas a imagem não
            # mudou: não é latência de verdade, então fica fora da média.
            if 0 <= path < 10_000 and not resend:
                w.latency.append(capture_ms + age_ms + path)
            w.capture.append(capture_ms)
            if not resend:
                w.age.append(age_ms)
            w.wait.append(wait_ms)
            w.net.append(net)
            w.transfer.append(transfer)
            if transfer > 0.5:
                w.rate.append(size / 1024 / (transfer / 1000))
            w.local.append(req.local_t / 10)
            w.decode.append(decode)
            if req.early_b:
                w.early.append(req.early_b)
            # reenvio por tela parada: o "tempo morto" é a espera de ~1 s, não a rede
            if req.idle_t != protocol.IDLE_NONE and not resend:
                w.idle.append(req.idle_t / 10)
            if req.burst_t:
                w.first.append(req.first_t / 10)
                w.burst.append(req.burst_t / 10)
                # o 1º pedaço já tinha chegado quando a rajada começou a contar
                payload = size - min(size, 1400)
                if payload and req.burst_t > 5:
                    w.burst_rate.append(payload / 1024 / (req.burst_t / 10000))
        if self.adaptive:
            self.adaptive.on_ack(size, transfer, decode)

    def maybe_report(self, quality=None) -> None:
        if self.adaptive:
            self.adaptive.update()
        if time.monotonic() - self.window.t0 < self.interval:
            return
        self.last_summary = self.window.summary(quality)
        log.info(format_summary(self.last_summary))
        self.window = Window(self.source, self.transport)

    def start_phase(self) -> None:
        self.phase = Window(self.source, self.transport)

    def phase_summary(self, quality=None) -> dict:
        return self.phase.summary(quality)
