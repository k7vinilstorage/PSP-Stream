"""Captura e codificação MJPEG com GStreamer (em processo, via PyGObject).

Pipeline (só guarda o frame mais novo em cada etapa):

  <fonte> ! queue leaky (1 buffer) ! videorate max-rate=FPS
          ! videoscale (480x272, mantém proporção) ! videoconvert I420
          ! jpegenc quality=Q ! appsink (1 buffer, drop)

Reduzir antes de converter: o videoconvert trabalha em 480x272 e não em
1080p/1440p. Custo medido (CPU de desenvolvimento, 1080p): ~1 ms com
bilinear, ~5 ms com lanczos, + ~1 ms do jpegenc.
"""
import logging
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from sources import FrameSource  # noqa: E402

log = logging.getLogger("pspstream.gst")

Gst.init(None)


def build_pipeline(src: str, width: int, height: int, fps: int, quality: int,
                   scale: str = "bilinear", keep_aspect: bool = True) -> str:
    return (
        f"{src} "
        "! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        f"! videorate drop-only=true max-rate={fps} "
        f"! videoscale method={scale} add-borders={'true' if keep_aspect else 'false'} "
        f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
        "! videoconvert ! video/x-raw,format=I420 "
        f"! jpegenc name=enc quality={quality} "
        "! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
    )


# Fontes prontas. {w}/{h} são do frame de teste, não da saída.
SOURCES = {
    # Padrão animado + relógio na tela (útil para medir latência filmando).
    "test": ("videotestsrc is-live=true pattern=ball ! video/x-raw,width=1920,height=1080,framerate=60/1 "
             "! timeoverlay font-desc=\"Sans 64\" halignment=center valignment=center"),
    # Sessão X11 (no Wayland use "portal").
    "x11": "ximagesrc use-damage=false show-pointer=true ! video/x-raw,framerate=60/1",
}


class GstSource(FrameSource):
    def __init__(self, src: str, width: int, height: int, fps: int, quality: int,
                 scale: str = "bilinear", keep_aspect: bool = True, keepalive=None):
        super().__init__()
        self._keepalive = keepalive  # objeto que precisa viver junto (ex.: sessão do portal)
        self._quality = quality
        desc = build_pipeline(src, width, height, fps, quality, scale, keep_aspect)
        log.debug("pipeline: %s", desc)
        self.pipeline = Gst.parse_launch(desc)
        self.enc = self.pipeline.get_by_name("enc")
        sink = self.pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        self.failed = None
        self._stop = threading.Event()

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                self.publish(bytes(info.data))
            finally:
                buf.unmap(info)
        return Gst.FlowReturn.OK

    def _watch_bus(self):
        bus = self.pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
        while not self._stop.is_set():
            msg = bus.timed_pop_filtered(100 * Gst.MSECOND, mask)
            if msg is None:
                continue
            if msg.type == Gst.MessageType.WARNING:
                err, _ = msg.parse_warning()
                log.warning("GStreamer: %s", err.message)
            elif msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                self.failed = f"{err.message} ({dbg})"
                log.error("GStreamer: %s", self.failed)
                return
            elif msg.type == Gst.MessageType.EOS:
                self.failed = "fim do stream (EOS)"
                log.error("GStreamer: %s", self.failed)
                return

    def start(self) -> None:
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("não foi possível iniciar o pipeline GStreamer")
        threading.Thread(target=self._watch_bus, name="gst-bus", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.pipeline.set_state(Gst.State.NULL)

    def set_quality(self, quality: int) -> None:
        # jpegenc aceita mudar a qualidade com o pipeline rodando.
        quality = max(1, min(100, int(quality)))
        if quality != self._quality:
            self.enc.set_property("quality", quality)
            self._quality = quality

    @property
    def quality(self):
        return self._quality


def transcode_image(path: str, width: int, height: int, quality: int, keep_aspect: bool = True) -> bytes:
    """Converte qualquer imagem (PNG, JPEG grande...) num JPEG 4:2:0 de até width x height."""
    src = f"filesrc location=\"{path}\" ! decodebin ! imagefreeze num-buffers=1 ! videoconvert"
    desc = (
        f"{src} ! videoscale add-borders={'true' if keep_aspect else 'false'} "
        f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
        f"! videoconvert ! video/x-raw,format=I420 ! jpegenc quality={quality} "
        "! appsink name=sink max-buffers=1"
    )
    pipeline = Gst.parse_launch(desc)
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        sample = sink.emit("try-pull-sample", 10 * Gst.SECOND)
        if sample is None:
            raise RuntimeError(f"não consegui converter {path}")
        buf = sample.get_buffer()
        return buf.extract_dup(0, buf.get_size())
    finally:
        pipeline.set_state(Gst.State.NULL)
