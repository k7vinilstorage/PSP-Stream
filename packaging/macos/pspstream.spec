# PyInstaller spec for PSPStream on macOS (arm64 / x86_64).
# Run from repo root via packaging/macos/build.py.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
SERVER = os.path.join(ROOT, "server")

a = Analysis(
    [os.path.join(SERVER, "pspstream.py")],
    pathex=[SERVER],
    binaries=[],
    datas=[
        (os.path.join(SERVER, "web"), "web"),
        (os.path.join(SERVER, "keymap.json"), "."),
        (os.path.join(ROOT, "assets", "testcard.jpg"), "assets"),
    ],
    hiddenimports=[
        "adaptive",
        "audio",
        "capture",
        "control",
        "framerate",
        "gamepad",
        "gst_pipe",
        "h264",
        "i18n",
        "imaging",
        "inject",
        "jpeginfo",
        "lang_pt",
        "mac_doctor",
        "mac_gamepad",
        "mac_input",
        "netcheck",
        "openh264",
        "paths",
        "protocol",
        "settings",
        "sources",
        "stats",
        "transports",
        "usb_link",
        "web",
        "wolf_api",
        "PIL.Image",
        "PIL.ImageDraw",
        "usb.core",
        "usb.util",
        "usb.backend.libusb1",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gi", "evdev", "tkinter", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pspstream",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PSPStream",
)
