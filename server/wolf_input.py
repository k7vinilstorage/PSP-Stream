"""Controles do PSP no Wolf: o controle de Xbox virtual do gamepad.py, mas em
pacotes de controle do Moonlight mandados pela API (sessions/input), em vez
do /dev/uinput. O Wolf cria o controle virtual da sessão (inputtino) e o liga
no jogo; para isso, a sessão do PSPStream entra no lobby (a WolfSource faz
isso quando há controles ligados).

Os mesmos perfis do keymap.json ("type": "gamepad": xbox, xbox-camera,
xbox-ombros), com a camada do SELECT, a zona morta e o --input-timeout.

Formato conferido no código do Wolf stable (facb8e0) e no moonlight-common-c:
- O hex vai inteiro para a struct INPUT_PKT do Wolf, sem conferir o tamanho
  (api/endpoints.cpp): cabeçalho do canal de controle 0x0206 + tamanho
  (little endian, 2 + 2 bytes), tamanho dos dados (big endian, 4 bytes), tipo
  (little endian, 4 bytes) e o resto. O pacote vai sempre inteiro.
- CONTROLLER_ARRIVAL (0x55000004) antes do estado: número, tipo (Xbox),
  capacidades (gatilhos analógicos) e os botões que existem. No Wolf as
  capacidades têm 1 byte e no Moonlight 2 (little endian): o Wolf lê o byte
  baixo, o mesmo; o resto ele ignora. Vai o formato do Moonlight, o que os
  clientes de verdade mandam.
- CONTROLLER_MULTI (0x0000000C), campos em little endian; o eixo Y é o do
  XInput (para cima é positivo): o inputtino grava ABS_Y = -y.
- Ao fechar, um CONTROLLER_MULTI sem o bit do controle na máscara: o Wolf
  desliga o controle virtual.
- START + cima + RB juntos é o atalho do Wolf UI: tira a sessão do lobby. A
  WolfSource percebe e entra de novo.
"""
import logging
import struct
import threading

import wolf_source
from gamepad import GamepadInjector
from wolf_api import WolfApiError

log = logging.getLogger("pspstream.wolf")

INPUT_DATA = 0x0206
CONTROLLER_MULTI = 0x0000000C
CONTROLLER_ARRIVAL = 0x55000004
TYPE_XBOX = 0x01
CAP_ANALOG_TRIGGERS = 0x01

# Botões do Moonlight (moonlight/control.hpp, CONTROLLER_BTN) a partir dos códigos do gamepad.py.
BUTTONS = {
    "BTN_A": 0x1000, "BTN_B": 0x2000, "BTN_X": 0x4000, "BTN_Y": 0x8000,
    "BTN_TL": 0x0100, "BTN_TR": 0x0200, "BTN_SELECT": 0x0020, "BTN_START": 0x0010,
    "BTN_MODE": 0x0400, "BTN_THUMBL": 0x0040, "BTN_THUMBR": 0x0080,
}
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
SUPPORTED = sum(BUTTONS.values()) | DPAD_UP | DPAD_DOWN | DPAD_LEFT | DPAD_RIGHT


def input_packet(kind: int, body: bytes) -> bytes:
    """INPUT_DATA completo: 0x0206, tamanho do resto, tamanho dos dados (BE), tipo e corpo."""
    data = struct.pack("<I", kind) + body
    inner = struct.pack(">I", len(data)) + data
    return struct.pack("<HH", INPUT_DATA, len(inner)) + inner


def arrival_packet(number: int = 0) -> bytes:
    return input_packet(CONTROLLER_ARRIVAL, struct.pack("<BBHI", number, TYPE_XBOX, CAP_ANALOG_TRIGGERS, SUPPORTED))


def multi_packet(number: int, mask: int, buttons: int, lt: int, rt: int, lsx: int, lsy: int, rsx: int,
                 rsy: int) -> bytes:
    """CONTROLLER_MULTI com as constantes do moonlight-common-c (headerB 0x1A, midB 0x14, tailA 0x9C,
    tailB 0x55); buttonFlags2 leva os botões acima de 16 bits."""
    body = struct.pack("<hhhhHBBhhhhhHh", 0x1A, number, mask, 0x14, buttons & 0xFFFF, lt, rt, lsx, lsy, rsx, rsy,
                       0x9C, (buttons >> 16) & 0xFFFF, 0x55)
    return input_packet(CONTROLLER_MULTI, body)


def _yup(value: int) -> int:
    """evdev (para baixo é positivo) -> XInput (para cima é positivo)."""
    return -max(-32767, min(32767, int(value)))


