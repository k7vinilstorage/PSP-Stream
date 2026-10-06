"""--check e --setup do servidor de Windows (o doctor.py é o do Linux).

O que o servidor de Windows usa: o GStreamer (de preferência o que vem junto
do pspstream.exe; senão o do instalador oficial) para o gst-launch, a
libopenh264 (a do runtime do GStreamer, ou a do Cisco que o --setup baixa),
o Pillow (JPEG) e o SendInput (teclado e mouse). O --setup só faz duas
coisas, perguntando antes: a regra do firewall (como administrador, pelo
UAC) e o download da DLL do openh264 do Cisco.
"""
import bz2
import hashlib
import os
import platform
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from doctor import GROUPS, STATES, Item  # noqa: F401 (GROUPS/STATES: o report usa)
from i18n import N_, tr

RULE = "PSPStream"
# A DLL do Cisco: a licença de patentes do H.264 vale para o binário baixado do Cisco, por isso o
# PSPStream não a distribui junto; o --setup baixa na máquina do usuário, como o Firefox.
OPENH264_VERSION = "2.4.1"
OPENH264_URL = ("https://github.com/cisco/openh264/releases/download/v{v}/openh264-{v}-win64.dll.bz2"
                .format(v=OPENH264_VERSION))
OPENH264_SHA256 = ""  # do .bz2; vazio = confere só que a DLL carrega e diz a versão certa


def _run(cmd, timeout=20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def check_system(items):
    items.append(Item("System", "ok", platform.platform()))
    v = sys.version_info
    items.append(Item("System", "ok", f"Python {v.major}.{v.minor}.{v.micro}"
                      + (tr(" (bundled)") if getattr(sys, "frozen", False) else "")))


def check_gstreamer(items):
    import gst_pipe
    gst = gst_pipe.find_gstreamer()
    if gst is None:
        items.append(Item("GStreamer", "missing", tr("GStreamer not found: the capture does not start without it. "
                                                     "Use the PSPStream build that bundles it, or install the "
                                                     "GStreamer runtime (MSVC 64-bit) from gstreamer.freedesktop.org"),
                          essential=True))
        return None
    ver = gst.version
    text = tr("GStreamer {version} ({where}: {path})").format(
        version=".".join(map(str, ver)) if ver else "?", where=gst.where, path=gst.bin)
    items.append(Item("GStreamer", "ok" if ver and ver >= (1, 22) else "warn",
                      text + ("" if ver and ver >= (1, 22) else tr(": tested from 1.22 on"))))
    checks = (
        (("videoconvert", "videoscale", "queue", "tcpclientsink"), "missing", N_("basic elements")),
        (("d3d11screencapturesrc",), "missing", N_("screen capture (Desktop Duplication)")),
        (("d3d11convert", "d3d11download"), "warn", N_("scaling on the GPU (without it, on the CPU)")),
        (("wasapi2src", "audioconvert", "audioresample", "adpcmenc"), "warn", N_("audio (WASAPI loopback, IMA ADPCM)")),
        (("videotestsrc",), "info", N_("test pattern (--source test)")),
    )
    for names, bad, what in checks:
        missing = [n for n in names if not gst.has(n)]
        if missing:
            items.append(Item("GStreamer", bad, f"{', '.join(missing)}: {tr(what)}", essential=bad == "missing"))
        else:
            items.append(Item("GStreamer", "ok", f"{', '.join(names)}: {tr(what)}"))
    return gst


def check_openh264(items):
    try:
        import openh264
        lib, ver = openh264.library()
        path = getattr(lib, "_name", "")
        items.append(Item("Video", "ok", tr("libopenh264 {version}: H.264 with P frames (the default)").format(
            version=f"{ver[0]}.{ver[1]}.{ver[2]}") + (f" ({path})" if path else "")))
    except Exception:  # noqa: BLE001 - OpenH264Error, ctypes
        items.append(Item("Video", "warn", tr("libopenh264 not found: without it the server sends JPEG (~10x more "
                                              "bytes per frame). pspstream --setup downloads Cisco's"),
                          fix="pspstream --setup"))
    import imaging
    if imaging.available():
        items.append(Item("Video", "ok", tr("Pillow: JPEG (old EBOOT or --codec jpeg) and still images")))
    else:
        items.append(Item("Video", "warn", tr("Pillow is missing (pip install pillow): no JPEG")))


def check_input(items):
    items.append(Item("Controls", "ok", tr("SendInput: keyboard and mouse")))
    items.append(Item("Controls", "info", tr("virtual Xbox controller: not available on Windows yet")))


def firewall_rule_exists() -> bool:
    return RULE in _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE}"])


def firewall_commands(port: int) -> list:
    return [f'netsh advfirewall firewall add rule name="{RULE}" dir=in action=allow protocol={proto} '
            f"localport={port} profile=private,domain" for proto in ("UDP", "TCP")]


