"""H.264 para o decoder de hardware do PSP: todo frame IDR (H264Encoder) ou
frames P codificados na hora de enviar (H264PEncoder).

Medido no PSP-3000 (psp/probe, wiki/Medições.md): todo frame IDR +
sceMpegAvcDecodeStop sai na hora, em ~3,7 ms direto na VRAM; frame P + 2
cópias, sem Stop, em ~10,6 ms.

Encoder: openh264. Chamado direto pela libopenh264 (openh264.py, ctypes):
sem as filas e threads do GStreamer, e o QP muda sem IDR. Se a biblioteca
faltar ou o layout dela não bater, volta para o openh264enc do GStreamer
(pacote em distro.PACKAGES["openh264"]), que só troca o QP refazendo o
encoder. O x264enc do GStreamer segura 1 frame
mesmo com tune=zerolatency (medido), e numa tela parada isso esconderia a
última mudança.
"""
import logging
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402
from i18n import tr  # noqa: E402

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


log = logging.getLogger("pspstream.h264")

# auto: libopenh264 direto, com o GStreamer de reserva; "openh264" ou
# "gstreamer" forçam um dos dois (pspstream.py --h264-encoder).
BACKEND = "auto"
_warned = set()


class _GstPipe:
    """openh264enc pelo GStreamer: appsrc -> encoder -> appsink. O QP não muda
    com o pipeline rodando (medido): trocar é refazer, e o novo começa com IDR."""
    live_qp = False

    def __init__(self, width: int, height: int, qp: int, idr_every_frame: bool):
        gop = 1 if idr_every_frame else 100000
        desc = (
            "appsrc name=src is-live=true format=time "
            f"caps=video/x-raw,format=I420,width={width},height={height},framerate=0/1 "
            # quality + bitrate alto + QP travado = QP constante (com rate-control=off
            # o openh264enc ignora qp-min/qp-max)
            f"! {ENCODER} gop-size={gop} rate-control=quality bitrate=50000000 qp-min={qp} qp-max={qp} "
            "complexity=medium slice-mode=n-slices num-slices=1 "
            "! video/x-h264,stream-format=byte-stream,alignment=au "
            "! appsink name=sink sync=false async=false"
        )
        self._pipe = Gst.parse_launch(desc)
        self._src = self._pipe.get_by_name("src")
        self._sink = self._pipe.get_by_name("sink")
        if self._pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(tr("could not start {encoder}").format(encoder=ENCODER))
        self._n = 0

    def encode(self, i420: bytes) -> bytes:
        buf = Gst.Buffer.new_wrapped(i420)
        buf.pts = self._n * Gst.SECOND // 60
        buf.duration = Gst.SECOND // 60
        self._n += 1
        self._src.emit("push-buffer", buf)
        sample = self._sink.emit("try-pull-sample", Gst.SECOND)
        if sample is None:
            raise RuntimeError(tr("{encoder} did not return the frame").format(encoder=ENCODER))
        out = sample.get_buffer()
        return out.extract_dup(0, out.get_size())

    def force_idr(self) -> None:
        ev = GstVideo.video_event_new_downstream_force_key_unit(
            Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, Gst.CLOCK_TIME_NONE, True, 0)
        self._src.get_static_pad("src").push_event(ev)

    def close(self) -> None:
        if self._pipe is not None:
            self._pipe.set_state(Gst.State.NULL)
            self._pipe = None


def _open(width: int, height: int, qp: int, idr_every_frame: bool, backend: str):
    """O encoder pedido; o direto, se der, e o GStreamer de reserva."""
    if backend != "gstreamer":
        import openh264
        try:
            enc = openh264.Encoder(width, height, qp, idr_every_frame=idr_every_frame)
            if "direct" not in _warned:
                _warned.add("direct")
                log.info(tr("H.264: libopenh264 %s called directly"), ".".join(map(str, enc.version)))
            return enc
        except openh264.OpenH264Error as exc:
            if backend == "openh264":
                raise RuntimeError(f"--h264-encoder openh264: {exc}") from exc
            if "fallback" not in _warned:
                _warned.add("fallback")
                log.warning(tr("H.264: %s; using GStreamer's openh264enc"), exc)
    return _GstPipe(width, height, qp, idr_every_frame)