def state_packet(state: dict, number: int = 0, connected: bool = True) -> bytes:
    """Estado do GamepadInjector ({("key"|"abs", código): valor}) -> CONTROLLER_MULTI."""
    buttons = 0
    for code, flag in BUTTONS.items():
        if state.get(("key", code)):
            buttons |= flag
    hat_x, hat_y = state.get(("abs", "ABS_HAT0X"), 0), state.get(("abs", "ABS_HAT0Y"), 0)
    buttons |= (DPAD_UP if hat_y < 0 else DPAD_DOWN if hat_y > 0 else 0)
    buttons |= (DPAD_LEFT if hat_x < 0 else DPAD_RIGHT if hat_x > 0 else 0)
    axis = {code: state.get(("abs", code), 0) for code in ("ABS_X", "ABS_Y", "ABS_RX", "ABS_RY", "ABS_Z", "ABS_RZ")}
    return multi_packet(number, (1 << number) if connected else 0, buttons,
                        max(0, min(255, axis["ABS_Z"])), max(0, min(255, axis["ABS_RZ"])),
                        max(-32767, min(32767, axis["ABS_X"])), _yup(axis["ABS_Y"]),
                        max(-32767, min(32767, axis["ABS_RX"])), _yup(axis["ABS_RY"]))


class WolfPad:
    """Saída do GamepadInjector para o Wolf. Uma thread manda os pacotes: a
    chamada à API não pode segurar a thread que recebe do PSP, e só o estado
    mais novo vai (sem fila)."""

    def __init__(self, number: int = 0, source_getter=None, idle_s: float = 0.5):
        self.number = number
        self.get_source = source_getter or wolf_source.current
        self.idle_s = idle_s
        self.state = GamepadInjector._neutral()
        self.cond = threading.Condition()
        self.dirty = False
        self.running = True
        self.link = None   # (fonte, geração da sessão) em que o controle foi anunciado
        self.source = None
        self.said = None
        self.thread = threading.Thread(target=self._loop, name="wolf-input", daemon=True)
        self.thread.start()

    def emit(self, changes) -> None:
        with self.cond:
            for kind, code, value in changes:
                self.state[(kind, code)] = value
            self.dirty = True
            self.cond.notify()

    def close(self) -> None:
        with self.cond:
            self.running = False
            self.cond.notify()
        self.thread.join(timeout=5)

    def _say(self, msg: str) -> None:
        if msg != self.said:
            self.said = msg
            log.warning("controles: %s", msg)

    def _loop(self) -> None:
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.dirty or not self.running, timeout=self.idle_s)
                running, dirty, state = self.running, self.dirty, dict(self.state)
                self.dirty = False
            if not running:
                break
            self._send(state, dirty)
        self._unplug()

    def _send(self, state: dict, dirty: bool) -> None:
        src = self.get_source()
        if src is not self.source:  # a interface web trocou a captura
            if self.source is not None:
                self.source.input_wanted = False
            self.source, self.link = src, None
        if src is None:
            return
        src.input_wanted = True  # a WolfSource põe a sessão no lobby
        where = src.input_target()
        if where is None:  # ainda fora do lobby (ou só visualização): os botões não vão
            self.link = None
            return
        sid, generation = where
        try:
            if self.link != (src, generation):
                src.api.send_input(sid, arrival_packet(self.number))
                self.link = (src, generation)
                dirty = True  # o estado atual vai logo depois
                log.info("controles: controle de Xbox virtual ligado na sessão %s do Wolf", sid)
            if dirty:
                src.api.send_input(sid, state_packet(state, self.number))
            self.said = None
        except WolfApiError as exc:
            self._say(f"o Wolf não recebeu o controle: {exc}")
            self.link = None  # anuncia de novo (o Wolf ignora um controle repetido)

    def _unplug(self) -> None:
        src = self.source
        if src is None:
            return
        src.input_wanted = False
        where = src.input_target()
        if self.link is not None and where is not None and self.link == (src, where[1]):
            try:
                src.api.send_input(where[0], state_packet(GamepadInjector._neutral(), self.number, connected=False))
            except WolfApiError as exc:
                log.debug("controles: desligando o controle no Wolf: %s", exc)
        self.link = None


class WolfInjector(GamepadInjector):
    """Os perfis de controle (keymap.json) com a saída no Wolf."""

    def __init__(self, profile: dict, dry_run: bool = False, timeout: float = 0.5, source_getter=None):
        super().__init__(profile, dry_run, timeout, out=None if dry_run else WolfPad(source_getter=source_getter))
