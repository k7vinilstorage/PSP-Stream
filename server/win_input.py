"""Controles do PSP no Windows: teclado e mouse pelo SendInput (user32).

O keymap.json usa os nomes do evdev (KEY_*, BTN_*): os perfis valem igual no
Linux e no Windows. Cada tecla vira o scancode do teclado (conjunto 1), não o
código virtual: jogos com DirectInput/Raw Input leem o scancode e ignoram o
código virtual. As teclas "estendidas" (setas, Insert/Delete, Ctrl e Alt da
direita, a tecla Windows) levam o prefixo E0, que no SendInput é a flag
KEYEVENTF_EXTENDEDKEY. As teclas de mídia não têm scancode estável e vão pelo
código virtual.

Limites do Windows: o SendInput não chega a janelas de um programa rodando
como administrador (UIPI) se o servidor não for administrador também, e
alguns anticheats ignoram entrada injetada.

As estruturas usam tipos de tamanho fixo (32 bits onde o Windows tem
LONG/DWORD), para o layout ser o mesmo em qualquer sistema: os testes
conferem os bytes no Linux.
"""
import ctypes
import sys

from i18n import tr

# nome do evdev -> (scancode do conjunto 1, estendida)
SCANCODES = {
    "KEY_ESC": (0x01, False), "KEY_1": (0x02, False), "KEY_2": (0x03, False), "KEY_3": (0x04, False),
    "KEY_4": (0x05, False), "KEY_5": (0x06, False), "KEY_6": (0x07, False), "KEY_7": (0x08, False),
    "KEY_8": (0x09, False), "KEY_9": (0x0A, False), "KEY_0": (0x0B, False), "KEY_MINUS": (0x0C, False),
    "KEY_EQUAL": (0x0D, False), "KEY_BACKSPACE": (0x0E, False), "KEY_TAB": (0x0F, False),
    "KEY_Q": (0x10, False), "KEY_W": (0x11, False), "KEY_E": (0x12, False), "KEY_R": (0x13, False),
    "KEY_T": (0x14, False), "KEY_Y": (0x15, False), "KEY_U": (0x16, False), "KEY_I": (0x17, False),
    "KEY_O": (0x18, False), "KEY_P": (0x19, False), "KEY_LEFTBRACE": (0x1A, False),
    "KEY_RIGHTBRACE": (0x1B, False), "KEY_ENTER": (0x1C, False), "KEY_LEFTCTRL": (0x1D, False),
    "KEY_A": (0x1E, False), "KEY_S": (0x1F, False), "KEY_D": (0x20, False), "KEY_F": (0x21, False),
    "KEY_G": (0x22, False), "KEY_H": (0x23, False), "KEY_J": (0x24, False), "KEY_K": (0x25, False),
    "KEY_L": (0x26, False), "KEY_SEMICOLON": (0x27, False), "KEY_APOSTROPHE": (0x28, False),
    "KEY_GRAVE": (0x29, False), "KEY_LEFTSHIFT": (0x2A, False), "KEY_BACKSLASH": (0x2B, False),
    "KEY_Z": (0x2C, False), "KEY_X": (0x2D, False), "KEY_C": (0x2E, False), "KEY_V": (0x2F, False),
    "KEY_B": (0x30, False), "KEY_N": (0x31, False), "KEY_M": (0x32, False), "KEY_COMMA": (0x33, False),
    "KEY_DOT": (0x34, False), "KEY_SLASH": (0x35, False), "KEY_RIGHTSHIFT": (0x36, False),
    "KEY_KPASTERISK": (0x37, False), "KEY_LEFTALT": (0x38, False), "KEY_SPACE": (0x39, False),
    "KEY_CAPSLOCK": (0x3A, False),
    "KEY_F1": (0x3B, False), "KEY_F2": (0x3C, False), "KEY_F3": (0x3D, False), "KEY_F4": (0x3E, False),
    "KEY_F5": (0x3F, False), "KEY_F6": (0x40, False), "KEY_F7": (0x41, False), "KEY_F8": (0x42, False),
    "KEY_F9": (0x43, False), "KEY_F10": (0x44, False), "KEY_F11": (0x57, False), "KEY_F12": (0x58, False),
    "KEY_NUMLOCK": (0x45, False), "KEY_SCROLLLOCK": (0x46, False),
    "KEY_KP7": (0x47, False), "KEY_KP8": (0x48, False), "KEY_KP9": (0x49, False), "KEY_KPMINUS": (0x4A, False),
    "KEY_KP4": (0x4B, False), "KEY_KP5": (0x4C, False), "KEY_KP6": (0x4D, False), "KEY_KPPLUS": (0x4E, False),
    "KEY_KP1": (0x4F, False), "KEY_KP2": (0x50, False), "KEY_KP3": (0x51, False), "KEY_KP0": (0x52, False),
    "KEY_KPDOT": (0x53, False), "KEY_102ND": (0x56, False),
    # estendidas (E0)
    "KEY_KPENTER": (0x1C, True), "KEY_RIGHTCTRL": (0x1D, True), "KEY_KPSLASH": (0x35, True),
    "KEY_SYSRQ": (0x37, True), "KEY_RIGHTALT": (0x38, True), "KEY_HOME": (0x47, True), "KEY_UP": (0x48, True),
    "KEY_PAGEUP": (0x49, True), "KEY_LEFT": (0x4B, True), "KEY_RIGHT": (0x4D, True), "KEY_END": (0x4F, True),
    "KEY_DOWN": (0x50, True), "KEY_PAGEDOWN": (0x51, True), "KEY_INSERT": (0x52, True),
    "KEY_DELETE": (0x53, True), "KEY_LEFTMETA": (0x5B, True), "KEY_RIGHTMETA": (0x5C, True),
    "KEY_COMPOSE": (0x5D, True),
}

