"""Servidor de macOS: mapeamento de teclas Quartz (CoreGraphics), opções de
captura/áudio/openh264 para darwin e diagnósticos/instalação (mac_doctor).

  python3 -m unittest tests.test_macos
"""
import bz2
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import mac_doctor  # noqa: E402
import mac_input  # noqa: E402
import win_input  # noqa: E402

MACOS = sys.platform == "darwin"


class FakeCG:
    def __init__(self):
        self.calls = []
        self.released = []
        self.next_id = 100

    def _new(self):
        self.next_id += 1
        return self.next_id

    def CGEventCreate(self, src):
        return self._new()

    def CGEventGetLocation(self, ev):
        return mac_input.CGPoint(100.0, 200.0)

    def CGMainDisplayID(self):
        return 1

    def CGDisplayPixelsWide(self, did):
        return 1920

    def CGDisplayPixelsHigh(self, did):
        return 1080

    def CGEventCreateKeyboardEvent(self, src, keycode, down):
        ev = self._new()
        self.calls.append(("key", keycode, down, ev))
        return ev

    def CGEventCreateMouseEvent(self, src, ev_type, pos, button):
        ev = self._new()
        self.calls.append(("mouse", ev_type, (pos.x, pos.y), button, ev))
        return ev

    def CGEventSetIntegerValueField(self, ev, field, value):
        self.calls.append(("field", ev, field, value))

    def CGEventPost(self, tap, ev):
        self.calls.append(("post", tap, ev))

    def CFRelease(self, ev):
        self.released.append(ev)


class QuartzInputTest(unittest.TestCase):
    def test_every_keyboard_profile_is_covered(self):
        data = json.loads((ROOT / "server" / "keymap.json").read_text())
        for name, profile in data.items():
            if name.startswith("_") or profile.get("type") == "gamepad":
                continue
            codes = {c.strip() for combo in profile.get("buttons", {}).values() for c in combo.split("+")}
            codes |= set(profile.get("analog", {}).get("keys", {}).values())
            self.assertEqual(sorted(c for c in codes if not mac_input.known(c)), [], name)

    def test_matches_windows_key_set(self):
        win_codes = set(win_input.SCANCODES) | set(win_input.VIRTUAL_KEYS) | set(win_input.MOUSE_BUTTONS)
        missing = sorted(c for c in win_codes if not mac_input.known(c))
        self.assertEqual(missing, [])

    def test_quartz_key_and_mouse_events(self):
        cg = FakeCG()
        out = mac_input.QuartzInputOutput({"KEY_W", "BTN_LEFT"}, cg=cg, cf=cg)
        out.key("KEY_W", True)
        out.key("KEY_W", False)
        out.move(10, -5)
        out.key("BTN_LEFT", True)
        out.move(4, 3)  # com BTN_LEFT segurado, vira LeftMouseDragged (6)
        out.key("BTN_LEFT", False)
        out.close()

        key_calls = [c for c in cg.calls if c[0] == "key"]
        self.assertEqual([(c[1], c[2]) for c in key_calls], [(0x0D, True), (0x0D, False)])
        mouse_calls = [c for c in cg.calls if c[0] == "mouse"]
        self.assertEqual(mouse_calls[0][1], mac_input.CG_EVENT_MOUSE_MOVED)
        self.assertEqual(mouse_calls[0][2], (110.0, 195.0))
        self.assertEqual(mouse_calls[1][1], 1)  # LeftMouseDown
        self.assertEqual(mouse_calls[2][1], 6)  # LeftMouseDragged
        self.assertEqual(mouse_calls[3][1], 2)  # LeftMouseUp
        fields = [c for c in cg.calls if c[0] == "field"]
        self.assertIn((mac_input.CG_MOUSE_EVENT_DELTA_X, 10), [(f[2], f[3]) for f in fields])
        self.assertIn((mac_input.CG_MOUSE_EVENT_DELTA_Y, -5), [(f[2], f[3]) for f in fields])
        self.assertEqual(len(cg.released), 10)  # 6 eventos + 4 CGEventCreate(None) (2 move + 2 click)

    @unittest.skipUnless(MACOS, "só no macOS")
    def test_cgevent_creation_real_macos(self):
        cg, cf = mac_input._load_frameworks()
        kev = cg.CGEventCreateKeyboardEvent(None, mac_input.KEYCODES["KEY_W"], True)
        self.assertTrue(kev)
        cf.CFRelease(kev)
        mev = cg.CGEventCreateMouseEvent(None, mac_input.CG_EVENT_MOUSE_MOVED, mac_input.CGPoint(10.0, 20.0), 0)
        self.assertTrue(mev)
        cg.CGEventSetIntegerValueField(mev, mac_input.CG_MOUSE_EVENT_DELTA_X, -3)
        cg.CGEventSetIntegerValueField(mev, mac_input.CG_MOUSE_EVENT_DELTA_Y, 7)
        cf.CFRelease(mev)