def _encode_safe(owner, i420: bytes) -> bytes:
    """encode() que troca o direto pelo GStreamer se ele se mostrar errado no
    meio (saída inconsistente): o encoder novo começa com IDR."""
    import openh264
    try:
        return owner._enc.encode(i420)
    except openh264.OpenH264Error as exc:
        if owner.backend == "openh264":
            raise
        log.warning(tr("H.264: %s; switching to GStreamer's openh264enc"), exc)
        owner._enc.close()
        owner.backend = "gstreamer"
        owner._enc = _GstPipe(owner.width, owner.height, owner._qp, owner.idr_every_frame)
        return owner._enc.encode(i420)


class H264Encoder:
    """Recebe I420 width x height, devolve um AU Annex B (SPS + PPS + IDR)."""
    idr_every_frame = True

    def __init__(self, width: int, height: int, quality: int, backend: str = None):
        self.width, self.height = width, height
        self.quality = quality
        self.backend = backend or BACKEND
        self._qp = None
        self._enc = None
        self._lock = threading.Lock()

    def set_quality(self, quality: int) -> None:
        self.quality = max(1, min(100, int(quality)))  # vale a partir do próximo frame

    def encode(self, i420: bytes):
        with self._lock:
            qp = qp_for_quality(self.quality)
            if self._enc is not None and qp != self._qp:
                if self._enc.live_qp:
                    self._enc.set_qp(qp)
                else:  # sem referências, refazer o encoder não custa nada além de alguns ms
                    self._enc.close()
                    self._enc = None
                self._qp = qp
            if self._enc is None:
                self._enc = _open(self.width, self.height, qp, True, self.backend)
                self._qp = qp
            return _encode_safe(self, i420)

    def close(self) -> None:
        with self._lock:
            if self._enc is not None:
                self._enc.close()
                self._enc = None


AUD = b"\x00\x00\x00\x01\x09\xf0"  # access unit delimiter: separa os AUs de um pacote P
COPIES = 2  # o decoder do PSP segura 2 frames; 2 cópias sem mudança empurram o frame real para a tela
IDR_MIN_INTERVAL_S = 0.15  # pedidos de IDR repetidos enquanto o anterior ainda viaja: ignora
# O openh264enc não troca o QP com o pipeline rodando (medido: o tamanho não
# muda); trocar é refazer o encoder, e o pipeline novo começa com IDR. Então a
# qualidade nova (adaptativo) só entra junto de um IDR que já ia sair, ou no
# máximo a cada QP_CHANGE_MIN_S.
QP_CHANGE_MIN_S = 3.0
# IDR periódico (pacotes; 0 = só quando o PSP pede). Era 1800 (30 s a 60 fps)
# por medo da volta de frame_num (15 bits) e do POC (16 bits) do openh264, que
# acontece em ~3 min sem IDR. A sonda v4.1 (psp/probe, passo 7) passou 12000
# frames (36000 AUs) sem IDR no PSP-3000, com a volta no meio: ela não é
# problema. O IDR a mais custava ~10 KB e uma travadinha a cada 30 s.
IDR_EVERY = 0


