"""Controles do PSP -> teclado/mouse do PC via uinput (Marco 4).

uinput cria um teclado + mouse virtuais no kernel, então funciona no Wayland
(o pynput não injeta entrada no Wayland) e em jogos. Requer acesso de
escrita a /dev/uinput (wiki/Installation.md).

Os botões viram teclas assim que o pedido chega. O analógico vira movimento
de mouse numa thread a 125 Hz: o PSP só manda a posição quando ela muda, e o
servidor integra a velocidade.

Tecla presa: enquanto algo está segurado, o PSP reafirma o estado a cada
~100 ms. Se passar `timeout` sem nenhuma mensagem (Wi-Fi travou, TCP
retransmitindo, PSP congelou), o servidor solta tudo em vez de manter a
última tecla apertada até a rede voltar.
"""
import json
import logging
import threading
import time
from pathlib import Path
from i18n import tr

log = logging.getLogger("pspstream.input")

PSP_BUTTONS = {
    "SELECT": 0x0001, "START": 0x0008,
    "UP": 0x0010, "RIGHT": 0x0020, "DOWN": 0x0040, "LEFT": 0x0080,
    "L": 0x0100, "R": 0x0200,
    "TRIANGLE": 0x1000, "CIRCLE": 0x2000, "CROSS": 0x4000, "SQUARE": 0x8000,
}

MOUSE_HZ = 125


# Nomes antigos dos perfis (em português), aceitos em --profile, PSPSTREAM_PROFILE e server.json.
PROFILE_ALIASES = {"jogo": "game", "setas": "arrows", "xbox-ombros": "xbox-shoulders"}


def canonical_profile(name: str) -> str:
    return PROFILE_ALIASES.get(name, name)


def load_profile(path: str, profile: str) -> dict:
    profile = canonical_profile(profile)
    data = json.loads(Path(path).read_text())
    if profile not in data:
        names = ", ".join(k for k in data if not k.startswith("_"))
        raise SystemExit(tr("profile '{profile}' does not exist in {path} (available: {names})").format(
            profile=profile, path=path, names=names))
    return data[profile]


class _DryRun:
    """Só registra no log o que seria injetado (--input-dry-run)."""

    def __init__(self):
        self.events = []

    def key(self, code: str, down: bool):
        self.events.append(("key", code, down))
        log.info(tr("key %s %s"), code, tr("pressed") if down else tr("released"))

    def move(self, dx: int, dy: int):
        self.events.append(("move", dx, dy))
        log.debug("mouse %+d %+d", dx, dy)

    def close(self):
        pass


class _UInput:
    def __init__(self, codes: set):
        try:
            from evdev import UInput, ecodes
        except ImportError as exc:
            import distro
            raise RuntimeError(tr("python-evdev is not installed ({hint})").format(hint=distro.hint("evdev"))) from exc
        self.ec = ecodes
        keys = sorted({getattr(ecodes, c) for c in codes} | {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE})
        caps = {ecodes.EV_KEY: keys, ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]}
        try:
            self.ui = UInput(caps, name="PSPStream (PSP)")
        except Exception as exc:  # OSError/PermissionError ou evdev.UInputError (módulo não carregado)
            raise RuntimeError(tr("no access to /dev/uinput ({error}); see \"Controls (uinput)\" on the Installation page of the PSPStream wiki").format(error=exc)) from exc

    def key(self, code: str, down: bool):
        self.ui.write(self.ec.EV_KEY, getattr(self.ec, code), 1 if down else 0)
        self.ui.syn()

    def move(self, dx: int, dy: int):
        if dx:
            self.ui.write(self.ec.EV_REL, self.ec.REL_X, dx)
        if dy:
            self.ui.write(self.ec.EV_REL, self.ec.REL_Y, dy)
        self.ui.syn()

    def close(self):
        self.ui.close()


