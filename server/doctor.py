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
from i18n import N_, tr

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


# Grupos e estados: identificadores internos; o texto mostrado passa por tr().
GROUPS = {"System": N_("System"), "GStreamer": "GStreamer", "Video": N_("Video"), "Capture": N_("Capture"),
          "Controls": N_("Controls"), "Audio": N_("Audio"), "Network": N_("Network")}
STATES = {"ok": "ok", "warn": N_("warn"), "missing": N_("missing"), "info": "info"}


@dataclass
class Item:
    group: str
    state: str           # ok | warn | missing | info
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
    items.append(Item("System", "ok" if fam else "warn",
                      pretty + ("" if fam else tr(" (unknown distribution: the commands below are Ubuntu's)"))))
    v = sys.version_info
    if v >= (3, 10):
        items.append(Item("System", "ok", f"Python {v.major}.{v.minor}.{v.micro}"))
    else:
        items.append(Item("System", "missing", tr("Python {version}: the server needs 3.10 or newer").format(
            version=f"{v.major}.{v.minor}"), essential=True))
    session = os.environ.get("XDG_SESSION_TYPE", "")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or "?"
    if session == "x11":
        items.append(Item("System", "info", tr("X11 session ({desktop}): use --source x11 (or --source kms)").format(
            desktop=desktop)))
    elif session == "wayland":
        items.append(Item("System", "ok", tr("Wayland session ({desktop}): capture through the portal (the default) "
                                             "or --source kms").format(desktop=desktop)))
    else:
        items.append(Item("System", "info", tr("no graphical session in this terminal (SSH?): run the server from "
                                               "inside the session, or use --source kms")))


def check_gstreamer(items):
    Gst = _gst()
    if Gst is None:
        # sem o gi não dá para conferir os plugins: sugere o conjunto todo
        items.append(Item("GStreamer", "missing", tr("PyGObject with GStreamer (gi): the server does not open without it"),
                          ("gi", "base", "good", "bad", "pipewire", "gl"), essential=True))
        return None
    major, minor, micro, _ = Gst.version()
    state = "ok" if (major, minor) >= (1, 20) else "warn"
    items.append(Item("GStreamer", state, tr("GStreamer {version} and PyGObject").format(version=f"{major}.{minor}.{micro}")
                      + ("" if state == "ok" else tr(": tested from 1.20 on"))))
    base = _missing(Gst, ("videoscale", "videoconvert", "appsink", "queue", "videotestsrc", "audioconvert",
                          "audioresample"))
    if base:
        items.append(Item("GStreamer", "missing", tr("basic elements: {names}").format(names=", ".join(base)), ("base",),
                          essential=True))
    else:
        items.append(Item("GStreamer", "ok", tr("basic elements (videoscale, videoconvert, appsink)")))
    checks = (
        (("pipewiresrc",), ("pipewire",), N_("capture through the portal (the default on Wayland)")),
        (("jpegenc",), ("good",), N_("JPEG (old EBOOT or --codec jpeg)")),
        (("pulsesrc",), ("good",), N_("audio (capture)")),
        (("adpcmenc",), ("bad",), N_("audio (IMA ADPCM)")),
        (("glupload", "gldownload", "glcolorscale"), ("gl",), N_("KMS capture and --dmabuf (scaling on the GPU)")),
        (("ximagesrc",), ("good",), N_("X11 capture (--source x11)")),
    )
    for names, needs, what in checks:
        missing = _missing(Gst, names)
        if missing:
            items.append(Item("GStreamer", "warn", f"{', '.join(missing)}: {tr(what)}", needs))
        else:
            items.append(Item("GStreamer", "ok", f"{', '.join(names)}: {tr(what)}"))
    return Gst


def check_openh264(items):
    try:
        import openh264
        lib, ver = openh264.library()
        items.append(Item("Video", "ok", tr("libopenh264 {version}: H.264 with P frames (the default)").format(
            version=f"{ver[0]}.{ver[1]}.{ver[2]}")))
    except Exception:  # noqa: BLE001 - ImportError, OpenH264Error, ctypes
        items.append(Item("Video", "warn", tr("libopenh264: without it the server sends JPEG (~10x more bytes per "
                                              "frame). Without a package in your distribution: Cisco's library "
                                              "(github.com/cisco/openh264/releases) in ~/.local/lib"), ("openh264",)))


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
        items.append(Item("Capture", "ok", tr("ScreenCast portal v{version}").format(version=version)
                          + (tr(" (remembers the chosen screen)") if version >= 4 else
                             tr(" (asks for the screen on every start)"))))
    except Exception as exc:  # noqa: BLE001 - PortalError, GLib.Error
        items.append(Item("Capture", "warn", tr("ScreenCast portal unavailable ({error}): install xdg-desktop-portal "
                                                "and your desktop's backend{backend}").format(
                              error=exc, backend=f" ({backend})" if backend else ""),
                          extra=["xdg-desktop-portal"] + ([backend] if backend else [])))


