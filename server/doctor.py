"""Confere o que o servidor precisa nesta máquina e diz como instalar o que falta.

  python3 server/pspstream.py --check      (ou python3 server/doctor.py)

Funciona em qualquer distribuição: os nomes dos pacotes e o comando (apt,
dnf, pacman, zypper) saem do distro.py. Não muda nada no sistema: só lê e
imprime. Sai com 1 se falta algo sem o qual o servidor nem abre.
"""
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import distro
import paths

ROOT = Path(__file__).resolve().parent.parent
UINPUT_RULE = """echo uinput | sudo tee /etc/modules-load.d/uinput.conf
printf '%s\\n' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \\
  | sudo tee /etc/udev/rules.d/60-pspstream-uinput.rules
sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger"""
PORTAL_BACKENDS = (  # XDG_CURRENT_DESKTOP -> backend do portal
    ("gnome", "xdg-desktop-portal-gnome"), ("unity", "xdg-desktop-portal-gnome"),
    ("kde", "xdg-desktop-portal-kde"), ("hyprland", "xdg-desktop-portal-hyprland"),
    ("sway", "xdg-desktop-portal-wlr"), ("wlroots", "xdg-desktop-portal-wlr"), ("river", "xdg-desktop-portal-wlr"),
    ("cosmic", "xdg-desktop-portal-cosmic"), ("xfce", "xdg-desktop-portal-gtk"), ("cinnamon", "xdg-desktop-portal-xapp"),
    ("mate", "xdg-desktop-portal-xapp"),
)


@dataclass
class Item:
    group: str
    state: str           # ok | aviso | falta | info
    text: str
    needs: tuple = ()    # chaves do distro.PACKAGES que resolvem
    extra: list = field(default_factory=list)  # pacotes fora do distro.PACKAGES (backend do portal)
    fix: str = ""        # comando que não é instalar pacote
    essential: bool = False


def _gst():
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        return Gst
    except (ImportError, ValueError):
        return None


def _missing(Gst, names):
    return [n for n in names if Gst.ElementFactory.find(n) is None]


def check_system(items):
    _, pretty = distro.current()
    fam = distro.current()[0]
    items.append(Item("Sistema", "ok" if fam else "aviso",
                      pretty + ("" if fam else " (distribuição desconhecida: os comandos abaixo são do Ubuntu)")))
    v = sys.version_info
    if v >= (3, 10):
        items.append(Item("Sistema", "ok", f"Python {v.major}.{v.minor}.{v.micro}"))
    else:
        items.append(Item("Sistema", "falta", f"Python {v.major}.{v.minor}: o servidor precisa do 3.10 ou mais novo",
                          essential=True))
    session = os.environ.get("XDG_SESSION_TYPE", "")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if session == "x11":
        items.append(Item("Sistema", "info", f"sessão X11 ({desktop or '?'}): use --source x11 (ou --source kms)"))
    elif session == "wayland":
        items.append(Item("Sistema", "ok", f"sessão Wayland ({desktop or '?'}): captura pelo portal (o padrão) "
                                           "ou --source kms"))
    else:
        items.append(Item("Sistema", "info", "sem sessão gráfica neste terminal (SSH?): rode o servidor de dentro "
                                             "da sessão, ou use --source kms"))


def check_gstreamer(items):
    Gst = _gst()
    if Gst is None:
        # sem o gi não dá para conferir os plugins: sugere o conjunto todo
        items.append(Item("GStreamer", "falta", "PyGObject com o GStreamer (gi): sem ele o servidor não abre",
                          ("gi", "base", "good", "bad", "pipewire", "gl"), essential=True))
        return None
    major, minor, micro, _ = Gst.version()
    state = "ok" if (major, minor) >= (1, 20) else "aviso"
    items.append(Item("GStreamer", state, f"GStreamer {major}.{minor}.{micro} e PyGObject"
                      + ("" if state == "ok" else ": testado do 1.20 em diante")))
    base = _missing(Gst, ("videoscale", "videoconvert", "appsink", "queue", "videotestsrc", "audioconvert",
                          "audioresample"))
    if base:
        items.append(Item("GStreamer", "falta", "elementos básicos: " + ", ".join(base), ("base",), essential=True))
    else:
        items.append(Item("GStreamer", "ok", "elementos básicos (videoscale, videoconvert, appsink)"))
    checks = (
        (("pipewiresrc",), ("pipewire",), "captura pelo portal (o padrão no Wayland)"),
        (("jpegenc",), ("good",), "JPEG (EBOOT antigo ou --codec jpeg)"),
        (("pulsesrc",), ("good",), "som (captura)"),
        (("adpcmenc",), ("bad",), "som (IMA ADPCM)"),
        (("glupload", "gldownload", "glcolorscale"), ("gl",), "captura KMS e --dmabuf (redução na GPU)"),
        (("ximagesrc",), ("good",), "captura X11 (--source x11)"),
    )
    for names, needs, what in checks:
        missing = _missing(Gst, names)
        if missing:
            items.append(Item("GStreamer", "aviso", f"{', '.join(missing)}: {what}", needs))
        else:
            items.append(Item("GStreamer", "ok", f"{', '.join(names)}: {what}"))
    return Gst