# Teclas de mídia: pelo código virtual (VK_*), sem scancode.
VIRTUAL_KEYS = {
    "KEY_MUTE": 0xAD, "KEY_VOLUMEDOWN": 0xAE, "KEY_VOLUMEUP": 0xAF, "KEY_NEXTSONG": 0xB0,
    "KEY_PREVIOUSSONG": 0xB1, "KEY_STOPCD": 0xB2, "KEY_PLAYPAUSE": 0xB3,
}

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSE_BUTTONS = {  # nome do evdev -> (flag de apertar, flag de soltar)
    "BTN_LEFT": (0x0002, 0x0004), "BTN_RIGHT": (0x0008, 0x0010), "BTN_MIDDLE": (0x0020, 0x0040),
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("_hi", ctypes.c_uint32 * 2)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _InputUnion)]


def known(code: str) -> bool:
    return code in SCANCODES or code in VIRTUAL_KEYS or code in MOUSE_BUTTONS


def key_input(code: str, down: bool) -> INPUT:
    """O INPUT de uma tecla ou de um botão do mouse (nomes do evdev)."""
    inp = INPUT()
    if code in MOUSE_BUTTONS:
        inp.type = INPUT_MOUSE
        inp.mi.dwFlags = MOUSE_BUTTONS[code][0 if down else 1]
        return inp
    inp.type = INPUT_KEYBOARD
    if code in VIRTUAL_KEYS:
        inp.ki.wVk = VIRTUAL_KEYS[code]
        inp.ki.dwFlags = 0 if down else KEYEVENTF_KEYUP
        return inp
    scan, extended = SCANCODES[code]
    inp.ki.wScan = scan
    inp.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (0 if down else KEYEVENTF_KEYUP)
    return inp


def move_input(dx: int, dy: int) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi.dx, inp.mi.dy = int(dx), int(dy)
    inp.mi.dwFlags = MOUSEEVENTF_MOVE
    return inp


class SendInputOutput:
    """A saída do inject.Injector no Windows: key(código, apertada), move(dx, dy), close()."""

    def __init__(self, codes):
        bad = sorted(c for c in codes if not known(c))
        if bad:
            raise RuntimeError(tr("keys the Windows server does not know: {codes}").format(codes=", ".join(bad)))
        if sys.platform != "win32":
            raise RuntimeError(tr("SendInput only exists on Windows"))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = ctypes.c_uint
        self._send = user32.SendInput
        self.blocked = False

    def _push(self, inp: INPUT) -> None:
        if self._send(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1 and not self.blocked:
            # UIPI (janela de administrador em foco) ou a tela de bloqueio: avisa uma vez
            self.blocked = True
            import logging
            logging.getLogger("pspstream.input").warning(tr(
                "controls: Windows refused the input (a window running as administrator, or the lock screen?)"))

    def key(self, code: str, down: bool) -> None:
        self._push(key_input(code, down))

    def move(self, dx: int, dy: int) -> None:
        self._push(move_input(dx, dy))

    def close(self) -> None:
        pass
