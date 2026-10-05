"""Captura e codificação MJPEG com GStreamer (em processo, via PyGObject).

Pipeline (só guarda o frame mais novo em cada etapa):

  <fonte> ! queue leaky (1 buffer) [limite de --fps: RateLimiter, sonda na saída da fila]
          ! videoscale (480x272, mantém proporção) ! videoconvert I420
          ! jpegenc quality=Q ! appsink (1 buffer, drop)

Com --dmabuf (portal, experimental), a tela chega na memória da GPU e a
redução é no OpenGL; só a imagem já pequena vem para a CPU:

  pipewiresrc ! capsfilter (memory:DMABuf) ! queue leaky ! glupload ! glcolorconvert
          ! glcolorscale (436x272 para 2240x1400) ! gldownload ! videoscale (bordas) ! ...

Sem videorate: o portal entrega taxa variável (framerate=0/1), e com isso o
`videorate drop-only=true max-rate=60` deixava passar só ~38 de 60 fps (os
horários variam ±1 ms; medido no GStreamer 1.24, também com max-rate=75). Era
a "fonte" de 36-40 fps nos logs do PSP-3000.

Reduzir antes de converter: o videoconvert trabalha em 480x272 e não em
1080p/1440p. Custo medido (CPU de desenvolvimento, 1080p): ~1 ms com
bilinear, ~5 ms com lanczos, + ~1 ms do jpegenc.
"""
import collections
import logging
import os
import statistics
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from sources import FrameSource  # noqa: E402

log = logging.getLogger("pspstream.gst")

Gst.init(None)


# Reduzir 2240x1400 -> 480x272 com bilinear2: 4,0 ms em 1 thread, 2,5 ms em 4.
SCALE_THREADS = min(4, os.cpu_count() or 1)


def gpu_size(src_size, width: int, height: int, keep_aspect: bool = True):
    """Tamanho da redução na GPU: cabe em width x height mantendo a proporção
    (as bordas pretas vêm depois, na CPU, já em 480x272). Larguras e alturas pares."""
    if not keep_aspect or not src_size:
        return width, height
    sw, sh = src_size
    f = min(width / sw, height / sh)
    return max(2, round(sw * f / 2) * 2), max(2, round(sh * f / 2) * 2)


def build_pipeline(src: str, width: int, height: int, fps: int, quality: int,
                   scale: str = "bilinear", keep_aspect: bool = True, codec: str = "jpeg",
                   gpu_from=None) -> str:
    """gpu_from = (largura, altura) da fonte: recebe DMA-BUF e reduz no OpenGL
    (só 480x272 chega à CPU); None = tudo na CPU."""
    # h264: o pipeline entrega I420 cru e o H264Encoder (h264.py) codifica,
    # porque o QP do openh264enc não muda com o pipeline rodando.
    enc = f"! jpegenc name=enc quality={quality} " if codec == "jpeg" else ""
    head, gpu = f"{src} ", ""
    if gpu_from:
        gw, gh = gpu_size(gpu_from, width, height, keep_aspect)
        head += '! capsfilter caps="video/x-raw(memory:DMABuf)" '
        gpu = ("! glupload ! glcolorconvert ! glcolorscale "
               f"! video/x-raw(memory:GLMemory),format=RGBA,width={gw},height={gh} ! gldownload ")
    return (
        head +
        "! queue name=q leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        f"{gpu}"
        f"! videoscale method={scale} n-threads={SCALE_THREADS} add-borders={'true' if keep_aspect else 'false'} "
        f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
        "! videoconvert ! video/x-raw,format=I420 "
        f"{enc}"
        "! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
    )


# Fontes prontas. {w}/{h} são do frame de teste, não da saída.
SOURCES = {
    # Padrão animado + relógio na tela (útil para medir latência filmando).
    # 720p: em 1080p o timeoverlay sozinho pode não sustentar 60 fps e o atraso acumula.
    "test": ("videotestsrc is-live=true pattern=ball ! video/x-raw,width=1280,height=720,framerate=60/1 "
             "! timeoverlay font-desc=\"Sans 48\" halignment=center valignment=center"),
    # Sessão X11 (no Wayland use "portal").
    "x11": "ximagesrc use-damage=false show-pointer=true ! video/x-raw,framerate=60/1",
}


