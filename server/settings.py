"""Configurações gerais do servidor: o que a interface web mostra e muda.

Prioridade, da menor para a maior: o padrão da linha de comando, o arquivo
de configuração (server.json, gravado pela interface web) e as opções dadas
na linha de comando. O arquivo guarda só o que difere do padrão, então um
padrão novo numa versão nova vale sozinho.

Cada configuração diz quando a mudança vale (`apply`):

  live     na hora (qualidade, limite de FPS, DSCP)
  capture  refaz a captura com o PSP conectado (o próximo frame P é um IDR)
  audio    refaz a captura do som
  input    refaz os controles (as teclas seguradas são soltas)
  next     na próxima conexão do PSP
  restart  ao reiniciar o servidor

Sem dependências fora da biblioteca padrão: serve também para o servidor de
Windows (wiki/Windows-Server-Plan.md).
"""
import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from i18n import N_, tr

log = logging.getLogger("pspstream.settings")

APPLY_TEXT = {
    "live": N_("applies right away"),
    "capture": N_("restarts the capture (the PSP stays connected)"),
    "audio": N_("restarts the audio capture"),
    "input": N_("restarts the controls"),
    "next": N_("applies on the PSP's next connection"),
    "restart": N_("applies when the server restarts"),
}

# Nome de fonte do PipeWire/PulseAudio (pactl list short sources). Vai para
# dentro da descrição do pipeline do GStreamer: nada de aspas nem espaços.
AUDIO_DEVICE_RE = re.compile(r"^[\w.:@+-]{1,200}$")
# Alvo no Wolf: id ou nome de lobby. Não entra no pipeline (lá vai o id que o Wolf devolve, conferido de novo).
WOLF_TARGET_RE = re.compile(r"^[^\x00-\x1f\x7f]{0,100}$")


@dataclass(frozen=True)
class Setting:
    key: str             # nome no arquivo e na API
    dest: str            # atributo do argparse
    kind: str            # bool | int | float | choice | text
    group: str
    label: str
    help: str = ""
    apply: str = "live"
    choices: tuple = ()  # choice: valores aceitos (a interface completa os dinâmicos)
    min: float = None
    max: float = None
    invert: bool = False  # bool que no argparse é o contrário (audio <-> --no-audio)
    flag: str = ""        # opção da linha de comando
    arg: str = ""         # atributo do argparse da opção, se não for `dest` (codec: o escolhido x o efetivo)
    show_if: tuple = ()   # (chave, (valores...)): só aparece com essa outra configuração