class Injector:
    def __init__(self, profile: dict, dry_run: bool = False, speed_scale: float = 1.0, timeout: float = 0.5):
        self.buttons = {}  # máscara PSP -> lista de códigos evdev
        for name, action in profile.get("buttons", {}).items():
            if name not in PSP_BUTTONS:
                raise SystemExit(tr("unknown button in the keymap: {name}").format(name=name))
            self.buttons[PSP_BUTTONS[name]] = [c.strip() for c in action.split("+") if c.strip()]
        analog = profile.get("analog", {})
        self.mode = analog.get("mode", "mouse")
        self.speed = float(analog.get("speed", 900)) * speed_scale
        self.deadzone = float(analog.get("deadzone", 0.15))
        self.curve = float(analog.get("curve", 2.0))
        self.analog_keys = analog.get("keys", {})

        codes = {c for combo in self.buttons.values() for c in combo} | set(self.analog_keys.values())
        self._validate(codes)
        self.out = _DryRun() if dry_run else _UInput(codes)

        self.lock = threading.Lock()
        self.prev = 0
        self.held = {}  # código -> quantas fontes o seguram (botão e analógico podem usar a mesma tecla)
        self.ax = self.ay = 0.0
        self.analog_dirs = set()
        self.frac = [0.0, 0.0]
        self.timeout = timeout
        self.last_update = time.monotonic()
        self.timed_out = False
        self.running = True
        threading.Thread(target=self._loop, name="input", daemon=True).start()

    @staticmethod
    def _validate(codes):
        try:
            from evdev import ecodes
        except ImportError:
            return  # sem evdev só o dry-run funciona; os nomes não são conferidos
        bad = [c for c in codes if not hasattr(ecodes, c)]
        if bad:
            raise SystemExit(tr("unknown codes in the keymap: {codes}").format(codes=", ".join(bad)))

    def _press(self, code: str, down: bool):
        n = self.held.get(code, 0)
        if down:
            self.held[code] = n + 1
            if n == 0:
                self.out.key(code, True)
        elif n > 0:
            self.held[code] = n - 1
            if n == 1:
                self.out.key(code, False)

    def _axis(self, raw: int) -> float:
        v = (raw - 128) / 127.0
        mag = abs(v)
        if mag < self.deadzone:
            return 0.0
        mag = min(1.0, (mag - self.deadzone) / (1 - self.deadzone)) ** self.curve
        return mag if v > 0 else -mag

    def update(self, buttons: int, lx: int, ly: int) -> None:
        with self.lock:
            self.last_update = time.monotonic()
            self.timed_out = False
            changed = buttons ^ self.prev
            for mask, combo in self.buttons.items():
                if changed & mask:
                    down = bool(buttons & mask)
                    for code in (combo if down else reversed(combo)):
                        self._press(code, down)
            self.prev = buttons
            self.ax, self.ay = self._axis(lx), self._axis(ly)
            if self.mode == "keys":
                dirs = set()
                if self.ax < 0:
                    dirs.add("left")
                if self.ax > 0:
                    dirs.add("right")
                if self.ay < 0:
                    dirs.add("up")
                if self.ay > 0:
                    dirs.add("down")
                for d in self.analog_dirs - dirs:
                    if d in self.analog_keys:
                        self._press(self.analog_keys[d], False)
                for d in dirs - self.analog_dirs:
                    if d in self.analog_keys:
                        self._press(self.analog_keys[d], True)
                self.analog_dirs = dirs

    def _active(self) -> bool:
        return bool(self.prev or self.ax or self.ay or any(n > 0 for n in self.held.values()))

    def _loop(self):
        period = 1.0 / MOUSE_HZ
        while self.running:
            time.sleep(period)
            if self.timeout and not self.timed_out and self._active() and \
                    time.monotonic() - self.last_update > self.timeout:
                log.warning(tr("controls: nothing from the PSP for %.0f ms, releasing everything"),
                            (time.monotonic() - self.last_update) * 1000)
                self.release_all()
                self.timed_out = True
            if self.mode != "mouse":
                continue
            with self.lock:
                if not (self.ax or self.ay):
                    self.frac = [0.0, 0.0]
                    continue
                self.frac[0] += self.ax * self.speed * period
                self.frac[1] += self.ay * self.speed * period
                dx, dy = int(self.frac[0]), int(self.frac[1])
                self.frac[0] -= dx
                self.frac[1] -= dy
                if dx or dy:
                    self.out.move(dx, dy)

    def release_all(self) -> None:
        """Solta tudo (PSP desconectou: nenhuma tecla pode ficar presa)."""
        with self.lock:
            for code, n in list(self.held.items()):
                if n > 0:
                    self.out.key(code, False)
            self.held.clear()
            self.prev = 0
            self.ax = self.ay = 0.0
            self.analog_dirs = set()

    def close(self) -> None:
        self.running = False
        self.release_all()
        self.out.close()
