"""Estatísticas da sessão: FPS, KB/frame, banda e latência."""
import logging
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


class SessionStats:
    def __init__(self, interval: float):
        self.interval = interval
        self.sent = {}  # frame_no -> (send_ms, age_ms, size)
        self.last_report = time.monotonic()
        self.total_frames = 0
        self.total_bytes = 0
        self.last_line = None
        self._reset()

    def _reset(self):
        self.frames = 0
        self.bytes = 0
        self.latency = []  # PC pronto -> PSP exibido (ms)
        self.age = []      # frame pronto -> enviado (ms)
        self.net = []      # pedido -> frame recebido, medido no PSP (ms)
        self.local = []    # recebido -> exibido, medido no PSP (ms)
        self.decode = []   # só decode (ms)

    def on_send(self, frame_no: int, send_ms: int, age_ms: float, size: int) -> None:
        self.sent[frame_no] = (send_ms, age_ms, size)
        if len(self.sent) > 256:
            for old in sorted(self.sent)[:128]:
                del self.sent[old]
        self.frames += 1
        self.bytes += size
        self.total_frames += 1
        self.total_bytes += size

    def on_ack(self, req, recv_ms: int) -> None:
        meta = self.sent.pop(req.ack_frame, None)
        if meta is None:
            return
        send_ms, age_ms, _ = meta
        # Relógio só do servidor: envio -> exibição -> (ack sobe pela rede).
        path = ((recv_ms - req.echo_ts) & 0xFFFFFFFF) - req.since_t / 10
        if 0 <= path < 10_000:
            self.latency.append(age_ms + path)
        self.age.append(age_ms)
        self.net.append(req.net_t / 10)
        self.local.append(req.local_t / 10)
        self.decode.append(req.decode_t / 10)

    def maybe_report(self, quality=None) -> None:
        now = time.monotonic()
        elapsed = now - self.last_report
        if elapsed < self.interval:
            return
        fps = self.frames / elapsed
        kb = self.bytes / self.frames / 1024 if self.frames else 0
        kbps = self.bytes / elapsed / 1024
        line = (
            f"{fps:5.1f} fps | {kb:5.1f} KB/frame | {kbps:6.1f} KB/s | "
            f"latência {_avg(self.latency):5.1f} ms (p95 {_p95(self.latency):5.1f}) = "
            f"idade {_avg(self.age):4.1f} + rede {_avg(self.net):5.1f} + psp {_avg(self.local):5.1f} "
            f"| decode {_avg(self.decode):4.1f} ms"
        )
        if quality is not None:
            line += f" | q {quality}"
        log.info(line)
        self.last_line = {
            "fps": fps, "kb_per_frame": kb, "kbps": kbps,
            "latency_ms": _avg(self.latency), "latency_p95_ms": _p95(self.latency),
            "age_ms": _avg(self.age), "net_ms": _avg(self.net),
            "local_ms": _avg(self.local), "decode_ms": _avg(self.decode),
            "quality": quality,
        }
        self.last_report = now
        self._reset()