class H264PEncoder:
    """Frames P (IPPP), codificados só quando vão ser enviados.

    O decoder do PSP (medido no PSP-3000) só solta o frame N depois de receber
    o N+2, e o sceMpegAvcDecodeStop, que solta na hora, zera as referências.
    Então cada pacote leva o frame real e 2 cópias dele (P sem mudança, ~20-80
    bytes cada), separados por AUD. O PSP decodifica os 3 e mostra o que sai
    da última chamada: o frame real, sem atraso.

    Como a referência de cada P é o frame anterior, o servidor não pode pular
    frames já codificados: este encoder só recebe os que vão ser enviados.
    Um frame perdido quebra a corrente, e o PSP pede um IDR (PS_REQ_IDR).

    Qualidade nova: com o openh264 direto, vale no próximo frame, sem IDR. Pelo
    GStreamer, refazer o encoder é um IDR, então ela espera um IDR que já ia
    sair ou no máximo qp_change_min_s."""
    idr_every_frame = False

    def __init__(self, width: int, height: int, quality: int, qp_change_min_s: float = QP_CHANGE_MIN_S,
                 copies: int = COPIES, idr_every: int = IDR_EVERY, backend: str = None):
        self.width, self.height = width, height
        self.copies = copies
        self.idr_every = idr_every   # 0 = só quando pedido
        self.backend = backend or BACKEND
        self._since_idr = 0
        self.quality = quality       # pedida (adaptativo)
        self.qp_change_min_s = qp_change_min_s
        self._qp = None              # em uso
        self._qp_t = 0.0             # quando o encoder (GStreamer) foi refeito
        self._enc = None
        self._last_idr = 0.0
        self._idr = False            # IDR pedido para o próximo frame
        self._lock = threading.Lock()

    @property
    def live_qp(self) -> bool:
        """True se a qualidade muda sem IDR (openh264 direto)."""
        if self._enc is None:
            with self._lock:
                if self._enc is None:
                    self._enc = _open(self.width, self.height, qp_for_quality(self.quality), False, self.backend)
                    self._qp = qp_for_quality(self.quality)
                    self._qp_t = time.monotonic()
        return self._enc.live_qp

    def set_quality(self, quality: int) -> None:
        self.quality = max(1, min(100, int(quality)))

    @property
    def applied_quality(self):
        """Qualidade do QP em uso (pelo GStreamer, pode estar atrás da pedida por até qp_change_min_s)."""
        return self.quality if self._qp == qp_for_quality(self.quality) else None

    def request_idr(self) -> bool:
        """Próximo frame vira IDR. False se um IDR saiu há pouco (o pedido é do
        IDR que ainda está a caminho)."""
        if time.monotonic() - self._last_idr < IDR_MIN_INTERVAL_S:
            return False
        with self._lock:
            self._idr = True
        return True

    def encode(self, i420: bytes) -> bytes:
        """Pacote: AUD + frame + COPIES x (AUD + cópia)."""
        with self._lock:
            self._since_idr += 1  # pacotes desde o último IDR, contando este
            if self.idr_every and self._since_idr >= self.idr_every:
                self._idr = True
            qp = qp_for_quality(self.quality)
            now = time.monotonic()
            fresh = False
            if self._enc is not None and qp != self._qp:
                if self._enc.live_qp:
                    self._enc.set_qp(qp)
                    self._qp = qp
                elif self._idr or now - self._qp_t >= self.qp_change_min_s:
                    self._enc.close()  # o pipeline novo começa com IDR
                    self._enc = None
            if self._enc is None:
                self._enc = _open(self.width, self.height, qp, False, self.backend)
                self._qp, self._qp_t, fresh = qp, now, True
            if self._idr and not fresh:
                self._enc.force_idr()
            self._idr = False
            first = _encode_safe(self, i420)
            if is_idr(first):
                self._last_idr = time.monotonic()
                self._since_idr = 0
            parts = [AUD, first]
            for _ in range(self.copies):
                parts += [AUD, _encode_safe(self, i420)]
            return b"".join(parts)

    def close(self) -> None:
        with self._lock:
            if self._enc is not None:
                self._enc.close()
                self._enc = None


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


def set_sps_level(data: bytes, level_idc: int) -> bytes:
    """Troca o level_idc dos SPS (3º byte depois do cabeçalho da NAL). Os bytes
    antes dele (profile 66, flags 0xC0 no openh264) não são zero, então não há
    byte de prevenção de emulação no caminho."""
    out = bytearray(data)
    i = 0
    while True:
        j = out.find(b"\x00\x00\x01", i)
        if j < 0 or j + 7 > len(out):
            return bytes(out)
        if out[j + 3] & 0x1F == 7:
            if 0 in out[j + 4:j + 6]:
                raise ValueError(tr("SPS with a zero before level_idc"))
            out[j + 6] = level_idc
        i = j + 3


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
            raise RuntimeError(tr("could not convert {path}").format(path=path))
        buf = sample.get_buffer()
        return buf.extract_dup(0, buf.get_size())
    finally:
        pipeline.set_state(Gst.State.NULL)


def is_h264(data: bytes) -> bool:
    return data[:4] == b"\x00\x00\x00\x01" or data[:3] == b"\x00\x00\x01"
