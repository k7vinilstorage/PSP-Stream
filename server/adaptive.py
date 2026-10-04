"""Qualidade JPEG adaptativa: a maior qualidade que não derruba o FPS.

Com prefetch, o PSP recebe o frame N+1 enquanto decodifica o N, então o FPS
fica limitado pelo mais lento entre transferir e decodificar:

    fps ~ 1 / max(transferência, decode)

A transferência é tamanho / vazão. Alvo de tamanho:

    alvo = vazão x max(1 / fps_alvo, decode)

Acima do alvo, o FPS e a latência pioram; abaixo, sobra banda e dá para
subir a qualidade de graça. A vazão é estimada pelos relatórios do PSP:
`net_t` (pedido -> frame recebido) menos o tempo que o servidor esperou por
um frame novo.
"""
import logging
import statistics
import time
from collections import deque

log = logging.getLogger("pspstream.adaptive")


class AdaptiveQuality:
    def __init__(self, source, target_fps: float = 30, q_min: int = 25, q_max: int = 90,
                 interval: float = 0.5):
        self.source = source
        self.budget_ms = 1000.0 / target_fps
        self.q_min = q_min
        self.q_max = q_max
        self.interval = interval
        self.rates = deque(maxlen=40)    # bytes/ms por frame
        self.sizes = deque(maxlen=20)    # bytes, só da qualidade atual
        self.decodes = deque(maxlen=20)  # ms
        self.last = time.monotonic()

    def on_ack(self, size: int, transfer_ms: float, decode_ms: float) -> None:
        if transfer_ms > 0.5:
            self.rates.append(size / transfer_ms)
        self.sizes.append(size)
        if decode_ms > 0:
            self.decodes.append(decode_ms)

    def rate_kbps(self) -> float:
        return statistics.median(self.rates) * 1000 / 1024 if self.rates else 0.0

    def update(self) -> None:
        now = time.monotonic()
        if now - self.last < self.interval or len(self.rates) < 5 or len(self.sizes) < 3:
            return
        self.last = now
        q = self.source.quality
        if q is None:
            return
        rate = statistics.median(self.rates)  # bytes/ms
        decode = statistics.median(self.decodes) if self.decodes else 0.0
        target = rate * max(self.budget_ms, decode)
        ratio = target / statistics.mean(self.sizes)
        if ratio < 0.92:
            new_q = q - max(2, min(10, round((1 - ratio) * 25)))
        elif ratio > 1.15:
            new_q = q + max(1, min(5, round((ratio - 1) * 10)))
        else:
            return
        new_q = max(self.q_min, min(self.q_max, new_q))
        if new_q != q:
            log.debug("qualidade %d -> %d (alvo %.1f KB, média %.1f KB, vazão %.0f KB/s, decode %.1f ms)",
                      q, new_q, target / 1024, statistics.mean(self.sizes) / 1024,
                      rate * 1000 / 1024, decode)
            self.source.set_quality(new_q)
            self.sizes.clear()