SETTINGS = (
    # Geral
    Setting("language", "lang", "choice", N_("General"), N_("Language"),
            N_("Language of this page and of the server messages (log, --check). English is the default."),
            "live", ("en", "pt"), flag="--lang"),
    # Captura
    Setting("source", "source", "choice", N_("Capture"), N_("Source"),
            N_("portal: the screen through the Wayland portal (asks for permission the first time). kms: straight "
               "from the graphics card, 60 fps on GNOME 50 (the helper needs permission to read the screen: "
               "--setup gives it). x11: X11 session. test: animated pattern. static: still image. wolf: what runs "
               "in Wolf (Games on Whales); shows up when the Wolf API socket exists."),
            "capture", ("portal", "kms", "x11", "test", "static", "wolf"), flag="--source"),
    Setting("kms_monitor", "kms_monitor", "int", N_("Capture"), N_("Monitor (KMS)"),
            N_("0 = the first connected monitor; the log says how many there are."), "capture", min=0, max=15,
            flag="--kms-monitor", show_if=("source", ("kms",))),
    Setting("window", "window", "bool", N_("Capture"), N_("Capture a window"),
            N_("The portal asks for a window instead of a monitor."), "capture", flag="--window",
            show_if=("source", ("portal",))),
    Setting("no_cursor", "no_cursor", "bool", N_("Capture"), N_("Hide the cursor"), "", "capture",
            flag="--no-cursor", show_if=("source", ("portal",))),
    Setting("wolf_target", "wolf_target", "text", N_("Capture"), N_("Target in Wolf"),
            N_("Lobby id or name, or session id. Empty = the only open lobby (with several, the log lists the "
               "options)."), "capture", flag="--wolf-target", show_if=("source", ("wolf",))),
    Setting("wolf_video_convert", "wolf_video_convert", "choice", N_("Capture"), N_("Conversion in Wolf"),
            N_("How Wolf brings the image down from the GPU: nvidia (CUDA), va (Intel/AMD) or cpu (Wolf without "
               "zero-copy). auto tries them in that order; the log says which one worked."), "capture",
            ("auto", "nvidia", "va", "cpu"), flag="--wolf-video-convert", show_if=("source", ("wolf",))),
    Setting("fps", "fps", "int", N_("Capture"), N_("FPS limit"),
            N_("On a 60 Hz screen, 30 is every other frame (even); 40 alternates 17 and 33 ms intervals."),
            "live", min=5, max=240, flag="--fps"),
    Setting("scale", "scale", "choice", N_("Capture"), N_("Scaling filter"),
            N_("bilinear2 (default) does not alias and makes smaller frames; lanczos makes text a little sharper, "
               "~2 ms more."),
            "capture", ("nearest-neighbour", "bilinear", "bilinear2", "lanczos", "mitchell", "catrom"),
            flag="--scale"),
    Setting("stretch", "stretch", "bool", N_("Capture"), N_("Stretch to the PSP screen"),
            N_("No black bars; the image loses its aspect ratio."), "capture", flag="--stretch"),
    # Vídeo
    Setting("codec", "codec_choice", "choice", N_("Video"), N_("Codec"),
            N_("auto = h264p with openh264. h264p: H.264 with P frames, ~10x fewer bytes. h264: every frame "
               "complete, copes better with losses at 2-3x the bandwidth. jpeg: without openh264."),
            "capture", ("auto", "h264p", "h264", "jpeg"), flag="--codec", arg="codec"),
    Setting("adaptive", "adaptive", "bool", N_("Video"), N_("Adaptive quality"),
            N_("Adjusts the quality to the measured Wi-Fi bandwidth."), "live", flag="--fixed-quality"),
    Setting("quality", "quality", "int", N_("Video"), N_("Quality"),
            N_("1-100. With adaptive quality, it is the starting one."), "live", min=1, max=100, flag="-q"),
    Setting("target_fps", "target_fps", "float", N_("Video"), N_("FPS the bandwidth must sustain"),
            N_("Adaptive: lower = more quality and more time per frame."), "live", min=5, max=60,
            flag="--target-fps", show_if=("adaptive", (True,))),
    Setting("q_min", "q_min", "int", N_("Video"), N_("Minimum quality"), "", "live", min=1, max=100,
            flag="--q-min", show_if=("adaptive", (True,))),
    Setting("q_max", "q_max", "int", N_("Video"), N_("Maximum quality"), "", "live", min=1, max=100,
            flag="--q-max", show_if=("adaptive", (True,))),
    # Som
    Setting("audio", "no_audio", "bool", N_("Audio"), N_("PC audio"),
            N_("The PSP also turns it on and off (SELECT + START + up). UDP only."), "audio", invert=True,
            flag="--no-audio"),
    Setting("audio_device", "audio_device", "text", N_("Audio"), N_("Audio source"),
            N_("monitor = what plays on the speakers (with the wolf source, Wolf's audio); test = 440 Hz tone; or "
               "a PipeWire source."), "audio",
            flag="--audio-device", show_if=("audio", (True,))),
    Setting("audio_rate", "audio_rate", "choice", N_("Audio"), N_("Rate (Hz)"),
            N_("44100 is the PSP's (no resampling). ~46 KB/s in stereo."), "audio", (22050, 32000, 44100, 48000),
            flag="--audio-rate", show_if=("audio", (True,))),
    Setting("audio_mono", "audio_mono", "bool", N_("Audio"), N_("Mono"), N_("Half the bytes."), "audio",
            flag="--audio-mono", show_if=("audio", (True,))),
    # Controles
    Setting("input", "no_input", "bool", N_("Controls"), N_("PSP controls on the PC"),
            N_("Needs access to /dev/uinput (PSPStream wiki, Installation)."), "input", invert=True,
            flag="--no-input"),
    Setting("profile", "profile", "choice", N_("Controls"), N_("Profile"),
            N_("game, desktop, arrows: keyboard and mouse. xbox*: virtual Xbox 360 controller."), "input",
            flag="--profile", show_if=("input", (True,))),
    Setting("mouse_speed", "mouse_speed", "float", N_("Controls"), N_("Mouse speed"),
            N_("Multiplies the profile's (keyboard and mouse profiles)."), "input", min=0.1, max=5,
            flag="--mouse-speed", show_if=("input", (True,))),
    # Rede
    Setting("dscp", "dscp", "choice", N_("Network"), N_("Wi-Fi priority (DSCP)"),
            N_("ef = WMM voice queue (default); cs5/af41 = video; 0 = none."), "live",
            ("ef", "cs5", "af41", "0"), flag="--dscp"),
    Setting("p_redundancy_ms", "p_redundancy_ms", "float", N_("Network"), N_("Copy of the last chunk (ms)"),
            N_("P frames: the last chunk of each frame is sent again after this; 0 turns it off."), "next",
            min=0, max=50, flag="--p-redundancy-ms"),
    Setting("port", "port", "int", N_("Network"), N_("Port"), N_("TCP and UDP. On the PSP, the same in server.txt."),
            "restart", min=1024, max=65535, flag="--port"),
)

