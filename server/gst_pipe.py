"""Captura pelo gst-launch-1.0 num processo à parte: a do servidor de Windows.

No Windows, o PyGObject só existe no Python do MSYS2; o GStreamer oficial
(MSVC) e o Python oficial não se conversam em processo. Então a captura roda
no gst-launch-1.0, e o servidor lê o resultado por TCP local:

  vídeo:  d3d11screencapturesrc ! ... ! I420 480x272 ! tcpclientsink
          -> frames de tamanho fixo (w*h*3/2 bytes), sem enquadramento
  som:    wasapi2src loopback=true ! ... ! adpcmenc blockalign=N ! tcpclientsink
          -> blocos IMA de N bytes

Daí em diante é igual à captura em processo: o I420 vai para o encoder da
sessão (frames P), para o H264Encoder (todo frame IDR) ou para o JPEG do
Pillow (imaging.py), e os blocos de som para os ouvintes da sessão UDP.

Por que TCP com ACK imediato: o gst-launch escreve cada frame de ~190 KB
de uma vez, e o último pedaço esperava o ACK atrasado do receptor (Nagle):
medido no localhost do Linux, travadas de até 150 ms entre frames de 16,7 ms.
Com o receptor confirmando na hora (TCP_QUICKACK no Linux,
SIO_TCP_SET_ACK_FREQUENCY no Windows), p99 de 17-19 ms. A saída padrão
(fdsink) não tem esse problema, mas no Windows ela pode estar em modo texto
(o \\n vira \\r\\n e os frames desalinham).

Também roda no Linux (testes, --source test com --capture pipe).
"""
import collections
import functools
import logging
import os
import shlex
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from framerate import CaptureMeter, RateLimiter
from sources import FrameSource
from i18n import tr

log = logging.getLogger("pspstream.pipe")

WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""
FIRST_FRAME_S = 8.0      # cada pipeline candidato tem esse tempo para mandar o primeiro frame
CONNECT_S = 20.0         # o gst-launch conecta depois de montar o pipeline (o 1º uso monta o registro)
STDERR_LINES = 30


@dataclass(eq=False)  # identidade: serve de chave do lru_cache
class GStreamer:
    launch: Path              # gst-launch-1.0
    inspect: Path             # gst-inspect-1.0
    bin: Path
    env: dict = field(repr=False, default_factory=dict)
    where: str = ""           # de onde veio (log e --check)

    @functools.cached_property
    def version(self):
        """(major, minor, micro) ou None."""
        try:
            out = subprocess.run([str(self.launch), "--version"], capture_output=True, text=True, timeout=30,
                                 env=self.env, **_child_flags()).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            if line.startswith("GStreamer "):
                try:
                    return tuple(int(x) for x in line.split()[1].split(".")[:3])
                except ValueError:
                    return None
        return None

    def has(self, element: str) -> bool:
        return _has_element(self, element)


@functools.lru_cache(maxsize=None)
def _has_element(gst: GStreamer, element: str) -> bool:
    try:
        return subprocess.run([str(gst.inspect), "--exists", element], capture_output=True, timeout=60,
                              env=gst.env, **_child_flags()).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False



