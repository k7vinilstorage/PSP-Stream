"""USB HID gamepad on macOS through the PSP composite USB driver (psp/usbdrv).

GamepadInjector (gamepad.py) computes the mapped Xbox 360 state from the xbox,
xbox-camera, and xbox-shoulders profiles (including the SELECT shift layer);
this module packs that state into the 8-byte HID gamepad report expected by
Interface 1 (HID, EP 0x83) of the PSP's USB driver and forwards it over EP0
vendor control transfers via usb_link.send_hid_report().

Because the PSP presents a real physical USB HID Gamepad on the wire, macOS
(IOHIDFamily, GameController.framework, SDL2, and Steam Input) recognizes it
natively with zero DriverKit extensions or Apple entitlements.
"""
import logging
import struct

from i18n import tr
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
    """Forwards mapped gamepad updates to the PSP composite USB HID interface."""

    def __init__(self):
        self.state = {}
        self._warned = False

    def emit(self, changes) -> None:
        for kind, code, value in changes:
            self.state[(kind, code)] = value
        if not usb_link.send_hid_report(report(self.state)) and not self._warned:
            self._warned = True
            log.info(tr("USB HID gamepad waiting for the PSP on the USB cable (transport=usb)"))

    def close(self) -> None:
        self.state.clear()
        usb_link.send_hid_report(NEUTRAL_REPORT)
