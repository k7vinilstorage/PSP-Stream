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
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402

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


AUD = b"\x00\x00\x00\x01\x09\xf0"  # access unit delimiter: separa os AUs de um pacote P
COPIES = 2  # o decoder do PSP segura 2 frames; 2 cópias sem mudança empurram o frame real para a tela
IDR_MIN_INTERVAL_S = 0.15  # pedidos de IDR repetidos enquanto o anterior ainda viaja: ignora
# O openh264enc não troca o QP com o pipeline rodando (medido: o tamanho não
# muda); trocar é refazer o encoder, e o pipeline novo começa com IDR. Então a
# qualidade nova (adaptativo) só entra junto de um IDR que já ia sair, ou no
# máximo a cada QP_CHANGE_MIN_S.
QP_CHANGE_MIN_S = 3.0


class H264PEncoder:
    """Frames P (IPPP), codificados só quando vão ser enviados.

    O decoder do PSP (medido no PSP-3000) só solta o frame N depois de receber
    o N+2, e o sceMpegAvcDecodeStop, que solta na hora, zera as referências.
    Então cada pacote leva o frame real e 2 cópias dele (P sem mudança, ~15-30
    bytes cada), separados por AUD. O PSP decodifica os 3 e mostra o que sai
    da última chamada: o frame real, sem atraso.

    Como a referência de cada P é o frame anterior, o servidor não pode pular
    frames já codificados: este encoder só recebe os que vão ser enviados.
    Um frame perdido quebra a corrente, e o PSP pede um IDR (PS_REQ_IDR)."""

    def __init__(self, width: int, height: int, quality: int, qp_change_min_s: float = QP_CHANGE_MIN_S):
        self.width, self.height = width, height
        self.quality = quality       # pedida (adaptativo)
        self.qp_change_min_s = qp_change_min_s
        self._qp = None              # em uso
        self._qp_t = 0.0             # quando o encoder foi refeito
        self._pipe = None
        self._n = 0
        self._last_idr = 0.0
        self._idr = False            # IDR pedido para o próximo frame
        self._lock = threading.Lock()

    def _build(self, qp: int) -> None:
        desc = (
            "appsrc name=src is-live=true format=time "
            f"caps=video/x-raw,format=I420,width={self.width},height={self.height},framerate=0/1 "
            f"! {ENCODER} gop-size=100000 rate-control=quality bitrate=50000000 qp-min={qp} qp-max={qp} "
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
        self.quality = max(1, min(100, int(quality)))

    @property
    def applied_quality(self):
        """Qualidade do QP em uso (pode estar atrás da pedida por até QP_CHANGE_MIN_S)."""
        return self.quality if self._qp == qp_for_quality(self.quality) else None

    def request_idr(self) -> bool:
        """Próximo frame vira IDR. False se um IDR saiu há pouco (o pedido é do
        IDR que ainda está a caminho)."""
        if time.monotonic() - self._last_idr < IDR_MIN_INTERVAL_S:
            return False
        with self._lock:
            self._idr = True
        return True

    def _encode_one(self, i420: bytes) -> bytes:
        buf = Gst.Buffer.new_wrapped(i420)
        buf.pts = self._n * Gst.SECOND // 60
        buf.duration = Gst.SECOND // 60
        self._n += 1
        self._src.emit("push-buffer", buf)
        sample = self._sink.emit("try-pull-sample", Gst.SECOND)
        if sample is None:
            raise RuntimeError(f"o {ENCODER} não devolveu o frame")
        out = sample.get_buffer()
        return out.extract_dup(0, out.get_size())

    def encode(self, i420: bytes) -> bytes:
        """Pacote: AUD + frame + COPIES x (AUD + cópia)."""
        with self._lock:
            qp = qp_for_quality(self.quality)
            now = time.monotonic()
            if self._pipe is None or (qp != self._qp and (self._idr or now - self._qp_t >= self.qp_change_min_s)):
                self._close()  # o QP não muda com o pipeline rodando; o novo começa com IDR
                self._build(qp)
                self._qp_t = now
            elif self._idr:
                ev = GstVideo.video_event_new_downstream_force_key_unit(
                    Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, True, 0)
                self._src.get_static_pad("src").push_event(ev)
            self._idr = False
            first = self._encode_one(i420)
            if is_idr(first):
                self._last_idr = time.monotonic()
            parts = [AUD, first]
            for _ in range(COPIES):
                parts += [AUD, self._encode_one(i420)]
            return b"".join(parts)

    def close(self) -> None:
        with self._lock:
            self._close()


def nal_types(au: bytes):
    """Tipos das NALs de um trecho Annex B."""
    types, i = [], 0
    while True:
        j = au.find(b"\x00\x00\x01", i)
        if j < 0 or j + 3 >= len(au):
            return types
        types.append(au[j + 3] & 0x1F)
        i = j + 3


def is_idr(au: bytes) -> bool:
    return 5 in nal_types(au)


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
