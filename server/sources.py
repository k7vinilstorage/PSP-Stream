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
    # Se True, os frames são I420 cru e a sessão codifica na hora de enviar
    # (--codec h264p: frames P só podem ser codificados se forem enviados).
    raw_i420 = False

    def __init__(self):
        self._cond = threading.Condition()
        self._seq = 0
        self._jpeg = None
        self._ready_t = 0.0
        self.capture_ms = 0.0  # captura -> JPEG pronto (média móvel), 0 se desconhecido

    def publish(self, jpeg: bytes, capture_ms=None) -> None:
        with self._cond:
            self._seq += 1
            self._jpeg = jpeg
            self._ready_t = time.monotonic()
            if capture_ms is not None and 0 <= capture_ms < 1000:
                self.capture_ms = capture_ms if not self.capture_ms else 0.9 * self.capture_ms + 0.1 * capture_ms
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
    """Uma imagem reenviada a cada pedido. Com `reencode`, a qualidade pode
    mudar (benchmark e modo adaptativo com conteúdo fixo e reproduzível)."""
    repeat = True

    def __init__(self, jpeg: bytes, reencode=None, quality=None, raw_i420=False):
        super().__init__()
        self._reencode = reencode
        self.raw_i420 = raw_i420
        self._quality = quality if reencode or raw_i420 else None
        if raw_i420:
            log.info("imagem estática: I420 cru, codificada a cada envio (frames P)")
        elif jpeg[:2] == b"\xff\xd8":
            info = jpeg_info(jpeg)
            for problem in info.problems():
                log.warning("imagem estática: %s", problem)
            log.info("imagem estática: %dx%d, %.1f KB", info.width, info.height, len(jpeg) / 1024)
        else:
            log.info("imagem estática: H.264, %.1f KB", len(jpeg) / 1024)
        self.publish(jpeg)

    def set_quality(self, quality: int) -> None:
        if self.raw_i420:  # quem codifica é a sessão
            self._quality = max(1, min(100, int(quality)))
            return
        if self._reencode is None or quality == self._quality:
            return
        self._quality = max(1, min(100, int(quality)))
        self.publish(self._reencode(self._quality))

    @property
    def quality(self):
        return self._quality