def _app_dir() -> Path:
    """A pasta do pspstream.exe (PyInstaller) ou a raiz do repositório."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _from_root(root: Path, where: str):
    root = Path(root)
    bin_dir = root / "bin"
    launch = bin_dir / f"gst-launch-1.0{EXE}"
    if not launch.is_file():
        return None
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    # Só os plugins desta instalação (um GST_PLUGIN_PATH de outra versão misturaria as duas).
    for var in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0"):
        env.pop(var, None)
    plugins = root / "lib" / "gstreamer-1.0"
    if plugins.is_dir():
        env["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(plugins)
    scanner = root / "libexec" / "gstreamer-1.0" / f"gst-plugin-scanner{EXE}"
    if scanner.is_file():
        env["GST_PLUGIN_SCANNER_1_0"] = str(scanner)
    if WINDOWS:
        cache = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PSPStream"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            env["GST_REGISTRY_1_0"] = str(cache / f"gst-registry-{abs(hash(str(root))) % 10 ** 8}.bin")
        except OSError:
            pass
    return GStreamer(launch, bin_dir / f"gst-inspect-1.0{EXE}", bin_dir, env, where)


def candidate_roots():
    """(raiz, origem) na ordem de procura."""
    env = os.environ.get("PSPSTREAM_GSTREAMER")
    if env:
        yield Path(env), "PSPSTREAM_GSTREAMER"
    yield _app_dir() / "gstreamer", tr("bundled")
    if WINDOWS:
        for var in ("GSTREAMER_1_0_ROOT_MSVC_X86_64", "GSTREAMER_1_0_ROOT_X86_64"):
            if os.environ.get(var):
                yield Path(os.environ[var]), var
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), "C:\\"):
            yield Path(base) / "gstreamer" / "1.0" / "msvc_x86_64", tr("official installer")


@functools.lru_cache(maxsize=1)
def find_gstreamer():
    """O GStreamer para o gst-launch, ou None."""
    for root, where in candidate_roots():
        gst = _from_root(root, where)
        if gst is not None:
            return gst
    launch = shutil.which("gst-launch-1.0")
    if launch:
        launch = Path(launch)
        inspect = shutil.which("gst-inspect-1.0") or launch.with_name(f"gst-inspect-1.0{EXE}")
        return GStreamer(launch, Path(inspect), launch.parent, dict(os.environ), "PATH")
    return None


def need_gstreamer() -> GStreamer:
    gst = find_gstreamer()
    if gst is None:
        raise RuntimeError(tr("GStreamer not found: install the GStreamer runtime (MSVC 64-bit) from "
                              "gstreamer.freedesktop.org, or use the PSPStream build that bundles it"))
    return gst


def _child_flags() -> dict:
    """Windows: o processo filho sem janela de console própria."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}


class _Job:
    """Windows: o gst-launch morre junto com o servidor, mesmo se ele cair
    (Job Object com KILL_ON_JOB_CLOSE). Em outros sistemas, nada."""

    def __init__(self):
        self.handle = None
        if not WINDOWS:
            return
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended
            self.handle, self._k32 = handle, k32

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle:
            self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))


_JOB = None


def _job():
    global _JOB
    if _JOB is None:
        try:
            _JOB = _Job()
        except Exception:  # sem o Job Object, o stop() ainda mata o processo
            _JOB = False
    return _JOB or None


