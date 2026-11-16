"""--check e --setup do servidor de macOS (como doctor.py no Linux e win_doctor.py no Windows).

Confere GStreamer (avfvideosrc, osxaudiosrc, adpcmenc), libopenh264, Pillow,
permissões TCC do macOS (Gravação de Tela e Acessibilidade), firewall e pyusb.
"""
import bz2
import hashlib
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

from doctor import GROUPS, STATES, Item
from i18n import N_, tr
import paths

OPENH264_VERSION = "2.4.1"
OPENH264_URLS = {
    "arm64": f"https://github.com/cisco/openh264/releases/download/v{OPENH264_VERSION}/openh264-{OPENH264_VERSION}-mac-arm64.dylib.bz2",
    "x86_64": f"https://github.com/cisco/openh264/releases/download/v{OPENH264_VERSION}/openh264-{OPENH264_VERSION}-mac64.dylib.bz2",
}
OPENH264_SHA256 = {"arm64": "", "x86_64": ""}


def _run(cmd, timeout=10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _arch(arch: str = None) -> str:
    raw = (arch or platform.machine()).lower()
    if raw in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def openh264_url(arch: str = None) -> str:
    return OPENH264_URLS[_arch(arch)]


def check_system(items) -> None:
    items.append(Item("System", "ok", platform.platform()))
    v = sys.version_info
    if v >= (3, 10):
        items.append(Item("System", "ok", f"Python {v.major}.{v.minor}.{v.micro}"))
    else:
        items.append(Item("System", "missing", tr("Python {version}: the server needs 3.10 or newer").format(
            version=f"{v.major}.{v.minor}"), essential=True))
    brew = shutil.which("brew")
    if brew:
        items.append(Item("System", "ok", f"Homebrew ({brew})"))
    else:
        items.append(Item("System", "info", tr("Homebrew not found (optional, recommended for GStreamer, openh264 and libusb)")))


def check_gstreamer(items):
    import gst_pipe
    gst = gst_pipe.find_gstreamer()
    if gst is None:
        items.append(Item("GStreamer", "missing",
                          tr("GStreamer not found: install it with Homebrew (brew install gstreamer) "
                             "or from gstreamer.freedesktop.org"),
                          fix="brew install gstreamer", essential=True))
        return None
    ver = gst.version
    text = tr("GStreamer {version} ({where}: {path})").format(
        version=".".join(map(str, ver)) if ver else "?", where=gst.where, path=gst.bin)
    items.append(Item("GStreamer", "ok" if ver and ver >= (1, 22) else "warn",
                      text + ("" if ver and ver >= (1, 22) else tr(": tested from 1.22 on"))))
    checks = (
        (("videoconvert", "videoscale", "queue", "tcpclientsink"), "missing", N_("basic elements")),
        (("avfvideosrc",), "missing", N_("screen capture (AVFoundation)")),
        (("osxaudiosrc", "audioconvert", "audioresample", "adpcmenc"), "warn", N_("audio (CoreAudio, IMA ADPCM)")),
        (("videotestsrc",), "info", N_("test pattern (--source test)")),
    )
    for names, bad, what in checks:
        missing = [n for n in names if not gst.has(n)]
        if missing:
            items.append(Item("GStreamer", bad, f"{', '.join(missing)}: {tr(what)}",
                              fix="brew install gstreamer" if bad in ("missing", "warn") else "",
                              essential=bad == "missing"))
        else:
            items.append(Item("GStreamer", "ok", f"{', '.join(names)}: {tr(what)}"))
    return gst


def check_openh264(items) -> None:
    try:
        import openh264
        lib, ver = openh264.library()
        path = getattr(lib, "_name", "")
        items.append(Item("Video", "ok", tr("libopenh264 {version}: H.264 with P frames (the default)").format(
            version=f"{ver[0]}.{ver[1]}.{ver[2]}") + (f" ({path})" if path else "")))
    except Exception:  # noqa: BLE001 - OpenH264Error, ctypes
        items.append(Item("Video", "warn",
                          tr("libopenh264 not found: without it the server sends JPEG (~10x more bytes per frame). "
                             "Install with Homebrew (brew install openh264) or run pspstream --setup"),
                          fix="brew install openh264   # or: pspstream --setup"))
    import imaging
    if imaging.available():
        items.append(Item("Video", "ok", tr("Pillow: JPEG (old EBOOT or --codec jpeg) and still images")))
    else:
        items.append(Item("Video", "warn", tr("Pillow is missing (pip install pillow): no JPEG"),
                          fix="pip3 install pillow"))


def check_permissions(items) -> None:
    import mac_input
    if mac_input.screen_capture_access(prompt=False):
        items.append(Item("Capture", "ok", tr("Screen Recording permission granted")))
    else:
        items.append(Item("Capture", "warn",
                          tr("Screen Recording permission is off: macOS shows only the wallpaper or a black "
                             "screen until granted in System Settings > Privacy & Security > Screen Recording"),
                          fix="pspstream --setup"))
    if paths.sck_helper().is_file():
        items.append(Item("Capture", "ok",
                          tr("ScreenCaptureKit helper ready (60 fps screen and speaker audio loopback without "
                             "extra drivers)")))
    else:
        items.append(Item("Capture", "info",
                          tr("ScreenCaptureKit helper not built (optional: native 60 fps screen and speaker "
                             "audio without BlackHole)"),
                          fix=f"make -C {paths.ROOT / 'tools' / 'sck'}"))
    if mac_input.is_trusted(prompt=False):
        items.append(Item("Controls", "ok",
                          tr("Quartz Event Services: keyboard and mouse (Accessibility permission granted)")))
    else:
        items.append(Item("Controls", "warn",
                          tr("Accessibility permission is off: macOS blocks keyboard and mouse injection until "
                             "granted in System Settings > Privacy & Security > Accessibility"),
                          fix="pspstream --setup"))
    import mac_gamepad
    if mac_gamepad.crossover_installed():
        items.append(Item("Controls", "ok", tr("CrossOver virtual Xbox 360 controller ready (--profile xbox)")))
    elif mac_gamepad.find_crossover_unix_dir() is not None:
        items.append(Item("Controls", "info",
                          tr("CrossOver found: run pspstream --setup to enable the virtual Xbox 360 controller "
                             "(--profile xbox)"),
                          fix="pspstream --setup"))
    else:
        items.append(Item("Controls", "info",
                          tr("Xbox controller (--profile xbox): plug the PSP over USB or install CrossOver and "
                             "run pspstream --setup")))


def check_network(items, port: int) -> None:
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

    fw = _run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"]).strip()
    if "enabled" in fw.lower() or "state = 1" in fw.lower() or "state = 2" in fw.lower():
        items.append(Item("Network", "info",
                          tr("macOS firewall active: allow incoming connections for Python/PSPStream if the PSP "
                             "does not find the Mac")))
    elif fw:
        items.append(Item("Network", "ok", tr("macOS firewall disabled")))

    import usb_link
    if usb_link.is_available():
        items.append(Item("Network", "ok",
                          tr("USB transport: ready (pyusb and libusb; no udev rule or root needed on macOS)")))
    else:
        items.append(Item("Network", "info",
                          tr("USB transport: off, pyusb/libusb is not installed (optional: streaming over the "
                             "USB cable, transport=usb on the PSP)"),
                          fix="brew install libusb && pip3 install pyusb"))


def run_checks(port: int = 5123) -> list:
    items = []
    check_system(items)
    check_gstreamer(items)
    check_openh264(items)
    check_permissions(items)
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
    fixes = [i for i in items if i.fix and i.state in ("missing", "warn", "info")]
    seen_fix = set()
    for item in fixes:
        if item.fix in seen_fix:
            continue
        seen_fix.add(item.fix)
        lines += ["", f"{tr(GROUPS.get(item.group, item.group))}:"] + [f"  {line}" for line in item.fix.splitlines()]
    if not any(i.state in ("missing", "warn") for i in items):
        lines += ["", tr("All set: python3 server/pspstream.py")]
    return "\n".join(lines).lstrip("\n")


def main(port: int = 5123, version: str = "") -> int:
    items = run_checks(port)
    head = (tr("PSPStream {version}: checking this machine").format(version=version) if version
            else tr("PSPStream: checking this machine")) + "\n"
    print(head + "\n" + report(items, color=sys.stdout.isatty()))
    return 1 if any(i.essential and i.state == "missing" for i in items) else 0


def _download(url: str, timeout: float = 60) -> bytes:
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:  # noqa: S310 - URL fixa
        return resp.read()


def download_openh264(dest_dir: Path = None, arch: str = None) -> Path:
    """Baixa a libopenh264 da Cisco para ~/Library/Application Support/PSPStream/lib."""
    import openh264
    kind = _arch(arch)
    url = OPENH264_URLS[kind]
    expected = OPENH264_SHA256[kind]
    dest_dir = dest_dir or openh264.user_lib_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    packed = _download(url, 60)
    digest = hashlib.sha256(packed).hexdigest()
    if expected and digest != expected:
        raise RuntimeError(tr("the downloaded file does not match the expected checksum ({digest})").format(
            digest=digest))
    dest = dest_dir / f"libopenh264-{OPENH264_VERSION}.dylib"
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(bz2.decompress(packed))
    os.replace(tmp, dest)
    alias = dest_dir / "libopenh264.dylib"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    try:
        alias.symlink_to(dest.name)
    except OSError:
        shutil.copy2(dest, alias)
    return dest


def setup(port: int = 5123, version: str = "", ask=input) -> int:
    print(tr("PSPStream {version}: preparing this machine ({system})").format(version=version,
                                                                             system=platform.platform()) + "\n")

    def yes(question: str) -> bool:
        try:
            return ask(question).strip().lower() in ("y", "yes", "s", "sim")
        except EOFError:
            return False

    import gst_pipe
    brew = shutil.which("brew")

    if gst_pipe.find_gstreamer() is None:
        if brew:
            cmd = "brew install gstreamer"
            print(tr("Install GStreamer") + f":\n  {cmd}")
            if yes(tr("Run it? [y/N] ")):
                rc = subprocess.run(cmd, shell=True).returncode  # noqa: S602
                print("  " + (tr("done") if rc == 0 else tr("failed: {command}").format(command=cmd)) + "\n")
            else:
                print("  " + tr("skipped") + "\n")
        else:
            print(tr("Install Homebrew (https://brew.sh) and run: brew install gstreamer") + "\n")

    import openh264
    try:
        openh264.library()
        have_h264 = True
    except Exception:  # noqa: BLE001
        have_h264 = False
    if not have_h264:
        if brew:
            cmd = "brew install openh264"
            print(tr("Install openh264 (H.264 with P frames)") + f":\n  {cmd}")
            if yes(tr("Run it? [y/N] ")):
                rc = subprocess.run(cmd, shell=True).returncode  # noqa: S602
                print("  " + (tr("done") if rc == 0 else tr("failed: {command}").format(command=cmd)) + "\n")
            else:
                print("  " + tr("skipped") + "\n")
        else:
            url = openh264_url()
            print(tr("Download Cisco's openh264 library (H.264 with P frames)") + f":\n  {url}")
            if yes(tr("Run it? [y/N] ")):
                try:
                    path = download_openh264()
                    print("  " + tr("done") + f": {path}\n")
                except Exception as exc:  # noqa: BLE001
                    print("  " + tr("failed: {command}").format(command=exc) + "\n")
            else:
                print("  " + tr("skipped") + "\n")

    import mac_input
    if not paths.sck_helper().is_file() and shutil.which("clang") and (paths.ROOT / "tools" / "sck" / "Makefile").is_file():
        cmd = f"make -C {paths.ROOT / 'tools' / 'sck'}"
        print(tr("Build the ScreenCaptureKit helper (native screen and speaker audio capture)") + f":\n  {cmd}")
        if yes(tr("Run it? [y/N] ")):
            rc = subprocess.run(cmd, shell=True).returncode  # noqa: S602
            print("  " + (tr("done") if rc == 0 else tr("failed: {command}").format(command=cmd)) + "\n")
        else:
            print("  " + tr("skipped") + "\n")

    if not mac_input.screen_capture_access(prompt=False):
        print(tr("Request macOS Screen Recording permission (opens System Settings if needed)"))
        if yes(tr("Run it? [y/N] ")):
            mac_input.screen_capture_access(prompt=True)
            print("  " + tr("done") + "\n")
        else:
            print("  " + tr("skipped") + "\n")

    if not mac_input.is_trusted(prompt=False):
        print(tr("Request macOS Accessibility permission for keyboard and mouse controls"))
        if yes(tr("Run it? [y/N] ")):
            mac_input.is_trusted(prompt=True)
            print("  " + tr("done") + "\n")
        else:
            print("  " + tr("skipped") + "\n")

    import mac_gamepad
    if mac_gamepad.find_crossover_unix_dir() is not None and not mac_gamepad.crossover_installed():
        print(tr("Install the CrossOver virtual Xbox 360 controller shim (--profile xbox)"))
        if yes(tr("Run it? [y/N] ")):
            dylib = paths.crossover_sdl2()
            makefile = paths.ROOT / "tools" / "crossover" / "Makefile"
            if not dylib.is_file() and makefile.is_file() and shutil.which("clang"):
                cmd = f"make -C {makefile.parent}"
                rc = subprocess.run(cmd, shell=True).returncode  # noqa: S602
                if rc != 0:
                    print("  " + tr("failed: {command}").format(command=cmd) + "\n")
                    dylib = None
            if dylib is not None and dylib.is_file():
                try:
                    dest = mac_gamepad.install_crossover_shim(dylib)
                    print("  " + tr("done") + f": {dest}\n")
                except Exception as exc:  # noqa: BLE001
                    print("  " + tr("failed: {command}").format(command=exc) + "\n")
            elif dylib is not None:
                print("  " + tr("failed: {command}").format(command=dylib) + "\n")
        else:
            print("  " + tr("skipped") + "\n")

    print(tr("Checking again:") + "\n")
    openh264._lib = None
    gst_pipe.find_gstreamer.cache_clear()
    return main(port, version)


if __name__ == "__main__":
    sys.exit(main())
