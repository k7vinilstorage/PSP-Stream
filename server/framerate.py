"""Limite de FPS e medição da taxa da captura, sem depender do GStreamer em
processo: servem à captura pelo PyGObject (gst_source.py) e à do gst-launch
num processo à parte (gst_pipe.py, o servidor de Windows)."""
import collections
import threading
import time

SECOND = 1_000_000_000          # ns, como o Gst.SECOND
CLOCK_TIME_NONE = 2 ** 64 - 1   # como o Gst.CLOCK_TIME_NONE


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
        self.period = int(SECOND / max(1.0, fps))
        self.next = None
        self.last = None
        self.intervals = collections.deque(maxlen=16)  # entre frames da fonte (ns)

    def keep(self, pts: int) -> bool:
        if pts == CLOCK_TIME_NONE:
            return True
        if self.last is not None and 0 < pts - self.last < SECOND // 10:
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