def check_openh264(items):
    try:
        import openh264
        lib, ver = openh264.library()
        items.append(Item("Vídeo", "ok", f"libopenh264 {ver[0]}.{ver[1]}.{ver[2]}: H.264 com frames P (o padrão)"))
    except Exception:  # noqa: BLE001 - ImportError, OpenH264Error, ctypes
        items.append(Item("Vídeo", "aviso", "libopenh264: sem ela o servidor manda JPEG (~10x mais bytes por frame)."
                          " Sem o pacote na sua distribuição: a biblioteca do Cisco "
                          "(github.com/cisco/openh264/releases) em ~/.local/lib", ("openh264",)))


def check_portal(items, have_gst):
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    backend = next((pkg for key, pkg in PORTAL_BACKENDS if key in desktop), "")
    if not have_gst:
        return
    try:
        from portal import _Portal
        version = _Portal().prop("version")
        items.append(Item("Captura", "ok", f"portal ScreenCast v{version}"
                          + (" (lembra a tela escolhida)" if version >= 4 else " (pergunta a tela a cada partida)")))
    except Exception as exc:  # noqa: BLE001 - PortalError, GLib.Error
        items.append(Item("Captura", "aviso", f"portal ScreenCast indisponível ({exc}): instale o "
                          f"xdg-desktop-portal e o backend do seu ambiente{f' ({backend})' if backend else ''}",
                          extra=["xdg-desktop-portal"] + ([backend] if backend else [])))


def check_kms(items):
    helper = paths.kms_helper()
    if not helper.exists():
        items.append(Item("Captura", "info", "auxiliar KMS não compilado (opcional: --source kms, 60 fps no GNOME 50+)",
                          ("kms",), fix="make -C tools/kms && make -C tools/kms cap"))
        return
    caps = ""
    if shutil.which("getcap"):
        try:
            caps = subprocess.run(["getcap", str(helper)], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    if "cap_sys_admin" in caps:
        items.append(Item("Captura", "ok", "auxiliar KMS pronto (--source kms)"))
    elif helper == paths.REPO_KMS_HELPER:
        items.append(Item("Captura", "aviso", "auxiliar KMS sem a permissão de ler a tela (opcional: --source kms)",
                          fix="make -C tools/kms cap   # refaça depois de cada make"))
    else:  # o do pacote: a permissão é opcional, e só o administrador dá
        items.append(Item("Captura", "info", "captura KMS (opcional: --source kms, 60 fps no GNOME 50+): o auxiliar "
                          "do pacote precisa da permissão de ler a tela",
                          fix=f"sudo setcap cap_sys_admin+ep {helper}"))


def check_input(items):
    try:
        import evdev  # noqa: F401
        evdev_ok = True
    except ImportError:
        evdev_ok = False
    dev = Path("/dev/uinput")
    writable = dev.exists() and os.access(dev, os.W_OK)
    if evdev_ok and writable:
        items.append(Item("Controles", "ok", "uinput e python-evdev: teclado, mouse e controle de Xbox virtuais"))
        return
    problems = []
    if not evdev_ok:
        problems.append("python-evdev não instalado")
    if not dev.exists():
        problems.append("/dev/uinput não existe (módulo uinput)")
    elif not writable:
        problems.append("sem permissão de escrita no /dev/uinput")
    items.append(Item("Controles", "aviso", "; ".join(problems) + " (o servidor transmite sem os controles)",
                      () if evdev_ok else ("evdev",), fix="" if writable else UINPUT_RULE))


def check_audio(items):
    if not shutil.which("pactl"):
        items.append(Item("Som", "aviso", "pactl não encontrado: o som usa a saída padrão, sem listar as fontes",
                          ("pactl",)))
        return
    try:
        out = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    server = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("Server Name")), "")
    if server:
        items.append(Item("Som", "ok", f"servidor de som: {server}"))
    else:
        items.append(Item("Som", "aviso", "pactl não achou o servidor de som (PipeWire/PulseAudio) desta sessão"))


def check_network(items, port):
    for kind, label in ((socket.SOCK_DGRAM, "UDP"), (socket.SOCK_STREAM, "TCP")):
        with socket.socket(socket.AF_INET, kind) as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                items.append(Item("Rede", "aviso", f"porta {port}/{label} em uso (outro servidor rodando?)"))
                break
    else:
        items.append(Item("Rede", "ok", f"porta {port} livre (TCP e UDP)"))
    fw = distro.firewall()
    if fw:
        items.append(Item("Rede", "info", f"firewall {fw} ativo: se o PSP não achar o PC, libere a porta",
                          fix=distro.firewall_command(port, fw)))
    else:
        items.append(Item("Rede", "ok", "nenhum firewall ativo reconhecido (ufw, firewalld)"))


def run_checks(port: int = 5123) -> list:
    items = []
    check_system(items)
    gst = check_gstreamer(items)
    check_openh264(items)
    check_portal(items, gst is not None)
    check_kms(items)
    check_input(items)
    check_audio(items)
    check_network(items, port)
    return items


