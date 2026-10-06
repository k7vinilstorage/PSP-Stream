"""Controles do PSP -> controle de Xbox 360 virtual no PC (uinput), como o Sunshine.

O kernel cria um "Xbox 360 pad" de verdade: mesmo fabricante/produto
(045e:028e), os mesmos 11 botões e os mesmos eixos do driver xpad. Assim o
SDL (a maioria dos jogos nativos e o Proton), o Steam e o navegador o
reconhecem sem configuração, com os botões no lugar certo.

O PSP tem menos controles que um Xbox: um analógico, nenhum gatilho
analógico, nada de L3/R3/Guide. O que falta vem de uma camada: segurando o
botão de shift (SELECT nos perfis prontos), os outros botões ganham outra
função; um toque rápido no SELECT sozinho vale BACK. Os perfis ficam no
keymap.json ("type": "gamepad").

Destinos:
    A B X Y LB RB BACK START GUIDE L3 R3        botões
    LT RT                                       gatilhos (apertado = 255)
    DPAD_UP DPAD_DOWN DPAD_LEFT DPAD_RIGHT      direcional
    LS_UP LS_DOWN LS_LEFT LS_RIGHT              analógico esquerdo no digital
    RS_UP RS_DOWN RS_LEFT RS_RIGHT              analógico direito no digital
Use + para apertar mais de um (ex.: "LB+RB").

Tecla presa: como no teclado (inject.py), se o PSP passar `timeout` sem
mandar nada com algo apertado, tudo volta ao neutro.
"""
import logging
import math
import threading
import time

from inject import PSP_BUTTONS
from i18n import tr

log = logging.getLogger("pspstream.gamepad")

# Botões na ordem dos códigos do kernel: é a ordem b0..b10 que o SDL usa no
# mapeamento do 045e:028e (a:b0, b:b1, x:b2, y:b3, leftshoulder:b4, ...).
# Um código a mais no meio trocaria os botões de lugar nos jogos.
BUTTON_CODES = {
    "A": "BTN_A", "B": "BTN_B", "X": "BTN_X", "Y": "BTN_Y",
    "LB": "BTN_TL", "RB": "BTN_TR", "BACK": "BTN_SELECT", "START": "BTN_START",
    "GUIDE": "BTN_MODE", "L3": "BTN_THUMBL", "R3": "BTN_THUMBR",
}
TRIGGERS = {"LT": "ABS_Z", "RT": "ABS_RZ"}
DPAD = {"DPAD_UP": (1, -1), "DPAD_DOWN": (1, 1), "DPAD_LEFT": (0, -1), "DPAD_RIGHT": (0, 1)}
STICK_DIRS = {
    "LS_UP": ("L", 1, -1), "LS_DOWN": ("L", 1, 1), "LS_LEFT": ("L", 0, -1), "LS_RIGHT": ("L", 0, 1),
    "RS_UP": ("R", 1, -1), "RS_DOWN": ("R", 1, 1), "RS_LEFT": ("R", 0, -1), "RS_RIGHT": ("R", 0, 1),
}
TARGETS = set(BUTTON_CODES) | set(TRIGGERS) | set(DPAD) | set(STICK_DIRS)
STICK_AXES = {"L": ("ABS_X", "ABS_Y"), "R": ("ABS_RX", "ABS_RY")}
AXIS_MAX = 32767

# Identidade do xpad com um controle com fio (045e:028e, versão 0x0114).
VENDOR, PRODUCT, VERSION, BUS_USB = 0x045E, 0x028E, 0x0114, 0x03
NAME = "PSPStream X-Box 360 pad"

TAP_MAX_S = 0.5      # SELECT solto antes disso, sem outro botão no meio = toque (BACK)
TAP_HOLD_S = 0.06    # quanto o toque fica apertado: um jogo a 30 fps ainda vê
LOOP_HZ = 100


def parse_targets(action: str):
    names = [t.strip().upper() for t in action.split("+") if t.strip()]
    bad = [t for t in names if t not in TARGETS]
    if bad:
        raise SystemExit(tr("unknown target in the keymap (gamepad): {names}; use {valid}").format(
            names=", ".join(bad), valid=", ".join(sorted(TARGETS))))
    return names


def _button_map(table: dict, what: str) -> dict:
    out = {}
    for name, action in table.items():
        if name not in PSP_BUTTONS:
            raise SystemExit(tr("unknown PSP button in {where}: {name}").format(where=what, name=name))
        out[PSP_BUTTONS[name]] = parse_targets(action)
    return out


