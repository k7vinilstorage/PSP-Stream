"""Controles do PSP no macOS: teclado e mouse via Quartz Event Services (CoreGraphics).

O keymap.json usa os nomes do evdev (KEY_*, BTN_*): os perfis valem igual no
Linux, no Windows e no macOS. Cada tecla vira o CGKeyCode (HIToolbox/Events.h) e
é enviada com CGEventPost(kCGHIDEventTap, ...).

Requer permissão de Acessibilidade para o terminal/Python em:
  Ajustes do Sistema > Privacidade e Segurança > Acessibilidade
Sem ela, o sistema aceita criar os eventos, mas os ignora ao entregar aos apps.
"""
import ctypes
import logging
import sys
from ctypes import POINTER, Structure, byref, c_bool, c_double, c_int64, c_uint16, c_uint32, c_void_p

from i18n import tr

log = logging.getLogger("pspstream.input")

COREGRAPHICS_PATH = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
COREFOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
APPSERVICES_PATH = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"

# Nomes do evdev -> CGKeyCode do macOS (HIToolbox/Events.h)
KEYCODES = {
    "KEY_ESC": 0x35, "KEY_1": 0x12, "KEY_2": 0x13, "KEY_3": 0x14, "KEY_4": 0x15,
    "KEY_5": 0x17, "KEY_6": 0x16, "KEY_7": 0x1A, "KEY_8": 0x1C, "KEY_9": 0x19,
    "KEY_0": 0x1D, "KEY_MINUS": 0x1B, "KEY_EQUAL": 0x18, "KEY_BACKSPACE": 0x33, "KEY_TAB": 0x30,
    "KEY_Q": 0x0C, "KEY_W": 0x0D, "KEY_E": 0x0E, "KEY_R": 0x0F, "KEY_T": 0x11,
    "KEY_Y": 0x10, "KEY_U": 0x20, "KEY_I": 0x22, "KEY_O": 0x1F, "KEY_P": 0x23,
    "KEY_LEFTBRACE": 0x21, "KEY_RIGHTBRACE": 0x1E, "KEY_ENTER": 0x24, "KEY_LEFTCTRL": 0x3B,
    "KEY_A": 0x00, "KEY_S": 0x01, "KEY_D": 0x02, "KEY_F": 0x03, "KEY_G": 0x05,
    "KEY_H": 0x04, "KEY_J": 0x26, "KEY_K": 0x28, "KEY_L": 0x25, "KEY_SEMICOLON": 0x29,
    "KEY_APOSTROPHE": 0x27, "KEY_GRAVE": 0x32, "KEY_LEFTSHIFT": 0x38, "KEY_BACKSLASH": 0x2A,
    "KEY_Z": 0x06, "KEY_X": 0x07, "KEY_C": 0x08, "KEY_V": 0x09, "KEY_B": 0x0B,
    "KEY_N": 0x2D, "KEY_M": 0x2E, "KEY_COMMA": 0x2B, "KEY_DOT": 0x2F, "KEY_SLASH": 0x2C,
    "KEY_RIGHTSHIFT": 0x3C, "KEY_KPASTERISK": 0x43, "KEY_LEFTALT": 0x3A, "KEY_SPACE": 0x31,
    "KEY_CAPSLOCK": 0x39,
    "KEY_F1": 0x7A, "KEY_F2": 0x78, "KEY_F3": 0x63, "KEY_F4": 0x76, "KEY_F5": 0x60,
    "KEY_F6": 0x61, "KEY_F7": 0x62, "KEY_F8": 0x64, "KEY_F9": 0x65, "KEY_F10": 0x6D,
    "KEY_F11": 0x67, "KEY_F12": 0x6F,
    "KEY_NUMLOCK": 0x47, "KEY_SCROLLLOCK": 0x6B,
    "KEY_KP7": 0x59, "KEY_KP8": 0x5B, "KEY_KP9": 0x5C, "KEY_KPMINUS": 0x4E,
    "KEY_KP4": 0x56, "KEY_KP5": 0x57, "KEY_KP6": 0x58, "KEY_KPPLUS": 0x45,
    "KEY_KP1": 0x53, "KEY_KP2": 0x54, "KEY_KP3": 0x55, "KEY_KP0": 0x52,
    "KEY_KPDOT": 0x41, "KEY_102ND": 0x0A,
    "KEY_KPENTER": 0x4C, "KEY_RIGHTCTRL": 0x3E, "KEY_KPSLASH": 0x4B, "KEY_SYSRQ": 0x69,
    "KEY_RIGHTALT": 0x3D, "KEY_HOME": 0x73, "KEY_UP": 0x7E, "KEY_PAGEUP": 0x74,
    "KEY_LEFT": 0x7B, "KEY_RIGHT": 0x7C, "KEY_END": 0x77, "KEY_DOWN": 0x7D,
    "KEY_PAGEDOWN": 0x79, "KEY_INSERT": 0x72, "KEY_DELETE": 0x75,
    "KEY_LEFTMETA": 0x37, "KEY_RIGHTMETA": 0x36, "KEY_COMPOSE": 0x6E,
    "KEY_MUTE": 0x4A, "KEY_VOLUMEDOWN": 0x49, "KEY_VOLUMEUP": 0x48,
    "KEY_NEXTSONG": 0x65, "KEY_PREVIOUSSONG": 0x62, "KEY_STOPCD": 0x64, "KEY_PLAYPAUSE": 0x64,
}