def report(items, color: bool = False) -> str:
    colors = {"ok": "32", "aviso": "33", "falta": "31", "info": "36"}
    lines, group = [], None
    for item in items:
        if item.group != group:
            group = item.group
            lines.append(f"\n{group}")
        tag = f"{item.state:<6}"
        if color:
            tag = f"\033[{colors[item.state]}m{tag}\033[0m"
        lines.append(f"  {tag} {item.text}")
    todo = [i for i in items if i.state in ("falta", "aviso")]
    needs = [n for i in todo for n in i.needs]
    extra = [p for i in todo for p in i.extra]
    fam = distro.current()[0] or "debian"
    pkgs = distro.packages(needs, fam) + [p for p in extra if p not in distro.packages(needs, fam)]
    if pkgs:
        lines += ["", "Para instalar o que falta:", f"  {distro.INSTALL[fam]} {' '.join(pkgs)}"]
        notes = sorted({distro.NOTES[(n, fam)] for n in needs if (n, fam) in distro.NOTES})
        lines += [f"  ({note})" for note in notes]
    fixes = [i for i in items if i.fix and (i.state in ("falta", "aviso") or i.group == "Rede")]
    for item in fixes:
        lines += ["", f"{item.group}:"] + [f"  {line}" for line in item.fix.splitlines()]
    info = [i for i in items if i.fix and i.state == "info" and i.group != "Rede"]
    for item in info:
        lines += ["", f"Opcional ({item.text.split(' (')[0]}):"]
        if item.needs:
            lines.append(f"  {distro.install_command(item.needs, fam)}")
        lines += [f"  {line}" for line in item.fix.splitlines()]
    if not todo:
        lines += ["", "Tudo pronto: python3 server/pspstream.py"]
    return "\n".join(lines).lstrip("\n")


def plan(items, fam: str) -> list:
    """Passos do --setup: [(título, [comandos])], só com o que falta."""
    todo = [i for i in items if i.state in ("falta", "aviso")]
    needs = [n for i in todo for n in i.needs]
    pkgs = distro.packages(needs, fam)
    pkgs += [p for i in todo for p in i.extra if p not in pkgs]
    steps = []
    if pkgs and fam:
        steps.append(("Instalar os pacotes que faltam", [f"{distro.INSTALL[fam]} {' '.join(pkgs)}"]))
    for item in todo:
        if item.fix and item.group == "Controles":
            steps.append(("Liberar o /dev/uinput para os controles", item.fix.replace("\\\n", "").splitlines()))
    kms = next((i for i in items if i.group == "Captura" and i.fix.startswith(("make", "sudo setcap"))), None)
    if kms is not None:
        if kms.fix.startswith("sudo setcap"):  # o auxiliar do pacote
            cmds = [kms.fix]
        else:
            cmds = ([distro.install_command(kms.needs, fam)] if kms.needs and fam else []) + [
                f"make -C {ROOT / 'tools' / 'kms'}", f"make -C {ROOT / 'tools' / 'kms'} cap"]
        steps.append(("Captura KMS (opcional: 60 fps no GNOME 50+; dá ao auxiliar a permissão de ler a tela)", cmds))
    fw = next((i for i in items if i.group == "Rede" and i.fix), None)
    if fw is not None:
        steps.append(("Liberar a porta no firewall", [fw.fix]))
    return steps


def setup(port: int = 5123, version: str = "", ask=input) -> int:
    """Executa os passos do plan(), cada um depois de mostrar os comandos e
    perguntar. Os comandos saem do distro.py e deste arquivo, nunca da
    entrada do usuário."""
    fam = distro.current()[0]
    items = run_checks(port)
    print(f"PSPStream {version}: preparando esta máquina ({distro.current()[1]})\n")
    if not fam:
        print("Distribuição desconhecida: instale os pacotes como em https://github.com/k7vinilstorage/PSP-Stream/wiki/Instalação. "
              "O resto (uinput, firewall) segue abaixo.\n")
    steps = plan(items, fam)
    if not steps:
        print("Nada a fazer.")
    for title, cmds in steps:
        print(f"{title}:")
        for cmd in cmds:
            print(f"  {cmd}")
        try:
            answer = ask("Executar? [s/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("s", "sim", "y", "yes"):
            print("  pulado\n")
            continue
        for cmd in cmds:
            if subprocess.run(cmd, shell=True).returncode != 0:  # noqa: S602 - comandos fixos, ver acima
                print(f"  falhou: {cmd}\n")
                break
        else:
            print("  feito\n")
    import importlib
    importlib.invalidate_caches()  # o gi recém-instalado aparece no import
    print("Conferindo de novo:\n")
    return main(port, version)


def main(port: int = 5123, version: str = "") -> int:
    items = run_checks(port)
    head = f"PSPStream {version}: conferindo esta máquina\n" if version else "PSPStream: conferindo esta máquina\n"
    print(head + "\n" + report(items, color=sys.stdout.isatty()))
    return 1 if any(i.essential and i.state == "falta" for i in items) else 0


if __name__ == "__main__":
    sys.exit(main())
