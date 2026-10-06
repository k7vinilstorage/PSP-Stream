"""Controle de Xbox 360 virtual no Windows, pelo driver ViGEmBus (o mesmo do Sunshine e do DS4Windows).

O GamepadInjector (gamepad.py) decide o estado com os mesmos perfis do Linux (xbox, xbox-camera,
xbox-shoulders e a camada do SELECT); aqui esse estado vira o XUSB_REPORT do ViGEm, que o Windows
entrega aos jogos pelo XInput como um controle de Xbox 360 com fio.

Precisa:
- do driver ViGEmBus, instalado uma vez como administrador (pspstream --setup baixa o instalador oficial,
  numa versão e com um hash fixos, e o roda);
- da ViGEmClient.dll (MIT), que vai junto do pspstream.exe, compilada do código-fonte oficial no CI
  (packaging/windows/vigem.py). PSPSTREAM_VIGEMCLIENT aponta para outra.

O ViGEmBus foi aposentado pelo autor em 2023, mas segue funcionando e é o que os outros programas
de streaming usam; não há outro jeito de criar um controle XInput sem driver.
"""
import ctypes
import logging
import os
import sys
import time
from ctypes import POINTER, Structure, c_short, c_ubyte, c_uint32, c_ulong, c_ushort, c_void_p
from pathlib import Path

from i18n import tr

log = logging.getLogger("pspstream.gamepad")

DLL_NAME = "ViGEmClient.dll"
DOWNLOAD_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"

# VIGEM_ERROR (include/ViGEm/Client.h)
NONE = 0x20000000
BUS_NOT_FOUND = 0xE0000001
NO_FREE_SLOT = 0xE0000002
BUS_VERSION_MISMATCH = 0xE0000008
BUS_ACCESS_FAILED = 0xE0000009

# XUSB_BUTTON: os bits do wButtons (iguais aos do XINPUT_GAMEPAD), a partir dos códigos que o
# GamepadInjector usa (os do kernel, como no uinput).
XUSB_BUTTONS = {
    "BTN_A": 0x1000, "BTN_B": 0x2000, "BTN_X": 0x4000, "BTN_Y": 0x8000,
    "BTN_TL": 0x0100, "BTN_TR": 0x0200, "BTN_SELECT": 0x0020, "BTN_START": 0x0010,
    "BTN_MODE": 0x0400, "BTN_THUMBL": 0x0040, "BTN_THUMBR": 0x0080,
}
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008


class XUSB_REPORT(Structure):
    """O XINPUT_GAMEPAD: 12 bytes."""
    _fields_ = [("wButtons", c_ushort), ("bLeftTrigger", c_ubyte), ("bRightTrigger", c_ubyte),
                ("sThumbLX", c_short), ("sThumbLY", c_short), ("sThumbRX", c_short), ("sThumbRY", c_short)]


def _thumb(value: int, invert: bool = False) -> int:
    # No evdev o Y cresce para baixo; no XInput, para cima.
    return max(-32768, min(32767, -value if invert else value))


def _trigger(value: int) -> int:
    return max(0, min(255, value))


def report(state: dict) -> XUSB_REPORT:
    """O estado do GamepadInjector ({("key"|"abs", código): valor}) como XUSB_REPORT."""
    buttons = 0
    for code, bit in XUSB_BUTTONS.items():
        if state.get(("key", code)):
            buttons |= bit
    hat_x, hat_y = state.get(("abs", "ABS_HAT0X"), 0), state.get(("abs", "ABS_HAT0Y"), 0)
    buttons |= (DPAD_LEFT if hat_x < 0 else DPAD_RIGHT if hat_x > 0 else 0)
    buttons |= (DPAD_UP if hat_y < 0 else DPAD_DOWN if hat_y > 0 else 0)
    return XUSB_REPORT(buttons, _trigger(state.get(("abs", "ABS_Z"), 0)), _trigger(state.get(("abs", "ABS_RZ"), 0)),
                       _thumb(state.get(("abs", "ABS_X"), 0)), _thumb(state.get(("abs", "ABS_Y"), 0), True),
                       _thumb(state.get(("abs", "ABS_RX"), 0)), _thumb(state.get(("abs", "ABS_RY"), 0), True))


