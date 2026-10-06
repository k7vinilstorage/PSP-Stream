# PyInstaller: o servidor de Windows numa pasta (pspstream.exe + _internal).
# Usado pelo packaging/windows/build.py, que acrescenta o GStreamer reduzido.
# Pasta e não um .exe único: abre na hora (o .exe único se descompacta a cada início).
from pathlib import Path

root = Path(SPECPATH).resolve().parent.parent  # noqa: F821 (SPECPATH: do PyInstaller)
server = root / "server"

# Os módulos que o servidor importa só quando precisa (o PyInstaller não os acha sozinho).
hidden = [p.stem for p in server.glob("*.py") if p.stem not in ("kms", "portal", "gst_source", "wolf_source")]

a = Analysis(  # noqa: F821
    [str(server / "pspstream.py")],
    pathex=[str(server)],
    hiddenimports=hidden,
    datas=[(str(server / "web"), "web"), (str(server / "keymap.json"), "."),
           (str(root / "assets" / "testcard.jpg"), "assets")],
    excludes=["gi", "evdev", "tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="pspstream", console=True, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PSPStream", upx=False)  # noqa: F821
