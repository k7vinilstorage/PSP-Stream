"""Baixa o GStreamer de Windows (as wheels oficiais do PyPI, com os hashes de
gstreamer-wheels.txt) e junta tudo numa pasta com o layout do instalador:

  python packaging/windows/gstreamer.py C:\\gst      # C:\\gst\\bin\\gst-launch-1.0.exe, C:\\gst\\lib\\gstreamer-1.0, ...

Cada wheel traz um pedaço (gstreamer_cli/bin, gstreamer_libs/lib/gstreamer-1.0,
...); a pasta resultante é o que o servidor procura (PSPSTREAM_GSTREAMER) e o
que build.py --gstreamer recebe. Usado pelo CI; roda em qualquer sistema (só
baixa e descompacta).
"""
import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "gstreamer-wheels.txt"
# Só as pastas do runtime; o __init__.py de cada pacote fica de fora.
LAYOUT = ("bin", "lib", "libexec", "share", "etc")


def download(dest: Path) -> list:
    """Baixa as wheels de win_amd64 (sem dependências, hashes conferidos pelo pip)."""
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                    "--no-deps", "--only-binary=:all:", "--platform", "win_amd64",
                    "--python-version", "3.12", "--require-hashes", "-r", str(REQUIREMENTS),
                    "-d", str(dest)], check=True)
    return sorted(dest.glob("*.whl"))


def merge(wheels: list, root: Path) -> int:
    """Extrai <pacote>.data/purelib/<pacote>/{bin,lib,...} de cada wheel em root. Devolve o número de arquivos."""
    root = root.resolve()
    count = 0
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            for info in z.infolist():
                parts = info.filename.split("/")
                # gstreamer_libs-1.28.7.data/purelib/gstreamer_libs/bin/x.dll
                if len(parts) < 5 or parts[1] != "purelib" or parts[3] not in LAYOUT or info.is_dir():
                    continue
                target = (root / Path(*parts[3:])).resolve()
                if not target.is_relative_to(root):
                    raise SystemExit(f"{wheel.name}: {info.filename} points outside {root}")
                data = z.read(info)
                if target.is_file() and target.read_bytes() != data:
                    raise SystemExit(f"{wheel.name}: {info.filename} differs from the copy of another wheel")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="folder for the GStreamer runtime")
    ap.add_argument("--wheels", type=Path, help="use the wheels already in this folder instead of downloading")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        wheels = sorted(args.wheels.glob("*.whl")) if args.wheels else download(Path(tmp))
        if not wheels:
            raise SystemExit("no wheels")
        count = merge(wheels, args.root)
    launch = args.root / "bin" / "gst-launch-1.0.exe"
    if not launch.is_file():
        raise SystemExit(f"{launch} is missing after extracting {len(wheels)} wheels")
    print(f"{count} files from {len(wheels)} wheels in {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
