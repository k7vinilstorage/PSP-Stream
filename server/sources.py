"""Fontes de frames JPEG.

Toda fonte guarda só o frame mais recente: quem envia pega o último e o resto
é descartado. É isso que mantém a latência baixa no modelo pull.
"""
import logging
import threading
import time

from jpeginfo import jpeg_info

log = logging.getLogger("pspstream.source")


class FrameSource:
    # Se True, a fonte não produz frames novos e o mesmo frame é reenviado a
    # cada pedido (modo estático = benchmark de rede + decode).
    repeat = False

    def __init__(self):
        self._cond = threading.Condition()
        self._seq = 0
        self._jpeg = None
        self._ready_t = 0.0

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._seq += 1
            self._jpeg = jpeg
            self._ready_t = time.monotonic()
            self._cond.notify_all()

    def latest(self):
        with self._cond:
            return self._seq, self._jpeg, self._ready_t

    def wait_newer(self, seq: int, timeout: float):
        """Espera um frame com número > seq. Devolve (seq, jpeg, ready_t) ou None."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > seq, timeout):
                return None
            return self._seq, self._jpeg, self._ready_t

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_quality(self, quality: int) -> None:
        pass

    @property
    def quality(self):
        return None


class StaticSource(FrameSource):
    repeat = True

    def __init__(self, jpeg: bytes):
        super().__init__()
        info = jpeg_info(jpeg)
        for problem in info.problems():
            log.warning("imagem estática: %s", problem)
        log.info("imagem estática: %dx%d, %.1f KB", info.width, info.height, len(jpeg) / 1024)
        self.publish(jpeg)
