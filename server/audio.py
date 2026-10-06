"""Som do PC para o PSP: captura, IMA ADPCM e pacotes UDP.

Pipeline (GStreamer, em processo):

  pulsesrc (monitor da saída padrão do PipeWire/PulseAudio) ! audioconvert
          ! audioresample ! S16LE, 44,1 kHz, estéreo ! adpcmenc (IMA/DVI)
          ! appsink

IMA ADPCM: 4 bits por amostra (44,1 kHz estéreo = 353 kbit/s, ~46 KB/s
com os cabeçalhos), codificado em C pelo adpcmenc. No PSP, decodificar é umas
poucas somas por amostra no CPU, sem o Media Engine (que é do H.264). Cada
bloco começa com a amostra e o passo do preditor, então decodifica sozinho:
um pacote perdido vira só aquele pedaço de silêncio. MP3 ou ATRAC pesariam
menos na rede, mas somariam 50-100 ms de atraso (quadros longos e o atraso
do encoder) e disputariam o Media Engine com o decode do vídeo.

O som é empurrado: um pacote (MAGIC_AUDIO) a cada ~20 ms, sem pedido, para
o endereço da sessão UDP, só se o PSP avisa que toca (CAP_AUDIO). Pacotes
de 20 ms: com 10 ms seriam 100 pacotes/s, e no 802.11b cada pacote custa
~1 ms de ar, disputado com o vídeo.
"""
import logging
import shutil
import subprocess
import threading

from i18n import tr

try:  # sem o PyGObject (servidor de Windows): os blocos e a referência do IMA servem; a captura é a do gst_pipe
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
except (ImportError, ValueError):
    Gst = None

log = logging.getLogger("pspstream.audio")

RATES = (22050, 32000, 44100, 48000)  # o sceAudioSRC do PSP aceita estas (e outras menores)
# 44,1 kHz é a taxa do hardware do PSP: o sceAudioSRC não reamostra (a
# conversão fica no audioresample do PC, de boa qualidade). Numa música de
# jogo, 0,3-1,8 dB a mais de fidelidade que 32 kHz, e agudos até 22 kHz.
DEFAULT_RATE = 44100
PACKET_MS = 20
MAX_BLOCK = 1400  # um pacote UDP com o cabeçalho cabe em 1500 bytes

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
    4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
    22385, 24623, 27086, 29794, 32767,
)
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


def block_samples(rate: int, packet_ms: float = PACKET_MS) -> int:
    """Amostras por canal num bloco IMA de ~packet_ms: 1 (cabeçalho) + múltiplo de 8."""
    return 8 * max(1, round(rate * packet_ms / 1000 / 8)) + 1


def block_align(samples: int, channels: int) -> int:
    """Bytes do bloco: 4 de cabeçalho por canal + 4 bits por amostra depois da 1ª."""
    return 4 * channels + (samples - 1) // 2 * channels


def ima_decode_block(block: bytes, channels: int) -> list:
    """Referência em Python (o PSP faz o mesmo em C, psp/src/audio.c): bloco
    IMA ADPCM do WAV -> amostras S16 intercaladas. Para testes e o fake_client."""
    pred, index = [], []
    for c in range(channels):
        h = block[4 * c:4 * c + 4]
        pred.append(int.from_bytes(h[0:2], "little", signed=True))
        index.append(min(88, h[2]))
    out = [[p] for p in pred]
    data = block[4 * channels:]
    # estéreo: 4 bytes (8 amostras) do canal 0, 4 do canal 1, e assim por diante
    for g in range(0, len(data), 4 * channels):
        for c in range(channels):
            for byte in data[g + 4 * c:g + 4 * c + 4]:
                for nib in (byte & 0x0F, byte >> 4):
                    step = STEP_TABLE[index[c]]
                    diff = step >> 3
                    if nib & 4:
                        diff += step
                    if nib & 2:
                        diff += step >> 1
                    if nib & 1:
                        diff += step >> 2
                    p = pred[c] - diff if nib & 8 else pred[c] + diff
                    pred[c] = max(-32768, min(32767, p))
                    index[c] = max(0, min(88, index[c] + INDEX_TABLE[nib]))
                    out[c].append(pred[c])
    n = min(len(ch) for ch in out)
    return [out[c][i] for i in range(n) for c in range(channels)]