class _PadDryRun:
    """Registra os eventos (--input-dry-run e testes)."""

    def __init__(self):
        self.events = []
        self.syns = 0

    def emit(self, changes):
        for kind, code, value in changes:
            self.events.append((code, value))
            log.info(tr("gamepad %s = %d"), code, value)
        self.syns += 1

    def close(self):
        pass


class _PadUInput:
    def __init__(self):
        try:
            from evdev import AbsInfo, UInput, ecodes
        except ImportError as exc:
            import distro
            raise RuntimeError(tr("python-evdev is not installed ({hint})").format(hint=distro.hint("evdev"))) from exc
        self.ec = ecodes
        stick = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
        trigger = AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)
        hat = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
        caps = {
            ecodes.EV_KEY: [getattr(ecodes, c) for c in BUTTON_CODES.values()],
            ecodes.EV_ABS: [(ecodes.ABS_X, stick), (ecodes.ABS_Y, stick), (ecodes.ABS_Z, trigger),
                            (ecodes.ABS_RX, stick), (ecodes.ABS_RY, stick), (ecodes.ABS_RZ, trigger),
                            (ecodes.ABS_HAT0X, hat), (ecodes.ABS_HAT0Y, hat)],
        }
        try:
            # max_effects=0: sem force feedback. Com ele declarado e ninguém
            # respondendo, um jogo que tentasse vibrar travaria esperando.
            self.ui = UInput(caps, name=NAME, vendor=VENDOR, product=PRODUCT, version=VERSION,
                             bustype=BUS_USB, max_effects=0)
        except Exception as exc:  # OSError/PermissionError ou evdev.UInputError
            raise RuntimeError(tr("no access to /dev/uinput ({error}); see \"Controls (uinput)\" on the Installation page of the PSPStream wiki").format(error=exc)) from exc

    def emit(self, changes):
        ec = self.ec
        for kind, code, value in changes:
            self.ui.write(ec.EV_KEY if kind == "key" else ec.EV_ABS, getattr(ec, code), value)
        self.ui.syn()

    def close(self):
        self.ui.close()