def quick_ack(sock: socket.socket) -> None:
    """O receptor confirma cada segmento na hora (ver a docstring do módulo).
    No Linux, o TCP_QUICKACK vale até a próxima leitura: chamar depois de cada recv."""
    if WINDOWS:
        try:
            import ctypes
            ws2 = ctypes.WinDLL("ws2_32")
            ws2.WSAIoctl.argtypes = [ctypes.c_size_t, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                     ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.c_void_p, ctypes.c_void_p]
            freq, ret = ctypes.c_ulong(1), ctypes.c_ulong(0)
            ws2.WSAIoctl(sock.fileno(), 0x98000017, ctypes.byref(freq), 4, None, 0,  # SIO_TCP_SET_ACK_FREQUENCY
                         ctypes.byref(ret), None, None)
        except (OSError, AttributeError):
            pass
    elif hasattr(socket, "TCP_QUICKACK"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
        except OSError:
            pass


class Pipe:
    """Um gst-launch-1.0 mandando registros de tamanho fixo por TCP local."""

    def __init__(self, gst: GStreamer, elements: str, record: int):
        self.gst = gst
        self.record = record
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.desc = f"{elements} ! tcpclientsink host=127.0.0.1 port={self.port} sync=false"
        self.stderr = collections.deque(maxlen=STDERR_LINES)
        self.conn = None
        self.proc = None

    def start(self) -> None:
        log.debug("gst-launch: %s", self.desc)
        # Um elemento por argumento, como no terminal: o gst-launch junta e escapa cada um.
        self.proc = subprocess.Popen([str(self.gst.launch), "-q", *shlex.split(self.desc)], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self.gst.env,
                                     **_child_flags())
        job = _job()
        if job is not None:
            job.add(self.proc)
        threading.Thread(target=self._read_stderr, name="gst-stderr", daemon=True).start()
        self.listener.settimeout(0.2)
        deadline = time.monotonic() + CONNECT_S
        while self.conn is None:
            try:
                self.conn, _ = self.listener.accept()
            except socket.timeout:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    reason = self.reason() or tr("gst-launch did not connect")
                    self.close()
                    raise RuntimeError(reason)
        self.listener.close()
        self.conn.setblocking(True)
        self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, max(1 << 20, 4 * self.record))
        quick_ack(self.conn)

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr.append(line)
                log.debug("gst-launch: %s", line)

    def reason(self) -> str:
        """O erro do gst-launch (as últimas linhas da saída de erro)."""
        lines = [line for line in self.stderr if line.strip()]
        errors = [line for line in lines if "ERROR" in line or "erro" in line.lower() or "WARNING" in line]
        return " | ".join((errors or lines)[-3:])

    def read(self, buf: bytearray) -> bool:
        """Lê um registro inteiro em buf; False no fim (o processo saiu)."""
        view = memoryview(buf)
        got = 0
        while got < self.record:
            try:
                n = self.conn.recv_into(view[got:], self.record - got)
            except OSError:
                return False
            if not n:
                return False
            got += n
            if not WINDOWS:
                quick_ack(self.conn)
        return True

    def close(self) -> None:
        for s in (self.conn, self.listener):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def cpu_tail(width: int, height: int, scale: str, keep_aspect: bool) -> str:
    """Fila de 1 frame + redução e conversão na CPU, como o build_pipeline do gst_source."""
    return ("queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
            f"! videoscale method={scale} add-borders={'true' if keep_aspect else 'false'} "
            f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
            "! videoconvert ! video/x-raw,format=I420")


def screen_candidates(monitor: int, cursor: bool, fps: int, width: int, height: int, scale: str,
                      keep_aspect: bool):
    """Windows: a tela pelo Desktop Duplication (d3d11screencapturesrc). Primeiro a
    redução na GPU (só 480x272 vem para a CPU), depois na CPU."""
    src = f"d3d11screencapturesrc monitor-index={monitor} show-cursor={'true' if cursor else 'false'}"
    borders = "true" if keep_aspect else "false"
    gpu = (f"{src} ! video/x-raw(memory:D3D11Memory),framerate={fps}/1 "
           "! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
           f"! d3d11convert add-borders={borders} "
           f"! video/x-raw(memory:D3D11Memory),format=I420,width={width},height={height},pixel-aspect-ratio=1/1 "
           f"! d3d11download ! video/x-raw,format=I420,width={width},height={height}")
    cpu = f"{src} ! video/x-raw,framerate={fps}/1 ! {cpu_tail(width, height, scale, keep_aspect)}"
    download = (f"{src} ! video/x-raw(memory:D3D11Memory),framerate={fps}/1 ! d3d11download "
                f"! {cpu_tail(width, height, scale, keep_aspect)}")
    return [("GPU (d3d11convert)", gpu), ("CPU", cpu), ("CPU (d3d11download)", download)]


def test_candidates(fps: int, width: int, height: int, scale: str, keep_aspect: bool):
    src = f"videotestsrc is-live=true pattern=ball ! video/x-raw,width=1280,height=720,framerate={fps}/1"
    return [("test", f"{src} ! {cpu_tail(width, height, scale, keep_aspect)}")]


def custom_candidates(elements: str, width: int, height: int, scale: str, keep_aspect: bool):
    return [("gst", f"{elements} ! {cpu_tail(width, height, scale, keep_aspect)}")]


