"""Monta o instalador do Windows (dist/PSPStream-Setup-x64.exe) com o Inno Setup, a partir da pasta
que o build.py deixou em dist/PSPStream:

  python packaging/windows/installer.py [--app dist/PSPStream] [--out dist]

A versão vem do VERSION.txt do build, e o endereço, o nome e o SHA-256 do instalador do ViGEmBus
vêm do server/win_doctor.py (o mesmo que o --setup baixa), para não haver duas cópias. Precisa do
Inno Setup 6 (ISCC.exe); usado pelo CI depois do build.py.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "packaging" / "windows" / "pspstream.iss"


def iscc() -> str:
    for candidate in (shutil.which("iscc"),
                      Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
                      Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit("ISCC.exe (Inno Setup 6) not found")


def defines(app: Path, out: Path) -> list:
    sys.path.insert(0, str(ROOT / "server"))
    import win_doctor
    version = (app / "VERSION.txt").read_text(encoding="utf-8").strip()
    return [f"/DAppVersion={version}", f"/DSourceDir={app}", f"/DOutputDir={out}",
            f"/DViGEmBusURL={win_doctor.VIGEMBUS_URL}", f"/DViGEmBusFile={win_doctor.VIGEMBUS_FILE}",
            f"/DViGEmBusSHA256={win_doctor.VIGEMBUS_SHA256}", f"/DViGEmBusVersion={win_doctor.VIGEMBUS_VERSION}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", type=Path, default=ROOT / "dist" / "PSPStream", help="the folder build.py made")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    args = ap.parse_args()
    app, out = args.app.resolve(), args.out.resolve()
    if not (app / "pspstream.exe").is_file():
        raise SystemExit(f"{app / 'pspstream.exe'} does not exist: run build.py first")
    subprocess.run([iscc(), "/Qp", *defines(app, out), str(SCRIPT)], check=True)
    setup = out / "PSPStream-Setup-x64.exe"
    print(f"{setup}: {setup.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