class GamepadInjector:
    def __init__(self, profile: dict, dry_run: bool = False, timeout: float = 0.5, out=None):
        """out: para onde vão as mudanças (emit/close); o padrão é o /dev/uinput (o Wolf usa a API)."""
        self.buttons = _button_map(profile.get("buttons", {}), "buttons")
        shift = profile.get("shift") or {}
        self.shift_mask = 0
        self.shift_buttons = {}
        self.tap = None
        if shift:
            name = shift.get("button", "SELECT")
            if name not in PSP_BUTTONS:
                raise SystemExit(tr("unknown shift button: {name}").format(name=name))
            self.shift_mask = PSP_BUTTONS[name]
            self.shift_buttons = _button_map(shift.get("buttons", {}), "shift")
            self.tap = parse_targets(shift["tap"]) if shift.get("tap") else None
            self.buttons.pop(self.shift_mask, None)  # o shift não tem função própria
        analog = profile.get("analog", {})
        self.stick = analog.get("stick", "left")
        if self.stick not in ("left", "right", "dpad", "none"):
            raise SystemExit(tr("analog.stick must be left, right, dpad or none (got {value})").format(value=self.stick))
        self.deadzone = float(analog.get("deadzone", 0.15))
        self.outer = float(analog.get("max", 0.92))  # o analógico do PSP raramente chega a 100%
        self.curve = float(analog.get("curve", 1.0))
        self.digital = float(profile.get("digital_stick", 1.0))

        self.out = out if out is not None else (_PadDryRun() if dry_run else _PadUInput())
        self.lock = threading.Lock()
        self.prev = 0
        self.active = {}          # máscara do botão do PSP -> destinos que ele está segurando
        self.ax = self.ay = 0.0   # analógico já com zona morta
        self.shift_down = False
        self.shift_t = 0.0
        self.shift_used = False
        self.tap_until = 0.0      # toque (BACK) segurado até este instante
        self.state = self._neutral()
        self.timeout = timeout
        self.last_update = time.monotonic()
        self.timed_out = False
        self.running = True
        threading.Thread(target=self._loop, name="gamepad", daemon=True).start()

    # ---- estado ----

    @staticmethod
    def _neutral() -> dict:
        state = {("key", c): 0 for c in BUTTON_CODES.values()}
        for code in ("ABS_X", "ABS_Y", "ABS_RX", "ABS_RY", "ABS_Z", "ABS_RZ", "ABS_HAT0X", "ABS_HAT0Y"):
            state[("abs", code)] = 0
        return state

    def _stick(self, raw_x: int, raw_y: int):
        """Zona morta radial, saturação antes da borda e curva: (-1..1, -1..1)."""
        x = max(-1.0, min(1.0, (raw_x - 128) / 127.0))
        y = max(-1.0, min(1.0, (raw_y - 128) / 127.0))
        mag = math.hypot(x, y)
        if mag <= self.deadzone:
            return 0.0, 0.0
        scaled = min(1.0, (mag - self.deadzone) / max(1e-6, self.outer - self.deadzone)) ** self.curve
        return x / mag * scaled, y / mag * scaled

    def _targets(self):
        held = [t for ts in self.active.values() for t in ts]
        if self.tap_until:
            held += self.tap
        return held

    def _wanted(self) -> dict:
        state = self._neutral()
        held = self._targets()
        sticks = {"L": [0.0, 0.0], "R": [0.0, 0.0]}
        if self.stick in ("left", "right"):
            sticks["L" if self.stick == "left" else "R"] = [self.ax, self.ay]
        digital = {"L": [0, 0], "R": [0, 0]}
        hat = [0, 0]
        if self.stick == "dpad":
            hat = [(self.ax > 0.5) - (self.ax < -0.5), (self.ay > 0.5) - (self.ay < -0.5)]
        for t in held:
            if t in BUTTON_CODES:
                state[("key", BUTTON_CODES[t])] = 1
            elif t in TRIGGERS:
                state[("abs", TRIGGERS[t])] = 255
            elif t in DPAD:
                axis, sign = DPAD[t]
                hat[axis] = sign
            else:
                side, axis, sign = STICK_DIRS[t]
                digital[side][axis] += sign
        for side, (ax_x, ax_y) in STICK_AXES.items():
            v = sticks[side]
            for axis in (0, 1):
                d = max(-1, min(1, digital[side][axis]))
                if d:  # o direcional digital manda naquele eixo
                    v[axis] = d * self.digital
            state[("abs", ax_x)] = int(round(max(-1.0, min(1.0, v[0])) * AXIS_MAX))
            state[("abs", ax_y)] = int(round(max(-1.0, min(1.0, v[1])) * AXIS_MAX))
        state[("abs", "ABS_HAT0X")] = max(-1, min(1, hat[0]))
        state[("abs", "ABS_HAT0Y")] = max(-1, min(1, hat[1]))
        return state

    def _flush(self):
        wanted = self._wanted()
        changes = [(kind, code, v) for (kind, code), v in wanted.items() if self.state[(kind, code)] != v]
        if changes:
            self.out.emit(changes)
            self.state = wanted

    # ---- entrada ----

    def update(self, buttons: int, lx: int, ly: int) -> None:
        now = time.monotonic()
        with self.lock:
            self.last_update = now
            self.timed_out = False
            changed = buttons ^ self.prev
            if self.shift_mask and changed & self.shift_mask:
                if buttons & self.shift_mask:
                    self.shift_down, self.shift_t, self.shift_used = True, now, False
                else:
                    self.shift_down = False
                    if self.tap and not self.shift_used and now - self.shift_t < TAP_MAX_S:
                        self.tap_until = now + TAP_HOLD_S
            for mask in PSP_BUTTONS.values():
                if not changed & mask or mask == self.shift_mask:
                    continue
                if buttons & mask:
                    # o destino é decidido ao apertar: soltar o SELECT antes
                    # do botão não troca o que ele está segurando
                    if self.shift_down:
                        self.shift_used = True
                    targets = self.shift_buttons.get(mask) if self.shift_down else None
                    if targets is None:
                        targets = self.buttons.get(mask)
                    if targets:
                        self.active[mask] = targets
                else:
                    self.active.pop(mask, None)
            self.prev = buttons
            self.ax, self.ay = self._stick(lx, ly)
            self._flush()

    def _is_active(self) -> bool:
        return bool(self.prev or self.ax or self.ay or self.active or self.tap_until)

    def _loop(self):
        period = 1.0 / LOOP_HZ
        while self.running:
            time.sleep(period)
            now = time.monotonic()
            with self.lock:
                if self.tap_until and now >= self.tap_until:
                    self.tap_until = 0.0
                    self._flush()
            if self.timeout and not self.timed_out and self._is_active() and \
                    now - self.last_update > self.timeout:
                log.warning(tr("gamepad: nothing from the PSP for %.0f ms, back to neutral"),
                            (now - self.last_update) * 1000)
                self.release_all()
                self.timed_out = True

    def release_all(self) -> None:
        """Tudo solto e analógicos no centro (PSP desconectou ou sumiu)."""
        with self.lock:
            self.active.clear()
            self.prev = 0
            self.ax = self.ay = 0.0
            self.shift_down = False
            self.tap_until = 0.0
            self._flush()

    def close(self) -> None:
        self.running = False
        self.release_all()
        self.out.close()