BY_KEY = {s.key: s for s in SETTINGS}
BY_DEST = {s.dest: s for s in SETTINGS}


def cli_dest(setting: Setting) -> str:
    """Atributo do argparse que a opção da linha de comando preenche."""
    return setting.arg or setting.dest


def arg_value(setting: Setting, args) -> object:
    """Valor da configuração lido do args (com a inversão)."""
    value = getattr(args, setting.dest)
    return (not value) if setting.invert else value


def set_arg(setting: Setting, args, value) -> None:
    setattr(args, setting.dest, (not value) if setting.invert else value)


def coerce(setting: Setting, value, choices=None):
    """Valor de JSON -> valor da configuração. ValueError com a explicação."""
    kind = setting.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(tr("use true or false"))
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(tr("invalid number"))
        try:
            number = float(value)
        except ValueError:
            raise ValueError(tr("invalid number")) from None
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(tr("invalid number"))
        if kind == "int":
            if number != int(number):
                raise ValueError(tr("use a whole number"))
            number = int(number)
        if setting.min is not None and number < setting.min:
            raise ValueError(tr("the minimum is {min:g}").format(min=setting.min))
        if setting.max is not None and number > setting.max:
            raise ValueError(tr("the maximum is {max:g}").format(max=setting.max))
        return number
    if kind == "choice":
        allowed = tuple(choices) if choices is not None else setting.choices
        for option in allowed:
            if value == option or (isinstance(option, int) and isinstance(value, str) and value == str(option)):
                return option
        raise ValueError(tr("invalid option ({options})").format(options=", ".join(str(c) for c in allowed)))
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError(tr("invalid text"))
        value = value.strip()
        if setting.key == "audio_device" and not AUDIO_DEVICE_RE.match(value):
            raise ValueError(tr("invalid source name (letters, digits and . : @ + - _)"))
        if setting.key == "wolf_target" and not WOLF_TARGET_RE.match(value):
            raise ValueError(tr("up to 100 characters, no control characters"))
        return value
    raise ValueError(tr("unknown type: {kind}").format(kind=kind))


def check_together(values: dict) -> dict:
    """Regras entre configurações. {chave: erro}."""
    errors = {}
    if values.get("q_min") is not None and values.get("q_max") is not None and values["q_min"] > values["q_max"]:
        errors["q_min"] = tr("the minimum is above the maximum")
    return errors


def explicit_dests(parser: argparse.ArgumentParser, argv) -> set:
    """Atributos dados na linha de comando (não vindos do padrão)."""
    probe = argparse.ArgumentParser(add_help=False)
    for action in parser._actions:
        if action.option_strings and action.dest != "help" and not isinstance(action, argparse._VersionAction):
            kwargs = {"dest": action.dest, "default": argparse.SUPPRESS}
            if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                   argparse._StoreConstAction)):
                kwargs.update(action="store_const", const=action.const)
            else:
                kwargs.update(nargs=action.nargs, const=action.const)
            probe.add_argument(*action.option_strings, **kwargs)
    ns, _ = probe.parse_known_args(argv)
    return set(vars(ns))


def default_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home()) / "PSPStream"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pspstream"
    return base / "server.json"


class ConfigStore:
    """O server.json: só as configurações mudadas pela interface web que
    diferem do padrão."""

    def __init__(self, path, defaults: dict):
        self.path = Path(path)
        self.defaults = defaults  # chave -> padrão da linha de comando
        self.values = {}

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning(tr("settings %s ignored: %s"), self.path, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning(tr("settings %s ignored: not a JSON object"), self.path)
            return {}
        for key, value in raw.items():
            setting = BY_KEY.get(key)
            if setting is None:
                log.warning(tr("settings %s: '%s' does not exist, ignored"), self.path, key)
                continue
            try:
                if setting.kind == "choice" and not setting.choices:  # perfil: conferido ao abrir os controles
                    if not isinstance(value, str):
                        raise ValueError(tr("invalid option"))
                    self.values[key] = value
                else:
                    self.values[key] = coerce(setting, value)
            except ValueError as exc:
                log.warning(tr("settings %s: %s = %r ignored (%s)"), self.path, key, value, exc)
        for key, msg in check_together({**self.defaults, **self.values}).items():
            log.warning(tr("settings %s: %s ignored (%s)"), self.path, key, msg)
            self.values.pop(key, None)
        return dict(self.values)

    def update(self, changes: dict) -> None:
        for key, value in changes.items():
            if value == self.defaults.get(key):
                self.values.pop(key, None)
            else:
                self.values[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
