"""H.264 só com quadros completos (IDR), para o decoder de hardware do PSP.

Medido no PSP-3000 (psp/probe, docs/MEASUREMENTS.md): todo frame IDR +
sceMpegAvcDecodeStop sai na hora (sem os 2 frames que o decoder segura
com frames P), em ~3,7 ms direto na VRAM. Cada frame é independente, como
no MJPEG: o servidor pode pular frames e uma perda estraga só aquele frame.

Encoder: openh264enc (no Fedora, pacote gstreamer1-plugin-openh264 do
repositório fedora-cisco-openh264). O x264enc do GStreamer segura 1 frame
mesmo com tune=zerolatency (medido), e numa tela parada isso esconderia a
última mudança.

Os parâmetros de QP do openh264enc não mudam com o pipeline rodando. Como
não há frames de referência, trocar a qualidade é só refazer o pipeline do
encoder (alguns ms).
"""
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

ENCODER = "openh264enc"


def available() -> bool:
    return Gst.ElementFactory.find(ENCODER) is not None


def qp_for_quality(quality: int) -> int:
    """Qualidade no estilo JPEG (1-100) -> QP do H.264, na mesma SSIM.

    Calibrado em 12 frames (desktop + jogos), openh264 intra x jpegenc:
    q30 -> QP 40 (40% dos bytes), q50 -> 36 (45%), q70 -> 34 (42%),
    q90 -> 30 (33%)."""
    return max(18, min(51, round(44.6 - 0.16 * quality)))


class H264Encoder:
    """Recebe I420 width x height, devolve um AU Annex B (SPS + PPS + IDR)."""

    def __init__(self, width: int, height: int, quality: int):
        self.width, self.height = width, height
        self.quality = quality
        self._qp = None
        self._pipe = None
        self._n = 0
        self._lock = threading.Lock()

    def _build(self, qp: int) -> None:
        desc = (
            "appsrc name=src is-live=true format=time "
            f"caps=video/x-raw,format=I420,width={self.width},height={self.height},framerate=0/1 "
            # quality + bitrate alto + QP travado = QP constante (com rate-control=off
            # o openh264enc ignora qp-min/qp-max)
            f"! {ENCODER} gop-size=1 rate-control=quality bitrate=50000000 qp-min={qp} qp-max={qp} "
            "complexity=medium slice-mode=n-slices num-slices=1 "
            "! video/x-h264,stream-format=byte-stream,alignment=au "
            "! appsink name=sink sync=false async=false"
        )
        self._pipe = Gst.parse_launch(desc)
        self._src = self._pipe.get_by_name("src")
        self._sink = self._pipe.get_by_name("sink")
        if self._pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f"não foi possível iniciar o {ENCODER}")
        self._qp = qp

    def _close(self) -> None:
        if self._pipe is not None:
            self._pipe.set_state(Gst.State.NULL)
            self._pipe = None

    def set_quality(self, quality: int) -> None:
        self.quality = max(1, min(100, int(quality)))  # vale a partir do próximo frame

    def encode(self, i420: bytes):
        with self._lock:
            qp = qp_for_quality(self.quality)
            if qp != self._qp:
                self._close()
                self._build(qp)
            buf = Gst.Buffer.new_wrapped(i420)
            buf.pts = self._n * Gst.SECOND // 60
            buf.duration = Gst.SECOND // 60
            self._n += 1
            self._src.emit("push-buffer", buf)
            sample = self._sink.emit("try-pull-sample", Gst.SECOND)
            if sample is None:
                return None
            out = sample.get_buffer()
            return out.extract_dup(0, out.get_size())

    def close(self) -> None:
        with self._lock:
            self._close()


def image_to_i420(path: str, width: int, height: int, keep_aspect: bool = True, scale: str = "bilinear") -> bytes:
    """Qualquer imagem -> I420 width x height (para o modo static)."""
    desc = (
        f'filesrc location="{path}" ! decodebin ! imagefreeze num-buffers=1 ! videoconvert '
        f"! videoscale method={scale} add-borders={'true' if keep_aspect else 'false'} "
        f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
        "! videoconvert ! video/x-raw,format=I420 ! appsink name=sink max-buffers=1"
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


def is_h264(data: bytes) -> bool:
    return data[:4] == b"\x00\x00\x00\x01" or data[:3] == b"\x00\x00\x01"