def network_category() -> str:
    """Public, Private ou DomainAuthenticated da rede ativa ('' se não deu para saber)."""
    out = _run(["powershell", "-NoProfile", "-Command", "(Get-NetConnectionProfile).NetworkCategory"])
    return out.strip().splitlines()[0].strip() if out.strip() else ""


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
    if firewall_rule_exists():
        items.append(Item("Network", "ok", tr("firewall rule {rule}").format(rule=RULE)))
    else:
        items.append(Item("Network", "warn", tr("no firewall rule for the port: Windows blocks the PSP (pspstream "
                                                "--setup creates it, as administrator)"),
                          fix="\n".join(firewall_commands(port))))
    category = network_category()
    if category == "Public":
        items.append(Item("Network", "warn", tr("the network is Public: Windows blocks the PSP and the 'Find the PC' "
                                                "broadcast. In Settings > Network, make it Private")))
    elif category:
        items.append(Item("Network", "ok", tr("network profile: {category}").format(category=category)))


def run_checks(port: int = 5123) -> list:
    items = []
    check_system(items)
    check_gstreamer(items)
    check_openh264(items)
    check_input(items)
    check_network(items, port)
    return items


def report(items) -> str:
    lines, group = [], None
    for item in items:
        if item.group != group:
            group = item.group
            lines.append(f"\n{tr(GROUPS.get(group, group))}")
        lines.append(f"  {tr(STATES[item.state]):<7} {item.text}")
    fixes = [i for i in items if i.fix and i.state in ("missing", "warn")]
    for item in fixes:
        lines += ["", f"{tr(GROUPS.get(item.group, item.group))}:"] + [f"  {line}" for line in item.fix.splitlines()]
    if not any(i.state in ("missing", "warn") for i in items):
        lines += ["", tr("All set: pspstream")]
    return "\n".join(lines).lstrip("\n")


def main(port: int = 5123, version: str = "") -> int:
    items = run_checks(port)
    print(tr("PSPStream {version}: checking this machine").format(version=version) + "\n\n" + report(items))
    return 1 if any(i.essential and i.state == "missing" for i in items) else 0


def add_firewall_rule(port: int) -> bool:
    """As regras pelo netsh, como administrador (o Windows mostra o UAC)."""
    cmds = firewall_commands(port)
    script = "; ".join("Start-Process netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '{}'".format(
        c[len("netsh "):].replace("'", "''")) for c in cmds)
    _run(["powershell", "-NoProfile", "-Command", script], timeout=120)
    return firewall_rule_exists()


def download_openh264(dest_dir: Path = None) -> Path:
    """Baixa a DLL do Cisco para %LOCALAPPDATA%\\PSPStream\\lib e confere que ela carrega."""
    import openh264
    dest_dir = dest_dir or openh264.user_lib_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(OPENH264_URL, timeout=60) as resp:  # noqa: S310 - endereço fixo, https
        packed = resp.read()
    digest = hashlib.sha256(packed).hexdigest()
    if OPENH264_SHA256 and digest != OPENH264_SHA256:
        raise RuntimeError(tr("the downloaded file does not match the expected checksum ({digest})").format(
            digest=digest))
    dest = dest_dir / f"openh264-{OPENH264_VERSION}-win64.dll"
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(bz2.decompress(packed))
    os.replace(tmp, dest)
    return dest


def setup(port: int = 5123, version: str = "", ask=input) -> int:
    print(tr("PSPStream {version}: preparing this machine ({system})").format(version=version,
                                                                             system=platform.platform()) + "\n")

    def yes(question: str) -> bool:
        try:
            return ask(question).strip().lower() in ("y", "yes", "s", "sim")
        except EOFError:
            return False

    if not firewall_rule_exists():
        print(tr("Open the port in the firewall") + ":")
        for cmd in firewall_commands(port):
            print(f"  {cmd}")
        if yes(tr("Run it? [y/N] ")):
            print("  " + (tr("done") if add_firewall_rule(port) else tr("failed: {command}").format(
                command="netsh")) + "\n")
        else:
            print("  " + tr("skipped") + "\n")
    try:
        import openh264
        openh264.library()
        have_h264 = True
    except Exception:  # noqa: BLE001
        have_h264 = False
    if not have_h264:
        print(tr("Download Cisco's openh264 library (H.264 with P frames)") + ":")
        print(f"  {OPENH264_URL}")
        if yes(tr("Run it? [y/N] ")):
            try:
                path = download_openh264()
                print("  " + tr("done") + f": {path}\n")
            except Exception as exc:  # noqa: BLE001 - rede, bz2, disco
                print("  " + tr("failed: {command}").format(command=exc) + "\n")
        else:
            print("  " + tr("skipped") + "\n")
    print(tr("Checking again:") + "\n")
    import openh264
    openh264._lib = None  # procura de novo (a DLL recém-baixada)
    return main(port, version)