def check_kms(items):
    helper = paths.kms_helper()
    if not helper.exists():
        items.append(Item("Capture", "info", tr("KMS helper not built (optional: --source kms, 60 fps on GNOME 50+)"),
                          ("kms",), fix="make -C tools/kms && make -C tools/kms cap"))
        return
    if paths.has_cap_sys_admin(helper):
        ignored = paths.kms_cap_ignored(helper)
        if ignored:
            items.append(Item("Capture", "warn", tr("KMS capture: {problem}").format(problem=ignored)))
        else:
            items.append(Item("Capture", "ok", tr("KMS helper ready (--source kms)")))
    elif helper == paths.REPO_KMS_HELPER:
        items.append(Item("Capture", "warn", tr("KMS helper without permission to read the screen (optional: "
                                                "--source kms)"),
                          fix="make -C tools/kms cap   # " + tr("again after every make")))
    else:  # o do pacote: a permissão é opcional, e só o administrador dá
        items.append(Item("Capture", "info", tr("KMS capture (optional: --source kms, 60 fps on GNOME 50+): the "
                                                "package's helper needs permission to read the screen"),
                          fix=paths.kms_fix(helper)))


def check_input(items):
    try:
        import evdev  # noqa: F401
        evdev_ok = True
    except ImportError:
        evdev_ok = False
    dev = Path("/dev/uinput")
    writable = dev.exists() and os.access(dev, os.W_OK)
    if evdev_ok and writable:
        items.append(Item("Controls", "ok", tr("uinput and python-evdev: virtual keyboard, mouse and Xbox controller")))
        return
    problems = []
    if not evdev_ok:
        problems.append(tr("python-evdev is not installed"))
    if not dev.exists():
        problems.append(tr("/dev/uinput does not exist (uinput module)"))
    elif not writable:
        problems.append(tr("no write permission on /dev/uinput"))
    items.append(Item("Controls", "warn", "; ".join(problems) + tr(" (the server streams without the controls)"),
                      () if evdev_ok else ("evdev",), fix="" if writable else UINPUT_RULE))


def check_audio(items):
    if not shutil.which("pactl"):
        items.append(Item("Audio", "warn", tr("pactl not found: the audio uses the default output, without listing "
                                              "the sources"), ("pactl",)))
        return
    try:
        out = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    server = next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("Server Name")), "")
    if server:
        items.append(Item("Audio", "ok", tr("sound server: {name}").format(name=server)))
    else:
        items.append(Item("Audio", "warn", tr("pactl did not find this session's sound server (PipeWire/PulseAudio)")))


def check_network(items, port):
    for kind, label in ((socket.SOCK_DGRAM, "UDP"), (socket.SOCK_STREAM, "TCP")):
        with socket.socket(socket.AF_INET, kind) as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                items.append(Item("Network", "warn", tr("port {port}/{proto} in use (another server running?)").format(
                    port=port, proto=label)))
                break
    else:
        items.append(Item("Network", "ok", tr("port {port} free (TCP and UDP)").format(port=port)))
    fw = distro.firewall()
    if fw:
        items.append(Item("Network", "info", tr("{firewall} firewall active: if the PSP does not find the PC, open the "
                                                "port").format(firewall=fw), fix=distro.firewall_command(port, fw)))
    else:
        items.append(Item("Network", "ok", tr("no known firewall active (ufw, firewalld)")))


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
    colors = {"ok": "32", "warn": "33", "missing": "31", "info": "36"}
    lines, group = [], None
    for item in items:
        if item.group != group:
            group = item.group
            lines.append(f"\n{tr(GROUPS.get(group, group))}")
        tag = f"{tr(STATES[item.state]):<7}"
        if color:
            tag = f"\033[{colors[item.state]}m{tag}\033[0m"
        lines.append(f"  {tag} {item.text}")
    todo = [i for i in items if i.state in ("missing", "warn")]
    needs = [n for i in todo for n in i.needs]
    extra = [p for i in todo for p in i.extra]
    fam = distro.current()[0] or "debian"
    pkgs = distro.packages(needs, fam) + [p for p in extra if p not in distro.packages(needs, fam)]
    if pkgs:
        lines += ["", tr("To install what is missing:"), f"  {distro.INSTALL[fam]} {' '.join(pkgs)}"]
        notes = sorted({tr(distro.NOTES[(n, fam)]) for n in needs if (n, fam) in distro.NOTES})
        lines += [f"  ({note})" for note in notes]
    fixes = [i for i in items if i.fix and (i.state in ("missing", "warn") or i.group == "Network")]
    for item in fixes:
        lines += ["", f"{tr(GROUPS.get(item.group, item.group))}:"] + [f"  {line}" for line in item.fix.splitlines()]
    info = [i for i in items if i.fix and i.state == "info" and i.group != "Network"]
    for item in info:
        lines += ["", tr("Optional ({what}):").format(what=item.text.split(" (")[0])]
        if item.needs:
            lines.append(f"  {distro.install_command(item.needs, fam)}")
        lines += [f"  {line}" for line in item.fix.splitlines()]
    if not todo:
        lines += ["", tr("All set: python3 server/pspstream.py")]
    return "\n".join(lines).lstrip("\n")


