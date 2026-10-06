"""Monta o servidor de Windows: dist/PSPStream/ (pspstream.exe, o GStreamer
reduzido, os textos) e dist/PSPStream-Windows-x64.zip.

  python packaging/windows/build.py --gstreamer C:\\caminho\\do\\gstreamer\\1.0\\msvc_x86_64

Usado pelo CI (.github/workflows/build.yml, job windows). Precisa de
pyinstaller, pillow, pefile e truststore (pip). O GStreamer é o runtime oficial (MSVC
64 bits): o do instalador ou o das wheels do PyPI juntadas por gstreamer.py;
daqui só vão os plugins que o servidor usa e as DLLs de que eles dependem,
achadas pela tabela de importações de cada arquivo (~20 MB em vez de ~300).
"""
import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Plugins (lib/gstreamer-1.0/gst<nome>.dll) e para quê.
PLUGINS = {
    "coreelements": "queue, fakesink",
    "videoconvertscale": "videoconvert, videoscale (GStreamer 1.22+)",
    "videotestsrc": "--source test",
    "audiotestsrc": "--audio-device test",
    "audioconvert": "audio",
    "audioresample": "audio",
    "tcp": "tcpclientsink (frames and audio blocks to the server)",
    "d3d11": "d3d11screencapturesrc, d3d11convert, d3d11download",
    "wasapi2": "wasapi2src (audio loopback)",
    "adpcmenc": "IMA ADPCM",
}
OLD_PLUGINS = {"videoconvertscale": ("videoconvert", "videoscale")}  # antes do 1.22
TOOLS = ("bin/gst-launch-1.0.exe", "bin/gst-inspect-1.0.exe", "libexec/gstreamer-1.0/gst-plugin-scanner.exe")
# O runtime do Visual C++ vem da pasta do sistema se o GStreamer não o trouxer (implantação local,
# permitida pela licença do redistribuível): o gst-launch não depende do que está instalado.
VC_RUNTIME = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")


def dll_imports(path: Path) -> list:
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                           pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
    names += [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
    pe.close()
    return names


def bundle_gstreamer(src: Path, dest: Path) -> list:
    """Copia as ferramentas, os plugins e o fecho das DLLs de que eles dependem. Devolve os plugins que faltaram."""
    bin_dir = src / "bin"
    available = {p.name.lower(): p for p in bin_dir.glob("*.dll")}
    system = Path(r"C:\Windows\System32")
    todo, missing = [], []
    for rel in TOOLS:
        if not (src / rel).is_file():
            raise SystemExit(f"{src / rel} does not exist")
        todo.append(src / rel)
    plugin_dir = src / "lib" / "gstreamer-1.0"
    for name in PLUGINS:
        files = [plugin_dir / f"gst{n}.dll" for n in OLD_PLUGINS.get(name, ())] \
            if not (plugin_dir / f"gst{name}.dll").is_file() else [plugin_dir / f"gst{name}.dll"]
        found = [f for f in files if f.is_file()]
        if not found:
            missing.append(name)
        todo += found
    # A libopenh264 do runtime do GStreamer (a do plugin openh264): o encoder do servidor a usa direto.
    todo += [p for p in bin_dir.glob("*openh264*.dll")]
    copied = {}
    while todo:
        path = todo.pop()
        rel = path.relative_to(src) if path.is_relative_to(src) else Path("bin") / path.name
        if str(rel).lower() in copied:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied[str(rel).lower()] = target
        for dep in dll_imports(path):
            key = dep.lower()
            if key in available:
                todo.append(available[key])
            elif key in VC_RUNTIME and (system / dep).is_file():
                todo.append(system / dep)
    return missing


def version() -> str:
    text = (ROOT / "server" / "pspstream.py").read_text(encoding="utf-8")
    base = re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1)
    try:
        tag = subprocess.run(["git", "describe", "--tags", "--exact-match"], cwd=ROOT, capture_output=True,
                             text=True).stdout.strip()
        if tag.startswith("v"):
            return tag[1:]
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True).stdout.strip()
        return f"{base}-dev+{sha}" if sha else base
    except OSError:
        return base


NOTICE = """PSPStream {version} for Windows

This folder bundles third-party software:

- Python {python} (PSF License), packaged with PyInstaller.
- Pillow (MIT-CMU License): JPEG and still images.
- truststore (MIT): HTTPS downloads (--setup) checked with Windows' own certificates.
- GStreamer {gst} (LGPL-2.1 or later), only the plugins PSPStream uses and the
  libraries they need, unmodified, from the official Windows binaries
  (https://gstreamer.freedesktop.org/download/, also published on PyPI as
  gstreamer-libs and gstreamer-plugins). Source code:
  https://gstreamer.freedesktop.org/src/
{openh264}{vigem}
PSPStream itself is MIT licensed (LICENSE).
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gstreamer", required=True, type=Path, help="GStreamer runtime root (has bin/ and lib/)")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--vigem", type=Path, help="folder with ViGEmClient.dll and LICENSE.txt (vigem.py): the "
                                               "virtual Xbox controller")
    args = ap.parse_args()
    out = args.out.resolve()
    work = out / "pyinstaller-work"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(out),
                    "--workpath", str(work), str(ROOT / "packaging" / "windows" / "pspstream.spec")], check=True)
    app = out / "PSPStream"
    missing = bundle_gstreamer(args.gstreamer, app / "gstreamer")
    if missing:
        print(f"warning: GStreamer plugins not found: {', '.join(missing)}")
    if any(m in ("coreelements", "videoconvertscale", "tcp", "d3d11") for m in missing):
        raise SystemExit("essential GStreamer plugins are missing")
    for name in ("LICENSE", "README.md", "README.pt-BR.md", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, app / name)
    if args.vigem:
        (app / "vigem").mkdir(exist_ok=True)
        for name in ("ViGEmClient.dll", "LICENSE.txt"):
            shutil.copy2(args.vigem / name, app / "vigem" / name)
    else:
        print("warning: no --vigem: the package goes without the virtual Xbox controller")
    gst_version = ""
    try:
        gst_version = subprocess.run([str(args.gstreamer / "bin" / "gst-launch-1.0.exe"), "--version"],
                                     capture_output=True, text=True).stdout.splitlines()[1].split()[-1]
    except (OSError, IndexError):
        pass
    has_h264 = any((app / "gstreamer" / "bin").glob("*openh264*.dll"))
    v = sys.version_info
    (app / "THIRD-PARTY.txt").write_text(NOTICE.format(
        version=version(), python=f"{v.major}.{v.minor}.{v.micro}", gst=gst_version,
        openh264=("- openh264 (BSD-2-Clause), the library from the GStreamer runtime. To use Cisco's\n"
                  "  binary instead (covered by Cisco's H.264 patent license), run pspstream --setup.\n")
        if has_h264 else "",
        vigem=("- ViGEmClient (MIT, vigem/LICENSE.txt), built from https://github.com/nefarius/ViGEmClient:\n"
               "  the virtual Xbox controller, through the ViGEmBus driver (installed separately).\n")
        if args.vigem else ""), encoding="utf-8")
    (app / "VERSION.txt").write_text(version() + "\n", encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    zip_path = out / "PSPStream-Windows-x64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, Path("PSPStream") / f.relative_to(app))
    size = sum(f.stat().st_size for f in app.rglob("*") if f.is_file())
    print(f"{app}: {size / 1e6:.0f} MB; {zip_path}: {zip_path.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