# nome do evdev -> (down_type, up_type, dragged_type, cg_button)
MOUSE_BUTTONS = {
    "BTN_LEFT": (1, 2, 6, 0),
    "BTN_RIGHT": (3, 4, 7, 1),
    "BTN_MIDDLE": (25, 26, 27, 2),
}

CG_EVENT_MOUSE_MOVED = 5
CG_HID_EVENT_TAP = 0
CG_MOUSE_EVENT_DELTA_X = 4
CG_MOUSE_EVENT_DELTA_Y = 5


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


def known(code: str) -> bool:
    return code in KEYCODES or code in MOUSE_BUTTONS


def _load_frameworks():
    cg = ctypes.CDLL(COREGRAPHICS_PATH)
    cf = ctypes.CDLL(COREFOUNDATION_PATH)
    cg.CGEventCreate.argtypes = [c_void_p]
    cg.CGEventCreate.restype = c_void_p
    cg.CGEventGetLocation.argtypes = [c_void_p]
    cg.CGEventGetLocation.restype = CGPoint
    cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
    cg.CGEventCreateKeyboardEvent.restype = c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, CGPoint, c_uint32]
    cg.CGEventCreateMouseEvent.restype = c_void_p
    cg.CGEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
    cg.CGEventSetIntegerValueField.restype = None
    cg.CGEventPost.argtypes = [c_uint32, c_void_p]
    cg.CGEventPost.restype = None
    cg.CGMainDisplayID.argtypes = []
    cg.CGMainDisplayID.restype = c_uint32
    cg.CGDisplayPixelsWide.argtypes = [c_uint32]
    cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
    cg.CGDisplayPixelsHigh.argtypes = [c_uint32]
    cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
    cf.CFRelease.argtypes = [c_void_p]
    cf.CFRelease.restype = None
    return cg, cf


def screen_capture_access(prompt: bool = False) -> bool:
    """Permissão de Gravação de Tela no macOS (TCC)."""
    if sys.platform != "darwin":
        return False
    try:
        cg = ctypes.CDLL(COREGRAPHICS_PATH)
        fn = cg.CGRequestScreenCaptureAccess if prompt else cg.CGPreflightScreenCaptureAccess
        fn.argtypes = []
        fn.restype = c_bool
        return bool(fn())
    except (OSError, AttributeError):
        return False