class MacOptionsTest(unittest.TestCase):
    """Opções e caminhos do macOS num processo com sys.platform = darwin."""

    def run_as_macos(self, code: str) -> dict:
        prog = ("import json, sys, argparse, ctypes, dataclasses, functools, http.server, logging, pathlib, shutil, "
                "socket, subprocess, threading, urllib.request, collections, statistics, bz2, hashlib, platform\n"
                "sys.platform = 'darwin'\n"
                f"sys.path.insert(0, {str(ROOT / 'server')!r})\n" + code)
        out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, timeout=60,
                             env={**os.environ, "PSPSTREAM_LANG": "en"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_options_and_pipelines(self):
        got = self.run_as_macos(
            "import pspstream, settings, capture, gst_pipe, openh264\n"
            "a = pspstream.build_parser().parse_args([])\n"
            "cands = gst_pipe.screen_candidates(0, True, 60, 480, 272, 'bilinear2', True)\n"
            "avf_only = gst_pipe.screen_candidates(0, True, 60, 480, 272, 'bilinear2', True, allow_sck=False)\n"
            "avf_args = pspstream.build_parser().parse_args(['--source', 'avf'])\n"
            "avf_src = capture.build_source(avf_args)\n"
            "print(json.dumps({\n"
            "  'source': a.source, 'monitor': a.monitor, 'pipe': capture.use_pipe(), 'has_usb': hasattr(a, 'usb'),\n"
            "  'choices': list(settings.BY_KEY['source'].choices), 'keys': sorted(settings.BY_KEY),\n"
            "  'screen_cands': [[name, pipe] for name, pipe in cands],\n"
            "  'avf_cands': [[name, pipe] for name, pipe in avf_only],\n"
            "  'avf_src_cands': [name for name, _ in avf_src.candidates],\n"
            "  'audio_mon': gst_pipe.audio_src('monitor'), 'audio_osx': gst_pipe.audio_src('osx'),\n"
            "  'audio_id': gst_pipe.audio_src('3'), 'pattern': openh264.PATTERN,\n"
            "  'cfg': str(settings.default_path()),\n"
            "}))")
        self.assertEqual(got["source"], "screen")
        self.assertEqual(got["monitor"], 0)
        self.assertTrue(got["pipe"])
        self.assertTrue(got["has_usb"])
        self.assertEqual(got["choices"], ["screen", "avf", "test", "static"])
        self.assertIn("monitor", got["keys"])
        self.assertNotIn("kms_monitor", got["keys"])
        self.assertTrue(any("avfvideosrc capture-screen=true" in str(pipe) for _, pipe in got["screen_cands"]))
        self.assertEqual(len(got["avf_cands"]), 1)
        self.assertEqual(got["avf_cands"][0][0], "avfvideosrc")
        self.assertIn("avfvideosrc capture-screen=true", got["avf_cands"][0][1])
        self.assertEqual(got["avf_src_cands"], ["avfvideosrc"])
        self.assertTrue(got["audio_mon"].startswith("osxaudiosrc"))
        self.assertTrue(got["audio_osx"].startswith("osxaudiosrc"))
        self.assertIn("device=3", got["audio_id"])
        self.assertEqual(got["pattern"], "*openh264*.dylib")
        self.assertIn("Library/Application Support/PSPStream/server.json", got["cfg"])

    def test_xbox_profile_rejected_clearly(self):
        got = self.run_as_macos(
            "import unittest.mock as mock, pspstream, capture, usb_link, mac_gamepad\n"
            "a = pspstream.build_parser().parse_args(['--profile', 'xbox'])\n"
            "with mock.patch.object(usb_link, 'is_available', return_value=False), \\\n"
            "     mock.patch.object(mac_gamepad, 'crossover_installed', return_value=False):\n"
            "    try:\n"
            "        capture.open_injector(a)\n"
            "        err = ''\n"
            "    except RuntimeError as exc:\n"
            "        err = str(exc)\n"
            "print(json.dumps({'err': err}))")
        self.assertIn("CrossOver", got["err"])
        self.assertIn("pyusb", got["err"])
        self.assertNotIn("wolf", got["err"])


class MacDoctorTest(unittest.TestCase):
    def test_openh264_urls(self):
        self.assertIn("mac-arm64", mac_doctor.openh264_url("arm64"))
        self.assertIn("mac64", mac_doctor.openh264_url("x86_64"))

    def test_download_openh264(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        raw = b"fake dylib bytes"
        packed = bz2.compress(raw)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("urllib.request.urlopen", return_value=Resp(packed)) as urlopen:
            dest = mac_doctor.download_openh264(Path(tmp), arch="arm64")
            self.assertEqual(urlopen.call_args[0][0], mac_doctor.openh264_url("arm64"))
            self.assertEqual(dest.read_bytes(), raw)
            self.assertEqual((Path(tmp) / "libopenh264.dylib").read_bytes(), raw)

    def test_run_checks_and_report(self):
        items = mac_doctor.run_checks(port=0)
        groups = {i.group for i in items}
        self.assertTrue({"System", "GStreamer", "Video", "Capture", "Controls", "Network"} <= groups, groups)
        text = mac_doctor.report(items)
        self.assertTrue(text)

    def test_setup_runs_only_confirmed(self):
        answers = iter(["y", "n", "n", "n"])
        with mock.patch("gst_pipe.find_gstreamer", return_value=None), \
                mock.patch("shutil.which", return_value="/opt/homebrew/bin/brew"), \
                mock.patch("openh264.library", side_effect=RuntimeError("missing")), \
                mock.patch.object(mac_doctor.paths, "sck_helper", return_value=Path(__file__)), \
                mock.patch("mac_input.screen_capture_access", return_value=False), \
                mock.patch("mac_input.is_trusted", return_value=False), \
                mock.patch("mac_gamepad.find_crossover_unix_dir", return_value=None), \
                mock.patch.object(mac_doctor.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(mac_doctor, "main", return_value=0), \
                mock.patch("builtins.print"):
            rc = mac_doctor.setup(5123, "test", ask=lambda _: next(answers))
        self.assertEqual(rc, 0)
        run.assert_called_once_with("brew install gstreamer", shell=True)


@unittest.skipUnless(MACOS, "macOS only")
class ScreenCaptureKitTest(unittest.TestCase):
    def test_make_builds_and_prioritizes_sck(self):
        import gst_pipe
        import paths

        res = subprocess.run(
            ["make", "-C", str(ROOT / "tools" / "sck")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(paths.sck_helper().is_file())
        if gst_pipe.sck_available():
            cands = gst_pipe.screen_candidates(0, True, 60, 480, 272, "bilinear2", True)
            self.assertGreaterEqual(len(cands), 2)
            self.assertEqual(cands[0][0], "ScreenCaptureKit")
            self.assertEqual(cands[1][0], "avfvideosrc")
            self.assertTrue(gst_pipe.audio_available("monitor"))

    def test_live_sck_video_i420(self):
        import gst_pipe

        if not gst_pipe.sck_available():
            self.skipTest("ScreenCaptureKit helper requires macOS 13+")
        if not mac_input.screen_capture_access():
            self.skipTest("Screen Recording permission not granted")

        cands = gst_pipe.screen_candidates(0, True, 30, 480, 272, "bilinear2", True)
        self.assertEqual(cands[0][0], "ScreenCaptureKit")
        src = gst_pipe.PipeSource([cands[0]], 480, 272, 30, 60, codec="h264p", label="screen")
        try:
            src.start()
            deadline = time.monotonic() + 5.0
            got = None
            while got is None and not src.failed and time.monotonic() < deadline:
                got = src.wait_newer(0, 0.1)
            if src.failed and ("declined" in src.failed.lower() or "permission" in src.failed.lower()):
                self.skipTest(src.failed)
            self.assertIsNone(src.failed)
            self.assertIsNotNone(got, "no I420 frame received from pspstream-sck")
            _, frame, _ = got
            self.assertEqual(len(frame), 480 * 272 * 3 // 2)
        finally:
            src.stop()

    def test_live_sck_audio_ima_adpcm(self):
        import audio
        import gst_pipe

        if not gst_pipe.sck_available():
            self.skipTest("ScreenCaptureKit helper requires macOS 13+")
        if not mac_input.screen_capture_access():
            self.skipTest("Screen Recording permission not granted")

        cap = gst_pipe.PipeAudioCapture("monitor", 44100, 2, 20)
        received = []
        done = threading.Event()

        def on_pkt(seq, pos, rate, channels, samples, block):
            received.append((seq, pos, rate, channels, samples, block))
            if len(received) >= 3:
                done.set()

        cap.start()
        cap.add_listener(on_pkt)
        try:
            if not done.wait(5.0):
                self.fail("no audio packets received from pspstream-sck")
        finally:
            cap.remove_listener(on_pkt)
            cap.stop()

        _, _, rate, channels, samples, block = received[-1]
        self.assertEqual(rate, 44100)
        self.assertEqual(channels, 2)
        pcm = audio.ima_decode_block(block, channels)
        self.assertEqual(len(pcm), samples * channels)


@unittest.skipUnless(MACOS, "macOS only")
class CrossOverGamepadTest(unittest.TestCase):
    def test_make_and_install_crossover_shim(self):
        import mac_gamepad
        import paths

        res = subprocess.run(
            ["make", "-C", str(ROOT / "tools" / "crossover")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        dylib = paths.crossover_sdl2()
        self.assertTrue(dylib.is_file())

        with tempfile.TemporaryDirectory() as tmp:
            unix_dir = Path(tmp)
            (unix_dir / "winebus.so").write_bytes(b"")
            with mock.patch.dict(os.environ, {"PSPSTREAM_CROSSOVER_UNIX_DIR": str(unix_dir)}):
                self.assertEqual(mac_gamepad.find_crossover_unix_dir(), unix_dir)
                self.assertFalse(mac_gamepad.crossover_installed())
                dest = mac_gamepad.install_crossover_shim(dylib)
                self.assertEqual(dest, unix_dir / "libSDL2-2.0.0.dylib")
                self.assertTrue(dest.is_file())
                self.assertTrue(mac_gamepad.crossover_installed())
                items = mac_doctor.run_checks(port=0)
                self.assertTrue(
                    any(i.group == "Controls" and i.state == "ok" and "CrossOver" in i.text for i in items),
                    items,
                )

    def test_sdl2_proxy_end_to_end(self):
        import paths

        subprocess.run(
            ["make", "-C", str(ROOT / "tools" / "crossover")],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        dylib = paths.crossover_sdl2()
        mock_c = """
#include <stdint.h>
#include <stdlib.h>

typedef struct {
    uint16_t version, type, naxes, nbuttons, nhats, vendor_id, product_id, padding;
    uint32_t button_mask, axis_mask;
    const char *name;
    void *userdata;
    void *callbacks[6];
} MockVirtualDesc;

static int g_attached = 0;
static int g_detach_count = 0;
static uint16_t g_vendor = 0;
static uint16_t g_product = 0;
static int16_t g_axes[6] = {0};
static uint8_t g_buttons[15] = {0};
static int g_joy_handle = 1;

int SDL_Init(uint32_t flags) { (void)flags; return 0; }
void SDL_Quit(void) {}
void SDL_PumpEvents(void) {}
int SDL_WaitEventTimeout(void *ev, int timeout) { (void)ev; (void)timeout; return 0; }
int SDL_NumJoysticks(void) { return g_attached ? 1 : 0; }
int32_t SDL_JoystickGetDeviceInstanceID(int idx) { return (g_attached && idx == 0) ? 42 : -1; }
int32_t SDL_JoystickInstanceID(void *joy) { (void)joy; return 42; }
void *SDL_JoystickOpen(int idx) { return (g_attached && idx == 0) ? (void *)&g_joy_handle : NULL; }
void SDL_JoystickClose(void *joy) { (void)joy; }
int SDL_JoystickAttachVirtualEx(const MockVirtualDesc *desc) {
    g_attached = 1;
    g_vendor = desc->vendor_id;
    g_product = desc->product_id;
    return 0;
}
int SDL_JoystickDetachVirtual(int idx) { (void)idx; g_attached = 0; g_detach_count++; return 0; }
int SDL_JoystickSetVirtualAxis(void *joy, int axis, int16_t value) {
    (void)joy;
    if (axis >= 0 && axis < 6) g_axes[axis] = value;
    return 0;
}
int SDL_JoystickSetVirtualButton(void *joy, int button, uint8_t value) {
    (void)joy;
    if (button >= 0 && button < 15) g_buttons[button] = value;
    return 0;
}

int mock_attached(void) { return g_attached; }
int mock_detach_count(void) { return g_detach_count; }
int mock_vendor(void) { return g_vendor; }
int mock_product(void) { return g_product; }
int mock_axis(int axis) { return (axis >= 0 && axis < 6) ? g_axes[axis] : 0; }
int mock_button(int btn) { return (btn >= 0 && btn < 15) ? g_buttons[btn] : 0; }
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            c_path = tmp_dir / "mock_sdl.c"
            real_sdl = tmp_dir / "libreal_sdl2.dylib"
            shm_file = tmp_dir / "pad.shm"
            c_path.write_text(mock_c, encoding="utf-8")
            subprocess.run(
                [os.environ.get("CC", "cc"), "-dynamiclib", "-O2", str(c_path), "-o", str(real_sdl)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            prog = f"""
import ctypes, json, sys
sys.path.insert(0, {str(ROOT / 'server')!r})
import gamepad, inject, mac_gamepad

proxy = ctypes.CDLL({str(dylib)!r})
proxy.SDL_WaitEventTimeout.argtypes = [ctypes.c_void_p, ctypes.c_int]
proxy.SDL_WaitEventTimeout.restype = ctypes.c_int
proxy.SDL_Init.argtypes = [ctypes.c_uint32]
proxy.SDL_Init.restype = ctypes.c_int
assert proxy.SDL_Init(0x3000) == 0

profile = inject.load_profile({str(ROOT / 'server' / 'keymap.json')!r}, 'xbox')
inj = gamepad.GamepadInjector(profile, timeout=0, out=mac_gamepad.UsbHidPad())
try:
    proxy.SDL_WaitEventTimeout(None, 0)
    mock = ctypes.CDLL({str(real_sdl)!r})
    initial = {{
        'attached': mock.mock_attached(),
        'vendor': mock.mock_vendor(),
        'product': mock.mock_product(),
        'axes': [mock.mock_axis(i) for i in range(6)],
        'buttons': [mock.mock_button(i) for i in range(15)],
    }}

    inj.update(inject.PSP_BUTTONS['CROSS'] | inject.PSP_BUTTONS['L'] | inject.PSP_BUTTONS['UP'], 255, 128)
    proxy.SDL_WaitEventTimeout(None, 0)
    pressed = {{
        'axes': [mock.mock_axis(i) for i in range(6)],
        'buttons': [mock.mock_button(i) for i in range(15)],
    }}

    inj.update(0, 128, 128)
    inj.update(inject.PSP_BUTTONS['SELECT'], 128, 128)
    inj.update(inject.PSP_BUTTONS['SELECT'] | inject.PSP_BUTTONS['L'] | inject.PSP_BUTTONS['TRIANGLE'], 128, 128)
    proxy.SDL_WaitEventTimeout(None, 0)
    shifted = {{
        'buttons': [mock.mock_button(i) for i in range(15)],
    }}
finally:
    inj.close()

proxy.SDL_WaitEventTimeout(None, 0)
after_close = {{
    'attached': mock.mock_attached(),
    'detach_count': mock.mock_detach_count(),
}}
print(json.dumps({{
    'initial': initial, 'pressed': pressed, 'shifted': shifted, 'after_close': after_close
}}))
"""
            res = subprocess.run(
                [sys.executable, "-c", prog],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "PSPSTREAM_REAL_SDL2": str(real_sdl),
                    "PSPSTREAM_PAD_SHM": str(shm_file),
                },
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            got = json.loads(res.stdout.strip().splitlines()[-1])

            self.assertEqual(got["initial"]["attached"], 1)
            self.assertEqual(got["initial"]["vendor"], 0x045E)
            self.assertEqual(got["initial"]["product"], 0x028E)
            self.assertEqual(got["initial"]["axes"], [0, 0, 0, 0, -32768, -32768])
            self.assertEqual(got["initial"]["buttons"], [0] * 15)

            self.assertEqual(got["pressed"]["axes"][0], 32767)
            self.assertEqual(got["pressed"]["axes"][4], 32767)
            self.assertEqual(got["pressed"]["buttons"][0], 1)   # A
            self.assertEqual(got["pressed"]["buttons"][11], 1)  # DPAD_UP

            self.assertEqual(got["shifted"]["buttons"][9], 1)   # LB (SDL_CONTROLLER_BUTTON_LEFTSHOULDER)
            self.assertEqual(got["shifted"]["buttons"][5], 1)   # GUIDE (SDL_CONTROLLER_BUTTON_GUIDE)

            self.assertEqual(got["after_close"]["attached"], 0)
            self.assertEqual(got["after_close"]["detach_count"], 1)


if __name__ == "__main__":
    unittest.main()
