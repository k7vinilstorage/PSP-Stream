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
Windows (wiki/Servidor-para-Windows.md).
"""
import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pspstream.settings")

APPLY_TEXT = {
    "live": "vale na hora",
    "capture": "refaz a captura (o PSP continua conectado)",
    "audio": "refaz a captura do som",
    "input": "refaz os controles",
    "next": "vale na próxima conexão do PSP",
    "restart": "vale ao reiniciar o servidor",
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
    # Captura
    Setting("source", "source", "choice", "Captura", "Fonte",
            "portal: a tela pelo portal do Wayland (pede permissão na primeira vez). kms: direto da placa de "
            "vídeo, 60 fps no GNOME 50 (precisa do auxiliar: make -C tools/kms). x11: sessão X11. "
            "test: padrão animado. static: imagem fixa. wolf: o que roda no Wolf (Games on Whales); aparece "
            "quando o socket da API do Wolf existe.",
            "capture", ("portal", "kms", "x11", "test", "static", "wolf"), flag="--source"),
    Setting("kms_monitor", "kms_monitor", "int", "Captura", "Monitor (KMS)",
            "0 = o primeiro monitor ligado; o log diz quantos há.", "capture", min=0, max=15,
            flag="--kms-monitor", show_if=("source", ("kms",))),
    Setting("window", "window", "bool", "Captura", "Capturar uma janela",
            "O portal pergunta qual janela, em vez de um monitor.", "capture", flag="--window",
            show_if=("source", ("portal",))),
    Setting("no_cursor", "no_cursor", "bool", "Captura", "Esconder o cursor", "", "capture",
            flag="--no-cursor", show_if=("source", ("portal",))),
    Setting("wolf_target", "wolf_target", "text", "Captura", "Alvo no Wolf",
            "Id ou nome do lobby, ou id da sessão. Vazio = o único lobby aberto (com vários, o log lista as "
            "opções).", "capture", flag="--wolf-target", show_if=("source", ("wolf",))),
    Setting("wolf_video_convert", "wolf_video_convert", "choice", "Captura", "Conversão no Wolf",
            "Como o Wolf desce a imagem da GPU: nvidia (CUDA), va (Intel/AMD) ou cpu (Wolf sem zero-copy). "
            "auto tenta nessa ordem; o log diz qual funcionou.", "capture", ("auto", "nvidia", "va", "cpu"),
            flag="--wolf-video-convert", show_if=("source", ("wolf",))),
    Setting("fps", "fps", "int", "Captura", "Limite de FPS",
            "Numa tela de 60 Hz, 30 é um frame sim, um não (uniforme); 40 alterna intervalos de 17 e 33 ms.",
            "live", min=5, max=240, flag="--fps"),
    Setting("scale", "scale", "choice", "Captura", "Filtro de redução",
            "bilinear2 (padrão) não serrilha e gera frames menores; lanczos deixa o texto um pouco mais "
            "nítido, com ~2 ms a mais.",
            "capture", ("nearest-neighbour", "bilinear", "bilinear2", "lanczos", "mitchell", "catrom"),
            flag="--scale"),
    Setting("stretch", "stretch", "bool", "Captura", "Esticar para a tela do PSP",
            "Sem as bordas pretas; a imagem perde a proporção.", "capture", flag="--stretch"),
    # Vídeo
    Setting("codec", "codec_choice", "choice", "Vídeo", "Codec",
            "auto = h264p com o openh264. h264p: H.264 com frames P, ~10x menos bytes. h264: todo frame "
            "completo, aguenta perdas melhor com 2-3x mais banda. jpeg: sem openh264.",
            "capture", ("auto", "h264p", "h264", "jpeg"), flag="--codec", arg="codec"),
    Setting("adaptive", "adaptive", "bool", "Vídeo", "Qualidade adaptativa",
            "Ajusta a qualidade à banda medida do Wi-Fi.", "live", flag="--fixed-quality"),
    Setting("quality", "quality", "int", "Vídeo", "Qualidade",
            "1-100. Com a adaptativa, é a inicial.", "live", min=1, max=100, flag="-q"),
    Setting("target_fps", "target_fps", "float", "Vídeo", "FPS que a banda tem de sustentar",
            "Adaptativa: menor = mais qualidade e mais tempo por frame.", "live", min=5, max=60,
            flag="--target-fps", show_if=("adaptive", (True,))),
    Setting("q_min", "q_min", "int", "Vídeo", "Qualidade mínima", "", "live", min=1, max=100,
            flag="--q-min", show_if=("adaptive", (True,))),
    Setting("q_max", "q_max", "int", "Vídeo", "Qualidade máxima", "", "live", min=1, max=100,
            flag="--q-max", show_if=("adaptive", (True,))),
    # Som
    Setting("audio", "no_audio", "bool", "Som", "Som do PC",
            "O PSP também liga e desliga (SELECT + START + cima). Só pelo UDP.", "audio", invert=True,
            flag="--no-audio"),
    Setting("audio_device", "audio_device", "text", "Som", "Fonte do som",
            "monitor = o que sai nas caixas (com a fonte wolf, o som do Wolf); test = tom de 440 Hz; ou uma "
            "fonte do PipeWire.", "audio",
            flag="--audio-device", show_if=("audio", (True,))),
    Setting("audio_rate", "audio_rate", "choice", "Som", "Taxa (Hz)",
            "44100 é a do PSP (sem reamostrar). ~46 KB/s em estéreo.", "audio", (22050, 32000, 44100, 48000),
            flag="--audio-rate", show_if=("audio", (True,))),
    Setting("audio_mono", "audio_mono", "bool", "Som", "Mono", "Metade dos bytes.", "audio",
            flag="--audio-mono", show_if=("audio", (True,))),
    # Controles
    Setting("input", "no_input", "bool", "Controles", "Controles do PSP no PC",
            "Precisa de acesso ao /dev/uinput (wiki do PSPStream, Instalação).", "input", invert=True,
            flag="--no-input"),
    Setting("profile", "profile", "choice", "Controles", "Perfil",
            "jogo, desktop, setas: teclado e mouse. xbox*: controle de Xbox 360 virtual.", "input",
            flag="--profile", show_if=("input", (True,))),
    Setting("mouse_speed", "mouse_speed", "float", "Controles", "Velocidade do mouse",
            "Multiplica a do perfil (perfis de teclado e mouse).", "input", min=0.1, max=5, flag="--mouse-speed",
            show_if=("input", (True,))),
    # Rede
    Setting("dscp", "dscp", "choice", "Rede", "Prioridade no Wi-Fi (DSCP)",
            "ef = fila de voz do WMM (padrão); cs5/af41 = vídeo; 0 = nenhuma.", "live",
            ("ef", "cs5", "af41", "0"), flag="--dscp"),
    Setting("p_redundancy_ms", "p_redundancy_ms", "float", "Rede", "Cópia do último pedaço (ms)",
            "Frames P: o último pedaço de cada frame vai de novo depois disso; 0 desliga.", "next",
            min=0, max=50, flag="--p-redundancy-ms"),
    Setting("port", "port", "int", "Rede", "Porta", "TCP e UDP. No PSP, a mesma no server.txt.", "restart",
            min=1024, max=65535, flag="--port"),
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
        raise ValueError("use verdadeiro ou falso")
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("número inválido")
        try:
            number = float(value)
        except ValueError:
            raise ValueError("número inválido") from None
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError("número inválido")
        if kind == "int":
            if number != int(number):
                raise ValueError("use um número inteiro")
            number = int(number)
        if setting.min is not None and number < setting.min:
            raise ValueError(f"o mínimo é {setting.min:g}")
        if setting.max is not None and number > setting.max:
            raise ValueError(f"o máximo é {setting.max:g}")
        return number
    if kind == "choice":
        allowed = tuple(choices) if choices is not None else setting.choices
        for option in allowed:
            if value == option or (isinstance(option, int) and isinstance(value, str) and value == str(option)):
                return option
        raise ValueError("opção inválida (" + ", ".join(str(c) for c in allowed) + ")")
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError("texto inválido")
        value = value.strip()
        if setting.key == "audio_device" and not AUDIO_DEVICE_RE.match(value):
            raise ValueError("nome de fonte inválido (letras, números e . : @ + - _)")
        if setting.key == "wolf_target" and not WOLF_TARGET_RE.match(value):
            raise ValueError("até 100 caracteres, sem caracteres de controle")
        return value
    raise ValueError(f"tipo desconhecido: {kind}")


def check_together(values: dict) -> dict:
    """Regras entre configurações. {chave: erro}."""
    errors = {}
    if values.get("q_min") is not None and values.get("q_max") is not None and values["q_min"] > values["q_max"]:
        errors["q_min"] = "a mínima passa da máxima"
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
            log.warning("configuração %s ignorada: %s", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning("configuração %s ignorada: não é um objeto JSON", self.path)
            return {}
        for key, value in raw.items():
            setting = BY_KEY.get(key)
            if setting is None:
                log.warning("configuração %s: '%s' não existe, ignorada", self.path, key)
                continue
            try:
                if setting.kind == "choice" and not setting.choices:  # perfil: conferido ao abrir os controles
                    if not isinstance(value, str):
                        raise ValueError("opção inválida")
                    self.values[key] = value
                else:
                    self.values[key] = coerce(setting, value)
            except ValueError as exc:
                log.warning("configuração %s: %s = %r ignorada (%s)", self.path, key, value, exc)
        for key, msg in check_together({**self.defaults, **self.values}).items():
            log.warning("configuração %s: %s ignorada (%s)", self.path, key, msg)
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
