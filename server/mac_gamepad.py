"""Virtual gamepad output on macOS via shared memory (CrossOver) and USB HID.

GamepadInjector (gamepad.py) computes the mapped Xbox 360 state from the xbox,
xbox-camera, and xbox-shoulders profiles (including the SELECT shift layer);
this module packs that state into the 8-byte HID gamepad report expected by
Interface 1 (HID, EP 0x83) of the PSP's USB driver and mirrors it into both:
  1. Shared memory (/tmp/pspstream_pad.shm) read by the CrossOver SDL2 proxy
     (tools/crossover/libSDL2-2.0.0.dylib) over Wi-Fi and USB.
  2. USB EP0 vendor control transfers via usb_link.send_hid_report() whenever
     the PSP is connected over the USB cable (system-wide native macOS HID).
"""
import logging
import mmap
import os
from pathlib import Path
import shutil
import struct
from typing import Optional

from i18n import tr
import paths
import usb_link

log = logging.getLogger("pspstream.gamepad")

# Order matches bits 0..10 in the HID Report Descriptor in psp/usbdrv/main.c
# and BUTTON_CODES in gamepad.py.
BUTTONS = {
    "BTN_A": 0,
    "BTN_B": 1,
    "BTN_X": 2,
    "BTN_Y": 3,
    "BTN_TL": 4,
    "BTN_TR": 5,
    "BTN_SELECT": 6,
    "BTN_START": 7,
    "BTN_MODE": 8,
    "BTN_THUMBL": 9,
    "BTN_THUMBR": 10,
}

# (ABS_HAT0X, ABS_HAT0Y) -> HID 4-bit hat switch value (0=N..7=NW, 0xF=neutral).
HAT_MAP = {
    (0, -1): 0,
    (1, -1): 1,
    (1, 0): 2,
    (1, 1): 3,
    (0, 1): 4,
    (-1, 1): 5,
    (-1, 0): 6,
    (-1, -1): 7,
    (0, 0): 0xF,
}

REPORT_STRUCT = struct.Struct("<HBBBBBB")
NEUTRAL_REPORT = REPORT_STRUCT.pack(0xF000, 128, 128, 128, 128, 0, 0)

SHM_MAGIC = 0x50535050  # "PSPP"
SHM_STRUCT = struct.Struct("<IIIIHBBBBBB")


def shm_path() -> Path:
    """Path to the shared memory file read by the CrossOver SDL2 proxy."""
    return Path(os.environ.get("PSPSTREAM_PAD_SHM", "/tmp/pspstream_pad.shm"))


def find_crossover_unix_dir() -> Optional[Path]:
    """Locate CrossOver's x86_64-unix directory containing winebus.so."""
    env = os.environ.get("PSPSTREAM_CROSSOVER_UNIX_DIR")
    candidates = [Path(env)] if env else [
        Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix"),
        Path.home() / "Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix",
    ]
    for directory in candidates:
        if (directory / "winebus.so").is_file():
            return directory
    return None


def crossover_installed() -> bool:
    """Return whether our SDL2 proxy is installed next to CrossOver's winebus.so."""
    unix_dir = find_crossover_unix_dir()
    return unix_dir is not None and (unix_dir / "libSDL2-2.0.0.dylib").is_file()


def install_crossover_shim(dylib_path: Path = None, unix_dir: Path = None) -> Path:
    """Install libSDL2-2.0.0.dylib alongside CrossOver's winebus.so atomically."""
    src = Path(dylib_path) if dylib_path is not None else paths.crossover_sdl2()
    if not src.is_file():
        raise FileNotFoundError(f"CrossOver SDL2 proxy dylib not found: {src}")
    dest_dir = Path(unix_dir) if unix_dir is not None else find_crossover_unix_dir()
    if dest_dir is None or not (dest_dir / "winebus.so").is_file():
        raise FileNotFoundError("CrossOver x86_64-unix directory (with winebus.so) not found")
    dest = dest_dir / "libSDL2-2.0.0.dylib"
    tmp = dest_dir / f".libSDL2-2.0.0.dylib.{os.getpid()}.tmp"
    try:
        shutil.copy2(src, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return dest


def _axis(value: int) -> int:
    """Convert -32768..32767 (evdev) into 0..255 with 0 -> 128 center."""
    return max(0, min(255, round((value + 32768) * 255 / 65535)))


def _trigger(value: int) -> int:
    return max(0, min(255, int(value)))


def report(state: dict) -> bytes:
    """Convert GamepadInjector state ({('key'|'abs', code): value}) to an 8-byte HID report."""
    buttons = 0
    for code, bit in BUTTONS.items():
        if state.get(("key", code)):
            buttons |= 1 << bit
    hat_x = max(-1, min(1, int(state.get(("abs", "ABS_HAT0X"), 0))))
    hat_y = max(-1, min(1, int(state.get(("abs", "ABS_HAT0Y"), 0))))
    hat = HAT_MAP.get((hat_x, hat_y), 0xF)
    buttons_hat = buttons | (hat << 12)
    return REPORT_STRUCT.pack(
        buttons_hat,
        _axis(state.get(("abs", "ABS_X"), 0)),
        _axis(state.get(("abs", "ABS_Y"), 0)),
        _axis(state.get(("abs", "ABS_RX"), 0)),
        _axis(state.get(("abs", "ABS_RY"), 0)),
        _trigger(state.get(("abs", "ABS_Z"), 0)),
        _trigger(state.get(("abs", "ABS_RZ"), 0)),
    )


class UsbHidPad:
    """Publishes mapped gamepad updates to shared memory (CrossOver) and USB HID."""

    def __init__(self, path: Path = None):
        self.state = {}
        self._warned = False
        self._path = Path(path) if path is not None else shm_path()
        self._seq = 1
        self._fd = None
        self._mmap = None
        self._open_shm()
        self._write_shm(active=1, pkt=NEUTRAL_REPORT)

    def _open_shm(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if os.fstat(fd).st_size != SHM_STRUCT.size:
                os.ftruncate(fd, SHM_STRUCT.size)
            self._mmap = mmap.mmap(fd, SHM_STRUCT.size)
            self._fd = fd
        except Exception:
            os.close(fd)
            raise

    def _write_shm(self, active: int, pkt: bytes) -> None:
        if self._mmap is None:
            return
        fields = REPORT_STRUCT.unpack(pkt)
        self._mmap[:SHM_STRUCT.size] = SHM_STRUCT.pack(
            SHM_MAGIC, active, os.getpid(), self._seq, *fields
        )

    def emit(self, changes) -> None:
        for kind, code, value in changes:
            self.state[(kind, code)] = value
        pkt = report(self.state)
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        self._write_shm(active=1, pkt=pkt)
        if not usb_link.send_hid_report(pkt) and not self._warned and not crossover_installed():
            self._warned = True
            log.info(tr("USB HID gamepad waiting for the PSP on the USB cable (transport=usb)"))

    def close(self) -> None:
        self.state.clear()
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        self._write_shm(active=0, pkt=NEUTRAL_REPORT)
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        usb_link.send_hid_report(NEUTRAL_REPORT)