def is_trusted(prompt: bool = False) -> bool:
    """Permissão de Acessibilidade no macOS (necessária para injetar teclas e mouse)."""
    if sys.platform != "darwin":
        return False
    try:
        app = ctypes.CDLL(APPSERVICES_PATH)
        if not prompt:
            app.AXIsProcessTrusted.argtypes = []
            app.AXIsProcessTrusted.restype = c_bool
            return bool(app.AXIsProcessTrusted())
        cf = ctypes.CDLL(COREFOUNDATION_PATH)
        key = c_void_p.in_dll(app, "kAXTrustedCheckOptionPrompt")
        val = c_void_p.in_dll(cf, "kCFBooleanTrue")
        kcb = c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        vcb = c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        cf.CFDictionaryCreate.argtypes = [c_void_p, POINTER(c_void_p), POINTER(c_void_p), ctypes.c_long,
                                          c_void_p, c_void_p]
        cf.CFDictionaryCreate.restype = c_void_p
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFRelease.restype = None
        app.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
        app.AXIsProcessTrustedWithOptions.restype = c_bool
        keys = (c_void_p * 1)(key)
        vals = (c_void_p * 1)(val)
        opts = cf.CFDictionaryCreate(None, keys, vals, 1, byref(kcb), byref(vcb))
        try:
            return bool(app.AXIsProcessTrustedWithOptions(opts))
        finally:
            if opts:
                cf.CFRelease(opts)
    except (OSError, AttributeError, ValueError):
        return False


class QuartzInputOutput:
    """Teclado e mouse virtuais no macOS (CoreGraphics CGEventPost)."""

    def __init__(self, codes: set, cg=None, cf=None):
        bad = [c for c in codes if not known(c)]
        if bad:
            raise RuntimeError(tr("unknown codes in the keymap: {codes}").format(codes=", ".join(sorted(bad))))
        if cg is None or cf is None:
            self._cg, self._cf = _load_frameworks()
            if not is_trusted(prompt=False):
                log.warning(tr("controls: macOS Accessibility permission is off; grant it to your terminal in "
                               "System Settings > Privacy & Security > Accessibility (or run pspstream --setup)"))
        else:
            self._cg, self._cf = cg, cf
        self._held_buttons = set()

    def _cursor_pos(self) -> CGPoint:
        ev = self._cg.CGEventCreate(None)
        if not ev:
            return CGPoint(0.0, 0.0)
        try:
            return self._cg.CGEventGetLocation(ev)
        finally:
            self._cf.CFRelease(ev)

    def key(self, code: str, down: bool) -> None:
        if code in MOUSE_BUTTONS:
            down_t, up_t, _, btn = MOUSE_BUTTONS[code]
            if down:
                self._held_buttons.add(code)
            else:
                self._held_buttons.discard(code)
            ev = self._cg.CGEventCreateMouseEvent(None, down_t if down else up_t, self._cursor_pos(), btn)
            if ev:
                try:
                    self._cg.CGEventPost(CG_HID_EVENT_TAP, ev)
                finally:
                    self._cf.CFRelease(ev)
            return
        keycode = KEYCODES[code]
        ev = self._cg.CGEventCreateKeyboardEvent(None, keycode, bool(down))
        if ev:
            try:
                self._cg.CGEventPost(CG_HID_EVENT_TAP, ev)
            finally:
                self._cf.CFRelease(ev)

    def move(self, dx: int, dy: int) -> None:
        dx, dy = int(dx), int(dy)
        if not (dx or dy):
            return
        loc = self._cursor_pos()
        did = self._cg.CGMainDisplayID()
        w = max(1, int(self._cg.CGDisplayPixelsWide(did)))
        h = max(1, int(self._cg.CGDisplayPixelsHigh(did)))
        nx = max(0.0, min(float(w - 1), loc.x + dx))
        ny = max(0.0, min(float(h - 1), loc.y + dy))
        ev_type, btn = CG_EVENT_MOUSE_MOVED, 0
        for held in ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"):
            if held in self._held_buttons:
                _, _, ev_type, btn = MOUSE_BUTTONS[held]
                break
        ev = self._cg.CGEventCreateMouseEvent(None, ev_type, CGPoint(nx, ny), btn)
        if ev:
            try:
                self._cg.CGEventSetIntegerValueField(ev, CG_MOUSE_EVENT_DELTA_X, dx)
                self._cg.CGEventSetIntegerValueField(ev, CG_MOUSE_EVENT_DELTA_Y, dy)
                self._cg.CGEventPost(CG_HID_EVENT_TAP, ev)
            finally:
                self._cf.CFRelease(ev)

    def close(self) -> None:
        self._held_buttons.clear()
