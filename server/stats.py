"""Estatísticas da sessão: FPS, KB/frame, vazão do Wi-Fi e latência."""
import logging
import statistics
import time

log = logging.getLogger("pspstream.stats")


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
            "quality": quality,
            "frames": self.frames,
        }


def format_summary(s: dict) -> str:
    source = f" (fonte {s['source_fps']:4.1f})" if s["source_fps"] is not None else ""
    line = (
        f"{s['fps']:5.1f} fps{source} | {s['kb_per_frame']:5.1f} KB/frame | "
        f"Wi-Fi {s['wifi_kbps']:5.0f} KB/s | "
        f"latência {s['latency_ms']:5.1f} ms (p95 {s['latency_p95_ms']:5.1f}) ~ "
        f"captura {s['capture_ms']:4.1f} + idade {s['age_ms']:4.1f} + "
        f"rede {s['transfer_ms']:5.1f} + psp {s['local_ms']:5.1f} "
        f"| decode {s['decode_ms']:4.1f} ms | espera por frame novo {s['wait_ms']:4.1f} ms"
    )
    if s["quality"] is not None:
        line += f" | q {s['quality']}"
    if s["keepalive"]:
        line += f" | {s['keepalive']} reenvios (tela parada)"
    if s["resent_pct"]:
        line += f" | {s['resent_pct']:.1f}% pedaços UDP reenviados"
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

    def on_send(self, frame_no: int, send_ms: int, age_ms: float, size: int, wait_ms: float,
                capture_ms: float = 0.0, resend: bool = False) -> None:
        self.sent[frame_no] = (send_ms, age_ms, size, wait_ms, capture_ms, resend)
        if len(self.sent) > 256:
            for old in sorted(self.sent)[:128]:
                del self.sent[old]
        for w in (self.window, self.phase):
            w.frames += 1
            w.bytes += size
            w.keepalive += resend
        self.total_frames += 1
        self.total_bytes += size

    def on_ack(self, req, recv_ms: int) -> None:
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
