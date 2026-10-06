"""Compila a ViGEmClient.dll (MIT) do código-fonte oficial, num commit fixo, para o controle de Xbox
virtual do servidor de Windows (server/win_gamepad.py):

  python packaging/windows/vigem.py C:\\vigem      # C:\\vigem\\ViGEmClient.dll e C:\\vigem\\LICENSE.txt

Com as definições da configuração Release_DLL|x64 do próprio projeto (src/ViGEmClient.vcxproj): CRT
estático (/MT), Unicode, VIGEM_DYNAMIC e VIGEM_EXPORTS. O CMakeLists.txt deles não exporta as funções
na DLL, por isso o cl direto. Precisa do git e do Visual Studio com C++ (o runner windows-2022 tem);
usado pelo CI antes do build.py --vigem.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "https://github.com/nefarius/ViGEmClient"
COMMIT = "b66d02d57e32cc8595369c53418b843e958649b4"  # a última (set/2023; o projeto foi aposentado)
DEFINES = ("WIN32_LEAN_AND_MEAN", "VIGEM_DYNAMIC", "VIGEM_EXPORTS", "NDEBUG", "_LIB", "UNICODE", "_UNICODE")
EXPORTS = ("vigem_alloc", "vigem_connect", "vigem_target_x360_alloc", "vigem_target_add",
           "vigem_target_x360_update", "vigem_target_remove", "vigem_target_x360_get_user_index")


def vcvars() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    out = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                          "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                         capture_output=True, text=True, check=True).stdout.strip()
    bat = Path(out.splitlines()[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not bat.is_file():
        raise SystemExit(f"{bat} not found (Visual Studio with C++)")
    return bat


def fetch(dest: Path) -> None:
    subprocess.run(["git", "clone", "--quiet", REPO, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", COMMIT], check=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if head != COMMIT:
        raise SystemExit(f"ViGEmClient at {head}, expected {COMMIT}")


def compile_dll(src: Path, work: Path) -> Path:
    flags = " ".join(f"/D{d}" for d in DEFINES)
    script = work / "build.bat"
    # o recurso (versão da DLL) é opcional: sem o rc, a DLL sai igual, só sem a aba de detalhes
    script.write_text(f"""@echo off
call "{vcvars()}" >nul || exit /b 1
cd /d "{work}"
set RES=
rc /nologo /fo ViGEmClient.res "{src}\\src\\ViGEmClient.rc" && set RES=ViGEmClient.res
cl /nologo /O2 /MT /EHsc /LD /W3 {flags} /I"{src}\\include" /I"{src}\\src" "{src}\\src\\ViGEmClient.cpp" %RES% ^
   /Fe:ViGEmClient.dll /link setupapi.lib kernel32.lib || exit /b 1
""", encoding="ascii")
    subprocess.run(["cmd", "/d", "/c", str(script)], check=True)
    return work / "ViGEmClient.dll"


def check_exports(dll: Path) -> None:
    try:
        import pefile
    except ImportError:
        print("pefile missing: exports not checked")
        return
    pe = pefile.PE(str(dll))
    names = {e.name.decode() for e in getattr(pe, "DIRECTORY_ENTRY_EXPORT").symbols if e.name}
    pe.close()
    missing = [n for n in EXPORTS if n not in names]
    if missing:
        raise SystemExit(f"{dll} does not export {', '.join(missing)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="folder for ViGEmClient.dll and its LICENSE.txt")
    args = ap.parse_args()
    if sys.platform != "win32":
        raise SystemExit("builds on Windows only (MSVC)")
    with tempfile.TemporaryDirectory() as tmp:
        src, work = Path(tmp) / "src", Path(tmp) / "build"
        work.mkdir()
        fetch(src)
        dll = compile_dll(src, work)
        check_exports(dll)
        args.out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dll, args.out / "ViGEmClient.dll")
        shutil.copy2(src / "LICENSE", args.out / "LICENSE.txt")
    print(f"{args.out / 'ViGEmClient.dll'}: ViGEmClient {COMMIT[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