class RateLimiter:
    """Até `fps` frames por segundo pelo pts, numa grade fixa: um frame passa
    se chega até meio período antes da vez dele (meio período da fonte, se
    ela for mais rápida), e a vez seguinte conta da vez, não do frame. Um
    frame atrasado não empurra a grade, e o seguinte, no horário, não é
    cortado. O limite anterior contava a vez seguinte do frame, com 25% de
    tolerância: com os horários tremidos em 2-3 ms (jogo, compositor), uma
    fonte de 60 Hz virava 55-59 fps com --fps 60 (o padrão) e 38-39 com
    --fps 40; 75 Hz com --fps 60 dava 56, e 60 Hz com --fps 50 dava 46-48.
    A grade recomeça quando fica para trás (fonte parada ou mais lenta)."""

    def __init__(self, fps: float):
        self.period = int(Gst.SECOND / max(1.0, fps))
        self.next = None
        self.last = None
        self.intervals = collections.deque(maxlen=16)  # entre frames da fonte (ns)

    def keep(self, pts: int) -> bool:
        if pts == Gst.CLOCK_TIME_NONE:
            return True
        if self.last is not None and 0 < pts - self.last < Gst.SECOND // 10:
            self.intervals.append(pts - self.last)
        self.last = pts
        if self.next is None or pts < self.next - 2 * self.period:  # início, ou o pts voltou
            self.next = pts + self.period
            return True
        src = sorted(self.intervals)[len(self.intervals) // 2] if self.intervals else self.period
        if pts < self.next - min(self.period, src) // 2:
            return False
        self.next += self.period
        if self.next <= pts:  # a grade ficou para trás: recomeça deste frame
            self.next = pts + self.period
        return True


class CaptureMeter:
    """Quantos frames a fonte entrega e quantos seguem, para achar onde a taxa cai."""

    def __init__(self):
        self.lock = threading.Lock()
        self.arrived = 0
        self.kept = 0
        self.last = None
        self.intervals = collections.deque(maxlen=240)  # ms entre frames da fonte

    def on_arrival(self) -> None:
        now = time.monotonic()
        with self.lock:
            self.arrived += 1
            if self.last is not None:
                self.intervals.append((now - self.last) * 1000)
            self.last = now

    def on_kept(self) -> None:
        with self.lock:
            self.kept += 1

    def snapshot(self):
        with self.lock:
            iv = sorted(self.intervals)
            return self.arrived, self.kept, iv


class GstSource(FrameSource):
    def __init__(self, src: str, width: int, height: int, fps: int, quality: int,
                 scale: str = "bilinear", keep_aspect: bool = True, keepalive=None, codec: str = "jpeg",
                 gpu_from=None):
        super().__init__()
        self.keepalive = keepalive  # objeto que precisa viver junto (ex.: sessão do portal)
        self._quality = quality
        self.raw_i420 = codec == "h264p"  # a sessão codifica na hora de enviar
        self.h264 = None
        if codec == "h264":
            from h264 import H264Encoder
            self.h264 = H264Encoder(width, height, quality)
        self.gpu = bool(gpu_from)
        desc = build_pipeline(src, width, height, fps, quality, scale, keep_aspect, codec, gpu_from)
        log.debug("pipeline: %s", desc)
        self.pipeline = Gst.parse_launch(desc)
        self.enc = self.pipeline.get_by_name("enc")
        sink = self.pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        self.failed = None
        self._stop = threading.Event()
        self.fps = fps
        self._limiter = RateLimiter(fps)
        self.meter = CaptureMeter()
        queue = self.pipeline.get_by_name("q")
        self._queue_in = queue.get_static_pad("sink")
        self._queue_in.add_probe(Gst.PadProbeType.BUFFER, self._probe_arrival)
        queue.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._probe_limit)
        self._report_at = None  # primeiro relatório da captura 5 s depois do primeiro frame
        self._report_base = None
        self._caps_logged = False

    def _probe_arrival(self, pad, info):
        self.meter.on_arrival()
        return Gst.PadProbeReturn.OK

    def _probe_limit(self, pad, info):
        if not self._limiter.keep(info.get_buffer().pts):
            return Gst.PadProbeReturn.DROP
        self.meter.on_kept()
        return Gst.PadProbeReturn.OK

    def _maybe_report(self) -> None:
        """Log da taxa da captura: 5 s depois do primeiro frame e depois a cada 60 s."""
        now = time.monotonic()
        arrived, kept, iv = self.meter.snapshot()
        if self._report_at is None:
            if arrived:
                self._report_at = now + 5
                self._report_base = (now, arrived, kept, self.latest()[0])
            return
        if now < self._report_at:
            return
        t0, a0, k0, p0 = self._report_base
        dt = max(1e-6, now - t0)
        if not self._caps_logged:
            self._caps_logged = True
            caps = self._queue_in.get_current_caps()
            if caps is not None:
                log.info("formato da captura: %s", caps.to_string())
        if iv:
            spread = (f"intervalo mediano {statistics.median(iv):.1f} ms, "
                      f"p10 {iv[len(iv) // 10]:.1f}, p90 {iv[len(iv) * 9 // 10]:.1f}")
        else:
            spread = "sem intervalos"
        log.info("captura: a fonte entrega %.1f fps (%s); passam pelo limite de %d fps: %.1f; "
                 "codificados: %.1f", (arrived - a0) / dt, spread, self.fps, (kept - k0) / dt,
                 (self.latest()[0] - p0) / dt)
        self._report_at = now + 60
        self._report_base = (now, arrived, kept, self.latest()[0])

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        # Fontes ao vivo carimbam o buffer na captura (running time): a
        # diferença para o relógio agora = captura + escala + encode.
        capture_ms = None
        clock = self.pipeline.get_clock()
        if clock is not None and buf.pts != Gst.CLOCK_TIME_NONE:
            running = clock.get_time() - self.pipeline.get_base_time()
            capture_ms = (running - buf.pts) / Gst.MSECOND
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                data = bytes(info.data)
            finally:
                buf.unmap(info)
            if self.h264 is not None:
                data = self.h264.encode(data)
                if data is None:
                    log.warning("o encoder H.264 não devolveu o frame")
                    return Gst.FlowReturn.OK
                if capture_ms is not None and clock is not None:
                    capture_ms = (clock.get_time() - self.pipeline.get_base_time() - buf.pts) / Gst.MSECOND
            self.publish(data, capture_ms)
        return Gst.FlowReturn.OK

    def _watch_bus(self):
        bus = self.pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
        while not self._stop.is_set():
            msg = bus.timed_pop_filtered(100 * Gst.MSECOND, mask)
            if msg is None:
                self._maybe_report()
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
        if self.h264 is not None:
            self.h264.close()

    def set_fps(self, fps: int) -> None:
        """Limite de FPS novo com o pipeline rodando (interface web)."""
        self.fps = fps
        self._limiter = RateLimiter(fps)

    def set_quality(self, quality: int) -> None:
        # jpegenc aceita mudar a qualidade com o pipeline rodando; o H.264 troca o QP no próximo frame.
        quality = max(1, min(100, int(quality)))
        if quality != self._quality:
            if self.h264 is not None:
                self.h264.set_quality(quality)
            elif self.enc is not None:
                self.enc.set_property("quality", quality)
            self._quality = quality  # h264p (cru): quem lê é o encoder da sessão

    @property
    def quality(self):
        return self._quality


def transcode_image(path: str, width: int, height: int, quality: int, keep_aspect: bool = True,
                    scale: str = "bilinear") -> bytes:
    """Converte qualquer imagem (PNG, JPEG grande...) num JPEG 4:2:0 de até width x height."""
    src = f"filesrc location=\"{path}\" ! decodebin ! imagefreeze num-buffers=1 ! videoconvert"
    desc = (
        f"{src} ! videoscale method={scale} add-borders={'true' if keep_aspect else 'false'} "
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