def app_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent.parent


def candidates() -> list:
    """Onde procurar a DLL: PSPSTREAM_VIGEMCLIENT, a pasta vigem\\ ao lado do pspstream.exe, a própria
    pasta, a lib\\ e a do --setup (%LOCALAPPDATA%\\PSPStream\\lib)."""
    env = os.environ.get("PSPSTREAM_VIGEMCLIENT")
    if env:
        return [Path(env)]
    app = app_dir()
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PSPStream" / "lib"
    return [app / "vigem" / DLL_NAME, app / DLL_NAME, app / "lib" / DLL_NAME, local / DLL_NAME]


def _bind(lib):
    lib.vigem_alloc.argtypes, lib.vigem_alloc.restype = (), c_void_p
    lib.vigem_free.argtypes, lib.vigem_free.restype = (c_void_p,), None
    lib.vigem_connect.argtypes, lib.vigem_connect.restype = (c_void_p,), c_uint32
    lib.vigem_disconnect.argtypes, lib.vigem_disconnect.restype = (c_void_p,), None
    lib.vigem_target_x360_alloc.argtypes, lib.vigem_target_x360_alloc.restype = (), c_void_p
    lib.vigem_target_free.argtypes, lib.vigem_target_free.restype = (c_void_p,), None
    lib.vigem_target_add.argtypes, lib.vigem_target_add.restype = (c_void_p, c_void_p), c_uint32
    lib.vigem_target_remove.argtypes, lib.vigem_target_remove.restype = (c_void_p, c_void_p), c_uint32
    lib.vigem_target_x360_update.argtypes = (c_void_p, c_void_p, XUSB_REPORT)
    lib.vigem_target_x360_update.restype = c_uint32
    lib.vigem_target_x360_get_user_index.argtypes = (c_void_p, c_void_p, POINTER(c_ulong))
    lib.vigem_target_x360_get_user_index.restype = c_uint32
    return lib


def load_client():
    """A ViGEmClient.dll já com os tipos. RuntimeError se não achar."""
    for path in candidates():
        if path.is_file():
            try:
                return _bind(ctypes.CDLL(str(path))), path
            except OSError as exc:
                raise RuntimeError(tr("{path} did not load: {error}").format(path=path, error=exc)) from None
    raise RuntimeError(tr("ViGEmClient.dll not found (it comes with pspstream.exe, in the vigem folder; "
                          "PSPSTREAM_VIGEMCLIENT points to another)"))


def error_text(code: int) -> str:
    if code == BUS_NOT_FOUND:
        return tr("the ViGEmBus driver is not installed: pspstream --setup installs it (once, as administrator)")
    if code == BUS_VERSION_MISMATCH:
        return tr("the ViGEmBus driver is too old: install the latest version ({url})").format(url=DOWNLOAD_URL)
    if code == NO_FREE_SLOT:
        return tr("ViGEmBus has no free controller slot (4 Xbox controllers already connected?)")
    if code == BUS_ACCESS_FAILED:
        return tr("no access to the ViGEmBus driver (another program holding it?)")
    return tr("ViGEmBus error 0x{code:08X}").format(code=code)


