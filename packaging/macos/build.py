#!/usr/bin/env python3
"""Build the standalone macOS bundle and zip archive for PSPStream.

Builds tools/sck/pspstream-sck and tools/crossover/libSDL2-2.0.0.dylib, freezes
server/pspstream.py with PyInstaller into dist/PSPStream/, places both native
helpers alongside the executable, and packs dist/PSPStream-macOS-<arch>.zip.
"""
import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

NOTICE = """PSPStream {version} for macOS

This bundle contains only the Python server, web interface, the native
ScreenCaptureKit helper (tools/sck/pspstream-sck, Apple macOS 13+ system
frameworks), and the CrossOver SDL2 virtual controller proxy
(tools/crossover/libSDL2-2.0.0.dylib).

Runtime dependencies installed via Homebrew or `pspstream --setup`:
  - GStreamer (LGPL-2.1+): brew install gstreamer
  - OpenH264 (BSD-2-Clause, Cisco binary download or Homebrew): brew install openh264
  - libusb (LGPL-2.1+, required for --usb): brew install libusb
  - Pillow (HPND / MIT-CMU) and PyUSB (BSD-3-Clause)
"""


def arch_tag() -> str:
    """Normalized architecture tag for archive filenames."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine or "macos"


def version() -> str:
    """Resolve package version using the same convention as packaging/version.sh."""
    env = os.environ.get("PSPSTREAM_VERSION")
    if env:
        return env
    try:
        tag = subprocess.check_output(
            ["git", "-C", str(ROOT), "describe", "--tags", "--exact-match", "--match", "v[0-9]*"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if tag:
            return tag.removeprefix("v")
    except (OSError, subprocess.CalledProcessError):
        pass

    text = (ROOT / "server" / "pspstream.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    base = m.group(1) if m else "0.0"
    try:
        when = subprocess.check_output(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cd", "--date=format:%Y%m%d"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        sha = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return f"{base}+local"
    return f"{base}~git{when}.{sha}"


def build_sck_helper() -> Path:
    """Compile the native ScreenCaptureKit helper in tools/sck."""
    sck_dir = ROOT / "tools" / "sck"
    subprocess.run(["make", "-C", str(sck_dir)], check=True)
    helper = sck_dir / "pspstream-sck"
    if not helper.is_file():
        raise FileNotFoundError(f"ScreenCaptureKit helper missing after build: {helper}")
    return helper


def build_crossover_sdl2() -> Path:
    """Compile the universal SDL2 proxy dylib for CrossOver in tools/crossover."""
    crossover_dir = ROOT / "tools" / "crossover"
    subprocess.run(["make", "-C", str(crossover_dir)], check=True)
    dylib = crossover_dir / "libSDL2-2.0.0.dylib"
    if not dylib.is_file():
        raise FileNotFoundError(f"CrossOver SDL2 proxy dylib missing after build: {dylib}")
    return dylib


def bundle_openh264(app: Path) -> None:
    """Copy libopenh264 dylibs into the bundle if explicitly requested."""
    sys.path.insert(0, str(ROOT / "server"))
    import openh264

    lib_path = Path(openh264.library())
    dest_dir = app / "lib"
    dest_dir.mkdir(exist_ok=True)
    shutil.copy2(lib_path, dest_dir / lib_path.name)


def pyinstaller_cmd() -> list[str]:
    """Use python -m PyInstaller when installed in this env, else the pyinstaller CLI (e.g. Homebrew)."""
    import importlib.util
    if importlib.util.find_spec("PyInstaller") is not None:
        return [sys.executable, "-m", "PyInstaller"]
    exe = shutil.which("pyinstaller")
    if exe:
        return [exe]
    raise SystemExit("PyInstaller not found: install with `brew install pyinstaller` or `pip install pyinstaller`")


def pack_zip(app: Path, archive: Path) -> None:
    """Create a zip archive preserving Mach-O executable permissions and symlinks."""
    if archive.exists():
        archive.unlink()
    ditto = shutil.which("ditto")
    if ditto:
        subprocess.run(
            [ditto, "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)],
            check=True,
        )
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", str(app.parent), app.name)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(ROOT / "dist"), help="output directory (default: dist)")
    p.add_argument("--no-pyinstaller", action="store_true",
                   help="skip PyInstaller step and reuse an existing PSPStream bundle")
    p.add_argument("--bundle-openh264", action="store_true",
                   help="also copy libopenh264 into <out>/PSPStream/lib/")
    args = p.parse_args(argv)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = out / "PSPStream"
    ver = version()

    helper = build_sck_helper()
    crossover_dylib = build_crossover_sdl2()

    if not args.no_pyinstaller:
        work = out / ".pyinstaller-work"
        if app.exists():
            shutil.rmtree(app)
        if work.exists():
            shutil.rmtree(work)
        subprocess.run(
            [
                *pyinstaller_cmd(),
                "--noconfirm", "--clean",
                "--distpath", str(out),
                "--workpath", str(work),
                str(HERE / "pspstream.spec"),
            ],
            check=True,
        )
        shutil.rmtree(work, ignore_errors=True)
    elif not app.is_dir():
        raise SystemExit(f"--no-pyinstaller set, but bundle directory does not exist: {app}")

    dest_helper = app / "pspstream-sck"
    shutil.copy2(helper, dest_helper)
    dest_helper.chmod(0o755)

    dest_crossover = app / "libSDL2-2.0.0.dylib"
    shutil.copy2(crossover_dylib, dest_crossover)
    dest_crossover.chmod(0o755)

    for doc in ("README.md", "README.pt-BR.md", "LICENSE"):
        src = ROOT / doc
        if src.is_file():
            shutil.copy2(src, app / doc)
    (app / "VERSION.txt").write_text(f"{ver}\n", encoding="utf-8")
    (app / "THIRD-PARTY.txt").write_text(NOTICE.format(version=ver), encoding="utf-8")

    if args.bundle_openh264:
        bundle_openh264(app)

    archive = out / f"PSPStream-macOS-{arch_tag()}.zip"
    pack_zip(app, archive)
    print(f"Built {app}")
    print(f"Packed {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