def default_monitor() -> str:
    """Monitor da saída padrão (o som que vai para as caixas). pactl existe
    com PipeWire (pipewire-pulse) e com o PulseAudio; sem ele, o nome especial."""
    if shutil.which("pactl"):
        try:
            sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True,
                                  timeout=3).stdout.strip()
            if sink:
                return sink + ".monitor"
        except (OSError, subprocess.SubprocessError):
            pass
    return "@DEFAULT_MONITOR@"


def available(pulse: bool = True) -> bool:
    """pulse=False: o som do Wolf, que chega por TCP (sem o pulsesrc)."""
    if Gst is None:
        return False
    need = ("pulsesrc", "adpcmenc", "audioresample") if pulse else ("adpcmenc", "audioresample", "tcpserversrc")
    return all(Gst.ElementFactory.find(e) for e in need)


def build_pipeline(device: str, rate: int, channels: int, align: int, src=None) -> str:
    """src: elementos da fonte no lugar do pulsesrc (o som do Wolf chega por tcpserversrc ! gdpdepay)."""
    if not src and device == "test":  # tom de 440 Hz (testes, sem PipeWire)
        src = "audiotestsrc is-live=true wave=sine freq=440 volume=0.3"
    elif not src:
        # latency-time = tamanho de cada leitura (us): 10 ms, menos que o bloco de 20 ms
        src = f'pulsesrc device="{device}" client-name=PSPStream buffer-time=40000 latency-time=10000'
    return (
        f"{src} ! audioconvert ! audioresample "
        f"! audio/x-raw,format=S16LE,layout=interleaved,rate={rate},channels={channels} "
        f"! adpcmenc layout=dvi blockalign={align} "
        "! appsink name=sink emit-signals=true sync=false max-buffers=25 drop=true"
    )


class AudioCapture:
    """Captura contínua; cada bloco vai para os ouvintes (a sessão UDP ativa)
    na thread do GStreamer: listener(seq, pos, taxa, canais, amostras, bloco)."""

    def __init__(self, device: str = "monitor", rate: int = DEFAULT_RATE, channels: int = 2,
                 packet_ms: float = PACKET_MS, src=None):
        self.device = default_monitor() if device == "monitor" and not src else device
        self.rate, self.channels = rate, channels
        self.samples = block_samples(rate, packet_ms)
        self.align = block_align(self.samples, channels)
        if self.align > MAX_BLOCK:
            raise ValueError(tr("a {size}-byte block does not fit in a packet: lower --audio-ms").format(size=self.align))
        self.pipeline = Gst.parse_launch(build_pipeline(self.device, rate, channels, self.align, src))
        self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        self.listeners = []
        self.lock = threading.Lock()
        self.seq = 0
        self.pos = 0
        self.failed = None
        self._stop = threading.Event()
        self._pending = b""

    @property
    def packet_ms(self) -> float:
        return self.samples * 1000 / self.rate

    @property
    def kbps(self) -> float:
        """KB/s na rede, com o cabeçalho do pacote (20 bytes) e o do UDP/IP (28)."""
        return (self.align + 48) * self.rate / self.samples / 1024

    def add_listener(self, fn) -> None:
        with self.lock:
            self.listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self.lock:
            if fn in self.listeners:
                self.listeners.remove(fn)

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = self._pending + bytes(info.data)
        finally:
            buf.unmap(info)
        n = len(data) // self.align * self.align
        self._pending = data[n:]  # o adpcmenc solta um bloco por buffer; isto é só por garantia
        with self.lock:
            listeners = list(self.listeners)
        for i in range(0, n, self.align):
            block = data[i:i + self.align]
            self.seq += 1
            for fn in listeners:
                try:
                    fn(self.seq, self.pos, self.rate, self.channels, self.samples, block)
                except OSError:
                    pass  # envio falhou (rede); a sessão percebe por conta própria
            self.pos += self.samples
        return Gst.FlowReturn.OK

    def _watch_bus(self):
        bus = self.pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS
        while not self._stop.is_set():
            msg = bus.timed_pop_filtered(200 * Gst.MSECOND, mask)
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                self._ended(f"{err.message} ({dbg})")
            else:
                self._ended(tr("end of stream (EOS)"))
            return

    def _ended(self, reason: str) -> None:
        """A captura parou (erro ou EOS). A do Wolf trata o fim esperado sem o aviso."""
        self.failed = reason
        log.warning(tr("audio stopped: %s (video goes on)"), reason)

    def start(self) -> None:
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(tr("could not capture the audio from '{device}'").format(device=self.device))
        threading.Thread(target=self._watch_bus, name="audio-bus", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.pipeline.set_state(Gst.State.NULL)