class ViGEmPad:
    """Um controle de Xbox 360 no barramento do ViGEm: o 'out' do GamepadInjector (emit/close)."""

    def __init__(self, lib=None):
        """lib: a DLL (os testes passam uma falsa). RuntimeError se o driver ou a DLL faltarem."""
        self.lib = lib if lib is not None else load_client()[0]
        self.client = self.target = None
        self.warned = False
        self.client = self.lib.vigem_alloc()
        if not self.client:
            raise RuntimeError(tr("ViGEmClient: out of memory"))
        err = self.lib.vigem_connect(self.client)
        if err != NONE:
            self.lib.vigem_free(self.client)
            self.client = None
            raise RuntimeError(error_text(err))
        target = self.lib.vigem_target_x360_alloc()
        err = self.lib.vigem_target_add(self.client, target) if target else NO_FREE_SLOT
        if err != NONE:
            if target:
                self.lib.vigem_target_free(target)
            self._disconnect()
            raise RuntimeError(error_text(err))
        self.target = target
        from gamepad import GamepadInjector
        self.state = GamepadInjector._neutral()
        self._send()

    def user_index(self):
        """O número do controle no XInput (0-3), ou None se o Windows ainda não deu um."""
        index = c_ulong(0)
        ok = self.lib.vigem_target_x360_get_user_index(self.client, self.target, ctypes.byref(index)) == NONE
        return index.value if ok else None

    def _send(self):
        err = self.lib.vigem_target_x360_update(self.client, self.target, report(self.state))
        if err != NONE and not self.warned:
            self.warned = True
            log.warning(tr("gamepad: ViGEmBus refused the update: %s"), error_text(err))

    def emit(self, changes):
        for kind, code, value in changes:
            self.state[(kind, code)] = value
        if self.target:
            self._send()

    def _disconnect(self):
        if self.client:
            self.lib.vigem_disconnect(self.client)
            self.lib.vigem_free(self.client)
            self.client = None

    def close(self):
        if self.target:
            self.lib.vigem_target_remove(self.client, self.target)
            self.lib.vigem_target_free(self.target)
            self.target = None
        self._disconnect()


class XINPUT_STATE(Structure):
    _fields_ = [("dwPacketNumber", c_uint32), ("Gamepad", XUSB_REPORT)]


def xinput_reader():
    """index -> XUSB_REPORT (o XINPUT_GAMEPAD) ou None se não há controle nesse número: o que um jogo lê."""
    for name in ("xinput1_4", "xinput9_1_0"):
        try:
            dll = ctypes.WinDLL(name)
            break
        except OSError:
            continue
    else:
        raise RuntimeError(tr("XInput not found"))
    get = dll.XInputGetState
    get.argtypes, get.restype = (c_uint32, POINTER(XINPUT_STATE)), c_uint32

    def read(index):
        state = XINPUT_STATE()
        return state.Gamepad if get(index, ctypes.byref(state)) == 0 else None
    return read


def self_test(pad=None, read=None, timeout: float = 3.0):
    """O controle de ponta a ponta: cria um, aperta A com o analógico para a direita e lê de volta pelo
    XInput, como um jogo; depois o remove. (True, número do controle no XInput) ou (False, motivo)."""
    read = read or xinput_reader()
    pad = pad or ViGEmPad()
    try:
        deadline = time.monotonic() + timeout
        index = pad.user_index()
        while index is None and time.monotonic() < deadline:
            time.sleep(0.05)
            index = pad.user_index()
        if index is None:
            return False, tr("the controller got no XInput number")
        pad.emit([("key", "BTN_A", 1), ("abs", "ABS_X", 32767)])
        while time.monotonic() < deadline:
            got = read(index)
            if got is not None and got.wButtons & XUSB_BUTTONS["BTN_A"] and got.sThumbLX == 32767:
                return True, index
            time.sleep(0.02)
        return False, tr("XInput did not see the button press (controller {index})").format(index=index + 1)
    finally:
        pad.close()


def bus_status():
    """Para o --check: ('ok', caminho da DLL), ('no-dll', motivo), ('no-bus', motivo) ou ('error', motivo).
    Só conecta ao barramento, sem criar um controle."""
    try:
        lib, path = load_client()
    except RuntimeError as exc:
        return "no-dll", str(exc)
    client = lib.vigem_alloc()
    if not client:
        return "error", tr("ViGEmClient: out of memory")
    try:
        err = lib.vigem_connect(client)
        if err == NONE:
            lib.vigem_disconnect(client)
            return "ok", str(path)
        return ("no-bus" if err == BUS_NOT_FOUND else "error"), error_text(err)
    finally:
        lib.vigem_free(client)