class PipeSource(FrameSource):
    """Frames I420 do gst-launch. h264p: a sessão codifica (raw_i420); h264: todo frame IDR aqui;
    jpeg: Pillow, na qualidade atual."""

    def __init__(self, candidates, width: int, height: int, fps: int, quality: int, codec: str = "h264p",
                 gst: GStreamer = None, label: str = ""):
        super().__init__()
        self.gst = gst or need_gstreamer()
        self.candidates = list(candidates)
        self.width, self.height = width, height
        self.frame_size = width * height * 3 // 2
        self.fps = fps
        self._quality = quality
        self.codec = codec
        self.raw_i420 = codec == "h264p"
        self.label = label
        self.h264 = None
        if codec == "h264":
            from h264 import H264Encoder
            self.h264 = H264Encoder(width, height, quality)
        elif codec == "jpeg":
            import imaging
            if not imaging.available():
                raise RuntimeError(tr("Pillow is missing (pip install pillow)"))
        self.pipe = None
        self.mode = None
        self.failed = None
        self.keepalive = None
        self._stop = threading.Event()
        self._limiter = RateLimiter(fps)
        self.meter = CaptureMeter()
        self._report_at = None
        self._report_base = None

    def start(self) -> None:
        reasons = []
        for name, elements in self.candidates:
            pipe = Pipe(self.gst, elements, self.frame_size)
            try:
                pipe.start()
            except RuntimeError as exc:
                reasons.append(f"{name}: {exc}")
                continue
            buf = bytearray(self.frame_size)
            ok = [False]

            def first():
                ok[0] = pipe.read(buf)

            t = threading.Thread(target=first, daemon=True)
            t.start()
            t.join(FIRST_FRAME_S)
            if not ok[0]:
                reasons.append(f"{name}: {pipe.reason() or tr('no frame in {seconds} s').format(seconds=FIRST_FRAME_S)}")
                pipe.close()
                t.join(1)
                continue
            self.pipe, self.mode = pipe, name
            if len(self.candidates) > 1:
                log.info(tr("capture: %s"), f"{self.label} ({name})" if self.label else name)
            self._handle(bytes(buf))
            threading.Thread(target=self._loop, name="pipe-video", daemon=True).start()
            return
        raise RuntimeError("; ".join(reasons) or tr("no pipeline to try"))

    def _loop(self) -> None:
        buf = bytearray(self.frame_size)
        while not self._stop.is_set():
            if not self.pipe.read(buf):
                if not self._stop.is_set():
                    self.failed = self.pipe.reason() or tr("end of stream (EOS)")
                    log.error("GStreamer: %s", self.failed)
                return
            self.meter.on_arrival()
            if not self._limiter.keep(time.monotonic_ns()):
                continue
            self.meter.on_kept()
            self._handle(bytes(buf))
            self._maybe_report()

    def _handle(self, i420: bytes) -> None:
        t0 = time.monotonic()
        if self.raw_i420:
            data = i420
        elif self.h264 is not None:
            data = self.h264.encode(i420)
            if data is None:
                return
        else:
            import imaging
            data = imaging.i420_to_jpeg(i420, self.width, self.height, self._quality)
        self.publish(data, (time.monotonic() - t0) * 1000)

    def _maybe_report(self) -> None:
        """Log da taxa da captura: 5 s depois do primeiro frame e depois a cada 60 s (como a GstSource)."""
        now = time.monotonic()
        if self._report_at is not None and now < self._report_at:
            return  # roda a cada frame: a cópia ordenada dos intervalos (snapshot) só na hora do log
        arrived, kept, iv = self.meter.snapshot()
        if self._report_at is None:
            self._report_at = now + 5
            self._report_base = (now, arrived, kept, self.latest()[0])
            return
        if now < self._report_at:
            return
        t0, a0, k0, p0 = self._report_base
        dt = max(1e-6, now - t0)
        if iv:
            spread = tr("median interval {median:.1f} ms, p10 {p10:.1f}, p90 {p90:.1f}").format(
                median=statistics.median(iv), p10=iv[len(iv) // 10], p90=iv[len(iv) * 9 // 10])
        else:
            spread = tr("no intervals")
        log.info(tr("capture: the source delivers %.1f fps (%s); through the %d fps limit: %.1f; "
                    "encoded: %.1f"), (arrived - a0) / dt, spread, self.fps, (kept - k0) / dt,
                 (self.latest()[0] - p0) / dt)
        self._report_at = now + 60
        self._report_base = (now, arrived, kept, self.latest()[0])

    def stop(self) -> None:
        self._stop.set()
        if self.pipe is not None:
            self.pipe.close()
        if self.h264 is not None:
            self.h264.close()

    def set_fps(self, fps: int) -> None:
        self.fps = fps
        self._limiter = RateLimiter(fps)

    def set_quality(self, quality: int) -> None:
        quality = max(1, min(100, int(quality)))
        if quality != self._quality:
            if self.h264 is not None:
                self.h264.set_quality(quality)
            self._quality = quality

    @property
    def quality(self):
        return self._quality


def audio_src(device: str) -> str:
    """A fonte do som no gst-launch. Windows: monitor = o que sai nas caixas (WASAPI loopback)."""
    if device == "test":
        return "audiotestsrc is-live=true wave=sine freq=440 volume=0.3"
    if WINDOWS:
        if device != "monitor":
            raise RuntimeError(tr("on Windows, the audio source is monitor (what plays on the speakers) or test"))
        return "wasapi2src loopback=true low-latency=true"
    from audio import default_monitor
    name = default_monitor() if device == "monitor" else device
    return f'pulsesrc device="{name}" client-name=PSPStream buffer-time=40000 latency-time=10000'


class PipeAudioCapture:
    """Como a audio.AudioCapture, com o adpcmenc no gst-launch: cada bloco vai para os
    ouvintes, listener(seq, pos, taxa, canais, amostras, bloco)."""

    def __init__(self, device: str = "monitor", rate: int = 44100, channels: int = 2, packet_ms: float = 20,
                 gst: GStreamer = None):
        from audio import MAX_BLOCK, block_align, block_samples
        self.gst = gst or need_gstreamer()
        self.device = device
        self.rate, self.channels = rate, channels
        self.samples = block_samples(rate, packet_ms)
        self.align = block_align(self.samples, channels)
        if self.align > MAX_BLOCK:
            raise ValueError(tr("a {size}-byte block does not fit in a packet: lower --audio-ms").format(size=self.align))
        self.desc = (f"{audio_src(device)} ! audioconvert ! audioresample "
                     f"! audio/x-raw,format=S16LE,layout=interleaved,rate={rate},channels={channels} "
                     f"! adpcmenc layout=dvi blockalign={self.align}")
        self.listeners = []
        self.lock = threading.Lock()
        self.seq = 0
        self.pos = 0
        self.failed = None
        self.pipe = None
        self._stop = threading.Event()

    @property
    def packet_ms(self) -> float:
        return self.samples * 1000 / self.rate

    @property
    def kbps(self) -> float:
        return (self.align + 48) * self.rate / self.samples / 1024

    def add_listener(self, fn) -> None:
        with self.lock:
            self.listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self.lock:
            if fn in self.listeners:
                self.listeners.remove(fn)

    def start(self) -> None:
        self.pipe = Pipe(self.gst, self.desc, self.align)
        try:
            self.pipe.start()
        except RuntimeError as exc:
            raise RuntimeError(tr("could not capture the audio from '{device}'").format(device=self.device)
                               + f": {exc}") from None
        threading.Thread(target=self._loop, name="pipe-audio", daemon=True).start()

    def _loop(self) -> None:
        buf = bytearray(self.align)
        while not self._stop.is_set():
            if not self.pipe.read(buf):
                if not self._stop.is_set():
                    self.failed = self.pipe.reason() or tr("end of stream (EOS)")
                    log.warning(tr("audio stopped: %s (video goes on)"), self.failed)
                return
            block = bytes(buf)
            self.seq += 1
            with self.lock:
                listeners = list(self.listeners)
            for fn in listeners:
                try:
                    fn(self.seq, self.pos, self.rate, self.channels, self.samples, block)
                except OSError:
                    pass
            self.pos += self.samples

    def stop(self) -> None:
        self._stop.set()
        if self.pipe is not None:
            self.pipe.close()


def audio_available(gst: GStreamer = None) -> bool:
    gst = gst or find_gstreamer()
    if gst is None:
        return False
    need = ["adpcmenc", "audioresample", "audioconvert", "tcpclientsink"]
    need.append("wasapi2src" if WINDOWS else "pulsesrc")
    return all(gst.has(e) for e in need)