def plan(items, fam: str) -> list:
    """Passos do --setup: [(título, [comandos])], só com o que falta."""
    todo = [i for i in items if i.state in ("missing", "warn")]
    needs = [n for i in todo for n in i.needs]
    pkgs = distro.packages(needs, fam)
    pkgs += [p for i in todo for p in i.extra if p not in pkgs]
    steps = []
    if pkgs and fam:
        steps.append((tr("Install the missing packages"), [f"{distro.INSTALL[fam]} {' '.join(pkgs)}"]))
    for item in todo:
        if item.fix and item.group == "Controls":
            steps.append((tr("Open /dev/uinput for the controls"), item.fix.replace("\\\n", "").splitlines()))
    kms = next((i for i in items if i.group == "Capture" and i.fix.startswith(("make", "sudo setcap"))), None)
    if kms is not None:
        if kms.fix.startswith("sudo setcap"):  # o auxiliar do pacote
            cmds = [kms.fix]
        else:
            cmds = ([distro.install_command(kms.needs, fam)] if kms.needs and fam else []) + [
                f"make -C {ROOT / 'tools' / 'kms'}", f"make -C {ROOT / 'tools' / 'kms'} cap"]
        steps.append((tr("KMS capture (optional: 60 fps on GNOME 50+; gives the helper permission to read the "
                         "screen)"), cmds))
    fw = next((i for i in items if i.group == "Network" and i.fix), None)
    if fw is not None:
        steps.append((tr("Open the port in the firewall"), [fw.fix]))
    return steps


def setup(port: int = 5123, version: str = "", ask=input) -> int:
    """Executa os passos do plan(), cada um depois de mostrar os comandos e
    perguntar. Os comandos saem do distro.py e deste arquivo, nunca da
    entrada do usuário."""
    fam = distro.current()[0]
    items = run_checks(port)
    print(tr("PSPStream {version}: preparing this machine ({system})").format(version=version, system=distro.current()[1])
          + "\n")
    if not fam:
        print(tr("Unknown distribution: install the packages as in {url}. The rest (uinput, firewall) follows below.")
              .format(url="https://github.com/k7vinilstorage/PSP-Stream/wiki/Installation") + "\n")
    steps = plan(items, fam)
    if not steps:
        print(tr("Nothing to do."))
    for title, cmds in steps:
        print(f"{title}:")
        for cmd in cmds:
            print(f"  {cmd}")
        try:
            answer = ask(tr("Run it? [y/N] ")).strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "s", "sim"):
            print("  " + tr("skipped") + "\n")
            continue
        for cmd in cmds:
            if subprocess.run(cmd, shell=True).returncode != 0:  # noqa: S602 - comandos fixos, ver acima
                print("  " + tr("failed: {command}").format(command=cmd) + "\n")
                break
        else:
            print("  " + tr("done") + "\n")
    import importlib
    importlib.invalidate_caches()  # o gi recém-instalado aparece no import
    print(tr("Checking again:") + "\n")
    return main(port, version)


def main(port: int = 5123, version: str = "") -> int:
    items = run_checks(port)
    head = (tr("PSPStream {version}: checking this machine").format(version=version) if version
            else tr("PSPStream: checking this machine")) + "\n"
    print(head + "\n" + report(items, color=sys.stdout.isatty()))
    return 1 if any(i.essential and i.state == "missing" for i in items) else 0


if __name__ == "__main__":
    sys.exit(main())
