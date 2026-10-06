"""Servidor de Windows: o que dá para conferir em qualquer sistema (conversões
de cor do Pillow, o layout do SendInput, a captura pelo gst-launch com o
videotestsrc, as opções do Windows) e, no Windows, as chamadas de verdade.

  python3 -m unittest tests.test_windows
"""
import ctypes
import io
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import gst_pipe  # noqa: E402
import imaging  # noqa: E402
import win_gamepad  # noqa: E402
import win_input  # noqa: E402

WINDOWS = sys.platform == "win32"
HAS_GST = gst_pipe.find_gstreamer() is not None
HAS_PIL = imaging.available()


def openh264_ok() -> bool:
    try:
        import openh264
        openh264.library()
        return True
    except Exception:
        return False


@unittest.skipUnless(HAS_PIL, "sem o Pillow")
class ImagingTest(unittest.TestCase):
    def rgb(self, color, size=(480, 272)):
        from PIL import Image
        return Image.new("RGB", size, color)

    def decode(self, jpeg: bytes):
        from PIL import Image
        return Image.open(io.BytesIO(jpeg)).convert("RGB")

    def test_round_trip_keeps_colors(self):
        """RGB -> I420 (faixa limitada) -> JPEG (faixa cheia) -> RGB: a cor volta."""
        for color in ((128, 128, 128), (255, 255, 255), (0, 0, 0), (200, 40, 40), (30, 160, 60), (20, 40, 200)):
            i420 = imaging.rgb_to_i420(self.rgb(color))
            self.assertEqual(len(i420), 480 * 272 * 3 // 2)
            got = self.decode(imaging.i420_to_jpeg(i420, 480, 272, 90)).getpixel((240, 136))
            for a, b in zip(got, color):
                self.assertLess(abs(a - b), 10, f"{color} -> {got}")

    def test_limited_range(self):
        """Branco e preto no I420 ficam em 235 e 16 (o que o openh264 e o PSP esperam)."""
        white = imaging.rgb_to_i420(self.rgb((255, 255, 255), (16, 16)))
        black = imaging.rgb_to_i420(self.rgb((0, 0, 0), (16, 16)))
        self.assertEqual((white[0], black[0]), (235, 16))
        self.assertEqual((white[256], black[256]), (128, 128))

    def test_jpeg_is_what_the_psp_decodes(self):
        from jpeginfo import jpeg_info
        jpeg = imaging.i420_to_jpeg(imaging.rgb_to_i420(self.rgb((90, 90, 90))), 480, 272, 60)
        info = jpeg_info(jpeg)
        self.assertEqual((info.width, info.height), (480, 272))
        self.assertEqual(info.problems(), [])

    def test_fit_letterbox(self):
        from PIL import Image
        img = Image.new("RGB", (200, 50), (255, 0, 0))  # 4:1 numa tela 480x272: faixas em cima e embaixo
        out = imaging.fit(img, 480, 272)
        self.assertEqual(out.size, (480, 272))
        self.assertEqual(out.getpixel((240, 2)), (0, 0, 0))
        self.assertGreater(out.getpixel((240, 136))[0], 240)
        stretched = imaging.fit(img, 480, 272, keep_aspect=False)
        self.assertGreater(stretched.getpixel((240, 2))[0], 240)

    def test_image_file(self):
        jpeg = imaging.transcode_image(str(ROOT / "assets" / "testcard.jpg"), 480, 272, 70)
        self.assertEqual(jpeg[:2], b"\xff\xd8")


class SendInputLayoutTest(unittest.TestCase):
    def test_sizes(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(win_input.INPUT), 40)  # o tamanho que o SendInput confere
        self.assertEqual(ctypes.sizeof(win_input.KEYBDINPUT), 8 + 2 * ctypes.sizeof(ctypes.c_size_t)
                         if ctypes.sizeof(ctypes.c_size_t) == 8 else 16)

    def test_key_bytes(self):
        up = win_input.key_input("KEY_UP", True)
        self.assertEqual((up.type, up.ki.wVk, up.ki.wScan), (win_input.INPUT_KEYBOARD, 0, 0x48))
        self.assertEqual(up.ki.dwFlags, win_input.KEYEVENTF_SCANCODE | win_input.KEYEVENTF_EXTENDEDKEY)
        a = win_input.key_input("KEY_A", False)
        self.assertEqual((a.ki.wScan, a.ki.dwFlags), (0x1E, win_input.KEYEVENTF_SCANCODE | win_input.KEYEVENTF_KEYUP))
        vol = win_input.key_input("KEY_VOLUMEUP", True)
        self.assertEqual((vol.ki.wVk, vol.ki.wScan, vol.ki.dwFlags), (0xAF, 0, 0))

    def test_mouse(self):
        left = win_input.key_input("BTN_LEFT", True)
        self.assertEqual((left.type, left.mi.dwFlags), (win_input.INPUT_MOUSE, 0x0002))
        self.assertEqual(win_input.key_input("BTN_RIGHT", False).mi.dwFlags, 0x0010)
        move = win_input.move_input(-5, 7)
        self.assertEqual((move.mi.dx, move.mi.dy, move.mi.dwFlags), (-5, 7, win_input.MOUSEEVENTF_MOVE))

    def test_every_keyboard_profile_is_covered(self):
        """Todo código dos perfis de teclado e mouse do keymap.json existe no Windows."""
        data = json.loads((ROOT / "server" / "keymap.json").read_text())
        for name, profile in data.items():
            if name.startswith("_") or profile.get("type") == "gamepad":
                continue
            codes = {c.strip() for combo in profile.get("buttons", {}).values() for c in combo.split("+")}
            codes |= set(profile.get("analog", {}).get("keys", {}).values())
            self.assertEqual(sorted(c for c in codes if not win_input.known(c)), [], name)

    def test_evdev_numbers_match_the_scancodes(self):
        """No Linux, KEY_ESC..KEY_KPDOT têm o mesmo número do scancode (conjunto 1): confere a tabela."""
        try:
            from evdev import ecodes
        except ImportError:
            self.skipTest("sem o python-evdev")
        for name, (scan, extended) in win_input.SCANCODES.items():
            if not extended and scan <= 0x53:
                self.assertEqual(getattr(ecodes, name), scan, name)


@unittest.skipUnless(HAS_GST, "sem o gst-launch-1.0")
class PipeCaptureTest(unittest.TestCase):
    def frames(self, source, seconds=1.5):
        source.start()
        try:
            seq, got, end = 0, [], time.monotonic() + seconds
            while time.monotonic() < end:
                r = source.wait_newer(seq, 1)
                if r:
                    seq = r[0]
                    got.append(r[1])
            return got
        finally:
            source.stop()

    def test_raw_i420(self):
        src = gst_pipe.PipeSource(gst_pipe.test_candidates(30, 480, 272, "bilinear", True), 480, 272, 30, 60, "h264p")
        got = self.frames(src)
        self.assertGreater(len(got), 20)
        self.assertTrue(all(len(f) == 480 * 272 * 3 // 2 for f in got))
        self.assertTrue(src.raw_i420)

    @unittest.skipUnless(HAS_PIL, "sem o Pillow")
    def test_jpeg(self):
        src = gst_pipe.PipeSource(gst_pipe.test_candidates(30, 480, 272, "bilinear", True), 480, 272, 30, 60, "jpeg")
        got = self.frames(src)
        self.assertGreater(len(got), 20)
        self.assertEqual(got[-1][:2], b"\xff\xd8")

    @unittest.skipUnless(openh264_ok(), "sem a libopenh264")
    def test_h264_idr(self):
        src = gst_pipe.PipeSource(gst_pipe.test_candidates(30, 480, 272, "bilinear", True), 480, 272, 30, 60, "h264")
        got = self.frames(src)
        self.assertGreater(len(got), 20)
        self.assertEqual(got[-1][:4], b"\x00\x00\x00\x01")

    def test_fallback_to_the_next_candidate(self):
        bad = ("bad", "nonexistentelement123 ! fakesink")
        good = gst_pipe.test_candidates(30, 160, 96, "bilinear", True)[0]
        src = gst_pipe.PipeSource([bad, good], 160, 96, 30, 60, "h264p")
        got = self.frames(src, 1.0)
        self.assertEqual(src.mode, "test")
        self.assertTrue(got)

    def test_error_reason(self):
        src = gst_pipe.PipeSource([("bad", "nonexistentelement123")], 160, 96, 30, 60, "h264p")
        with self.assertRaises(RuntimeError) as ctx:
            src.start()
        self.assertIn("bad", str(ctx.exception))

    def test_frame_pacing(self):
        """Frames de 16,7 ms chegam sem travadas (o ACK imediato contra o Nagle)."""
        src = gst_pipe.PipeSource(gst_pipe.test_candidates(60, 480, 272, "bilinear", True), 480, 272, 60, 60, "h264p")
        src.start()
        times, seq, end = [], 0, time.monotonic() + 4
        try:
            while time.monotonic() < end:
                r = src.wait_newer(seq, 1)
                if r:
                    seq = r[0]
                    times.append(time.monotonic())
        finally:
            src.stop()
        iv = sorted((b - a) * 1000 for a, b in zip(times[10:], times[11:]))
        p99 = iv[int(len(iv) * 0.99)]
        print(f"\npipe: {len(times)} frames, p50 {iv[len(iv) // 2]:.1f} ms, p99 {p99:.1f} ms, max {iv[-1]:.1f} ms")
        self.assertGreater(len(times), 180)
        self.assertLess(p99, 40)

    def test_audio_blocks(self):
        from audio import ima_decode_block
        cap = gst_pipe.PipeAudioCapture("test", 44100, 2)
        blocks = []
        cap.add_listener(lambda seq, pos, rate, ch, n, block: blocks.append((seq, pos, block)))
        cap.start()
        time.sleep(1.0)
        cap.stop()
        self.assertGreater(len(blocks), 30)
        self.assertEqual([b[0] for b in blocks[:3]], [1, 2, 3])
        self.assertEqual(blocks[1][1], cap.samples)
        pcm = ima_decode_block(blocks[-1][2], 2)
        self.assertEqual(len(pcm), cap.samples * 2)
        self.assertGreater(max(abs(v) for v in pcm), 3000)  # o tom de 440 Hz, não silêncio


class WindowsOptionsTest(unittest.TestCase):
    """As opções do Windows, num processo com sys.platform = win32 (no Windows, o de verdade)."""

    def run_as_windows(self, code: str) -> dict:
        # a biblioteca padrão primeiro, com a plataforma de verdade (o shutil, por exemplo, escolhe na importação)
        prog = ("import json, sys, argparse, ctypes, dataclasses, functools, http.server, logging, pathlib, shutil, "
                "socket, subprocess, threading, urllib.request, collections, statistics, bz2, hashlib, platform\n"
                "sys.platform = 'win32'\n"
                f"sys.path.insert(0, {str(ROOT / 'server')!r})\n" + code)
        out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, timeout=60,
                             env={**os.environ, "PSPSTREAM_LANG": "en"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_options(self):
        got = self.run_as_windows(
            "import pspstream, settings, capture, control\n"
            "a = pspstream.build_parser().parse_args([])\n"
            "print(json.dumps({'source': a.source, 'monitor': a.monitor, 'pipe': capture.use_pipe(),\n"
            "  'choices': list(settings.BY_KEY['source'].choices), 'keys': sorted(settings.BY_KEY),\n"
            "  'profiles': sorted(control.keymap_profiles(a.keymap))}))")
        self.assertEqual(got["source"], "screen")
        self.assertEqual(got["monitor"], 0)
        self.assertTrue(got["pipe"])
        self.assertEqual(got["choices"], ["screen", "test", "static"])
        self.assertIn("monitor", got["keys"])
        self.assertNotIn("kms_monitor", got["keys"])
        self.assertNotIn("wolf_target", got["keys"])
        self.assertEqual(got["profiles"], ["arrows", "desktop", "game", "xbox", "xbox-camera", "xbox-shoulders"])

    def test_xbox_profile_needs_vigem(self):
        # sem a DLL (ou, com ela, sem o driver) o perfil xbox não abre: o servidor segue sem controles
        got = self.run_as_windows(
            "import os, pspstream, capture\n"
            "a = pspstream.build_parser().parse_args(['--profile', 'xbox'])\n"
            "try:\n"
            "    capture.open_injector(a)[0].close()\n"
            "    err = ''\n"
            "except RuntimeError as exc:\n"
            "    err = str(exc)\n"
            "a = pspstream.build_parser().parse_args(['--profile', 'xbox', '--input-dry-run'])\n"
            "inj, kind = capture.open_injector(a)\n"
            "inj.close()\n"
            "print(json.dumps({'error': err, 'kind': kind}))")
        if got["error"]:
            self.assertIn("ViGEm", got["error"])
        self.assertEqual(got["kind"], "virtual Xbox 360 controller")

    def test_linux_keeps_its_options(self):
        if WINDOWS:
            self.skipTest("no Windows")
        import settings
        self.assertEqual(settings.BY_KEY["source"].choices[0], "portal")
        self.assertNotIn("monitor", settings.BY_KEY)


class FakeViGEm:
    """A ViGEmClient.dll de mentira: registra as chamadas e os relatórios."""

    def __init__(self, connect=win_gamepad.NONE, add=win_gamepad.NONE):
        self.connect, self.add, self.calls, self.reports = connect, add, [], []

    def vigem_alloc(self):
        self.calls.append("alloc")
        return 1

    def vigem_free(self, client):
        self.calls.append("free")

    def vigem_connect(self, client):
        self.calls.append("connect")
        return self.connect

    def vigem_disconnect(self, client):
        self.calls.append("disconnect")

    def vigem_target_x360_alloc(self):
        self.calls.append("target_alloc")
        return 2

    def vigem_target_free(self, target):
        self.calls.append("target_free")

    def vigem_target_add(self, client, target):
        self.calls.append("add")
        return self.add

    def vigem_target_remove(self, client, target):
        self.calls.append("remove")
        return win_gamepad.NONE

    def vigem_target_x360_update(self, client, target, report):
        self.reports.append({name: getattr(report, name) for name, _ in report._fields_})
        return win_gamepad.NONE

    def vigem_target_x360_get_user_index(self, client, target, index):
        index._obj.value = 0
        return win_gamepad.NONE


def xbox_profile():
    from inject import load_profile
    return load_profile(str(ROOT / "server" / "keymap.json"), "xbox")


class ViGEmReportTest(unittest.TestCase):
    """O estado do GamepadInjector como o XUSB_REPORT do ViGEm (o XINPUT_GAMEPAD)."""

    def test_layout(self):
        r = win_gamepad.XUSB_REPORT
        self.assertEqual(ctypes.sizeof(r), 12)
        self.assertEqual([getattr(r, f).offset for f in ("wButtons", "bLeftTrigger", "bRightTrigger", "sThumbLX",
                                                          "sThumbLY", "sThumbRX", "sThumbRY")], [0, 2, 3, 4, 6, 8, 10])

    def test_buttons_and_axes(self):
        from gamepad import GamepadInjector
        state = GamepadInjector._neutral()
        self.assertEqual(bytes(win_gamepad.report(state)), bytes(12))
        state.update({("key", "BTN_A"): 1, ("key", "BTN_SELECT"): 1, ("abs", "ABS_Z"): 255,
                      ("abs", "ABS_X"): 32767, ("abs", "ABS_Y"): -32767,   # evdev: Y negativo = para cima
                      ("abs", "ABS_RY"): 32767, ("abs", "ABS_HAT0X"): -1, ("abs", "ABS_HAT0Y"): 1})
        r = win_gamepad.report(state)
        self.assertEqual(r.wButtons, 0x1000 | 0x0020 | 0x0004 | 0x0002)  # A, BACK, esquerda, baixo
        self.assertEqual((r.bLeftTrigger, r.bRightTrigger), (255, 0))
        self.assertEqual((r.sThumbLX, r.sThumbLY, r.sThumbRX, r.sThumbRY), (32767, 32767, 0, -32767))

    def test_every_button(self):
        from gamepad import BUTTON_CODES
        bits = {"A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000, "LB": 0x0100, "RB": 0x0200, "BACK": 0x0020,
                "START": 0x0010, "GUIDE": 0x0400, "L3": 0x0040, "R3": 0x0080}  # XINPUT_GAMEPAD_*
        self.assertEqual(set(bits), set(BUTTON_CODES))
        for name, bit in bits.items():
            self.assertEqual(win_gamepad.report({("key", BUTTON_CODES[name]): 1}).wButtons, bit, name)

    def test_injector_to_vigem(self):
        from gamepad import GamepadInjector
        from inject import PSP_BUTTONS as B
        lib = FakeViGEm()
        pad = win_gamepad.ViGEmPad(lib)
        self.assertEqual(lib.calls, ["alloc", "connect", "target_alloc", "add"])
        self.assertEqual(pad.user_index(), 0)
        inj = GamepadInjector(xbox_profile(), timeout=0, out=pad)
        try:
            inj.update(B["CROSS"] | B["L"], 255, 128)          # A, LT e o analógico todo para a direita
            last = lib.reports[-1]
            self.assertEqual(last["wButtons"], 0x1000)
            self.assertEqual(last["bLeftTrigger"], 255)
            self.assertEqual((last["sThumbLX"], last["sThumbLY"]), (32767, 0))
            inj.update(B["CROSS"] | B["L"], 128, 0)            # para cima no PSP = Y positivo no XInput
            self.assertEqual((lib.reports[-1]["sThumbLX"], lib.reports[-1]["sThumbLY"]), (0, 32767))
            inj.update(B["SELECT"], 128, 128)
            inj.update(B["SELECT"] | B["CROSS"], 128, 128)     # a camada do SELECT: X vira L3
            self.assertEqual(lib.reports[-1]["wButtons"], 0x0040)
            inj.update(0, 128, 128)
            self.assertEqual(lib.reports[-1], {"wButtons": 0, "bLeftTrigger": 0, "bRightTrigger": 0, "sThumbLX": 0,
                                               "sThumbLY": 0, "sThumbRX": 0, "sThumbRY": 0})
        finally:
            inj.close()
        self.assertEqual(lib.calls[-4:], ["remove", "target_free", "disconnect", "free"])
        pad.close()  # de novo: nada
        self.assertEqual(lib.calls.count("remove"), 1)

    def test_self_test(self):
        lib = FakeViGEm()

        def read(index):  # o XInput de mentira devolve o último relatório do controle 0
            return win_gamepad.XUSB_REPORT(**lib.reports[-1]) if index == 0 and lib.reports else None
        self.assertEqual(win_gamepad.self_test(win_gamepad.ViGEmPad(lib), read), (True, 0))
        self.assertEqual(lib.reports[-1]["wButtons"], 0x1000)
        self.assertEqual(lib.calls[-4:], ["remove", "target_free", "disconnect", "free"])  # o controle sai
        lib = FakeViGEm()
        ok, reason = win_gamepad.self_test(win_gamepad.ViGEmPad(lib), lambda index: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("XInput did not see", reason)
        self.assertEqual(lib.calls[-1], "free")

    def test_errors(self):
        lib = FakeViGEm(connect=win_gamepad.BUS_NOT_FOUND)
        with self.assertRaisesRegex(RuntimeError, "ViGEmBus driver is not installed"):
            win_gamepad.ViGEmPad(lib)
        self.assertEqual(lib.calls, ["alloc", "connect", "free"])
        lib = FakeViGEm(add=win_gamepad.NO_FREE_SLOT)
        with self.assertRaisesRegex(RuntimeError, "no free controller slot"):
            win_gamepad.ViGEmPad(lib)
        self.assertEqual(lib.calls, ["alloc", "connect", "target_alloc", "add", "target_free", "disconnect", "free"])


class ViGEmBusSetupTest(unittest.TestCase):
    """O --setup baixa o instalador do ViGEmBus (hash fixo) e o roda como administrador."""

    def test_download_checks_the_hash(self):
        import hashlib
        import tempfile
        from unittest import mock
        import win_doctor

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        good = b"installer"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(win_doctor, "VIGEMBUS_SHA256", hashlib.sha256(good).hexdigest()):
            with mock.patch("urllib.request.urlopen", return_value=Resp(good)) as urlopen:
                path = win_doctor.download_vigembus(Path(tmp))
            self.assertEqual(urlopen.call_args[0][0], win_doctor.VIGEMBUS_URL)
            try:  # os certificados do Windows, não só os que o OpenSSL acha gravados
                import truststore
                self.assertIsInstance(urlopen.call_args.kwargs["context"], truststore.SSLContext)
            except ImportError:
                pass
            self.assertEqual(path.read_bytes(), good)
            self.assertEqual(path.name, win_doctor.VIGEMBUS_FILE)
            with mock.patch("urllib.request.urlopen", return_value=Resp(b"tampered")):
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    win_doctor.download_vigembus(Path(tmp) / "other")
            self.assertFalse((Path(tmp) / "other" / win_doctor.VIGEMBUS_FILE).exists())

    def test_install_runs_elevated(self):
        from unittest import mock
        import win_doctor
        exe, log = Path("C:/x/it's.exe"), Path("C:/x/i.log")
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(win_doctor.install_vigembus(exe, quiet=True, log=log), 0)
        script = run.call_args[0][0][-1]
        self.assertIn("-Verb RunAs -Wait -PassThru", script)
        self.assertIn("-FilePath '{}'".format(str(exe).replace("'", "''")), script)  # aspas simples dobradas
        self.assertIn(f"-ArgumentList '/quiet', '/norestart', '/log', '{log}'", script)
        self.assertTrue(script.endswith("exit $p.ExitCode"))


class InstallerScriptTest(unittest.TestCase):
    """O instalador (packaging/windows/pspstream.iss) faz o mesmo que o --setup."""

    def setUp(self):
        self.iss = (ROOT / "packaging" / "windows" / "pspstream.iss").read_text(encoding="utf-8")

    def test_same_firewall_rules_as_setup(self):
        import re
        import win_doctor
        port = re.search(r'#define Port "(\d+)"', self.iss).group(1)
        self.assertEqual(port, "5123")
        rules = [line.split('Parameters: "', 1)[1].split('";', 1)[0].replace('""', '"').replace("{#Port}", port)
                 for line in self.iss.splitlines() if "firewall add rule" in line]
        self.assertEqual(["netsh " + r for r in rules], win_doctor.firewall_commands(int(port)))

    def test_both_languages(self):
        import re
        names = set(re.findall(r"^(?:en|pt)\.(\w+)=", self.iss, re.M))
        for name in names:
            self.assertIn(f"\nen.{name}=", self.iss, name)
            self.assertIn(f"\npt.{name}=", self.iss, name)
        for used in set(re.findall(r"\{cm:(\w+)", self.iss)) - {"CreateDesktopIcon", "AdditionalIcons",
                                                                    "UninstallProgram", "LaunchProgram"}:
            self.assertIn(used, names)

    def test_english_by_default(self):
        self.assertIn("\nLanguageDetectionMethod=none\n", self.iss)  # não segue o idioma do Windows
        languages = self.iss.split("[Languages]\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertTrue(languages[0].startswith('Name: "en"'), languages)  # o primeiro é o padrão

    def test_defines_come_from_win_doctor(self):
        import tempfile
        sys.path.insert(0, str(ROOT / "packaging" / "windows"))
        import installer
        import win_doctor
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "VERSION.txt").write_text("1.2.3\n")
            got = installer.defines(Path(tmp), Path(tmp))
        self.assertIn("/DAppVersion=1.2.3", got)
        self.assertIn(f"/DViGEmBusSHA256={win_doctor.VIGEMBUS_SHA256}", got)
        self.assertIn(f"/DViGEmBusURL={win_doctor.VIGEMBUS_URL}", got)


@unittest.skipUnless(WINDOWS, "só no Windows")
class ViGEmBusTest(unittest.TestCase):
    """O controle virtual de verdade, lido de volta pelo XInput. Precisa da ViGEmClient.dll e do driver
    ViGEmBus; sem eles, pula (PSPSTREAM_REQUIRE_VIGEM=1 faz falhar). Roda num Windows 10/11 com o
    driver: o instalador do ViGEmBus recusa o Windows Server, que é o sistema dos runners do GitHub."""

    def setUp(self):
        self.status, self.detail = win_gamepad.bus_status()
        if os.environ.get("PSPSTREAM_REQUIRE_VIGEM") and self.status != "ok":
            self.fail(self.detail)

    def test_without_driver(self):
        if self.status != "no-bus":
            self.skipTest(self.status)
        with self.assertRaisesRegex(RuntimeError, "ViGEmBus driver is not installed"):
            win_gamepad.ViGEmPad()

    def test_self_test(self):
        if self.status != "ok":
            self.skipTest(self.detail)
        ok, got = win_gamepad.self_test()
        self.assertTrue(ok, got)

    def test_xinput(self):
        if self.status != "ok":
            self.skipTest(self.detail)
        from gamepad import GamepadInjector
        from inject import PSP_BUTTONS as B

        class XINPUT_STATE(ctypes.Structure):
            _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", win_gamepad.XUSB_REPORT)]

        xinput = ctypes.WinDLL("xinput1_4")
        xinput.XInputGetState.argtypes = (ctypes.c_uint32, ctypes.POINTER(XINPUT_STATE))
        xinput.XInputGetState.restype = ctypes.c_uint32

        def read(index):
            st = XINPUT_STATE()
            return st.Gamepad if xinput.XInputGetState(index, ctypes.byref(st)) == 0 else None

        def wait_for(index, check):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                pad = read(index)
                if pad is not None and check(pad):
                    return pad
                time.sleep(0.02)
            self.fail(f"XInput {index}: {read(index) and bytes(read(index)).hex()}")

        pad = win_gamepad.ViGEmPad()
        inj = GamepadInjector(xbox_profile(), timeout=0, out=pad)
        try:
            deadline = time.monotonic() + 3
            while pad.user_index() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            index = pad.user_index()
            self.assertIsNotNone(index, "the Xbox controller got no XInput number")
            inj.update(B["CROSS"] | B["R"], 255, 128)
            got = wait_for(index, lambda g: g.wButtons & 0x1000)
            self.assertEqual(got.bRightTrigger, 255)
            self.assertGreater(got.sThumbLX, 30000)
            inj.update(0, 128, 128)
            wait_for(index, lambda g: g.wButtons == 0 and g.sThumbLX == 0)
        finally:
            inj.close()
        time.sleep(0.5)
        self.assertIsNone(read(index), "the controller is still there after close()")


@unittest.skipUnless(HAS_GST and openh264_ok(), "sem o gst-launch ou a libopenh264")
class PipeServerTest(unittest.TestCase):
    """O servidor inteiro pelo caminho do Windows (PSPSTREAM_CAPTURE=pipe no Linux) e o PSP falso."""

    def test_stream_with_audio(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            server = subprocess.Popen(
                [sys.executable, str(ROOT / "server" / "pspstream.py"), "--source", "test", "--no-input",
                 "--audio-device", "test", "--port", str(port), "--no-web", "--config", str(Path(tmp) / "s.json")],
                env={**os.environ, "PSPSTREAM_CAPTURE": "pipe", "PSPSTREAM_LANG": "en"},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                time.sleep(3)
                out = subprocess.run([sys.executable, str(ROOT / "tools" / "fake_client.py"), "127.0.0.1",
                                      "--port", str(port), "--transport", "udp", "--h264p", "--audio",
                                      "--seconds", "3", "--json"], capture_output=True, text=True, timeout=60)
            finally:
                server.terminate()
                log = server.communicate(timeout=10)[0]
        self.assertEqual(out.returncode, 0, out.stderr + log)
        summary = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertGreater(summary["frames"], 100, log)
        self.assertEqual(summary["broken"], 0)
        self.assertGreater(summary["audio_packets"], 50, log)


@unittest.skipUnless(WINDOWS, "só no Windows")
class WindowsApiTest(unittest.TestCase):
    def test_send_input(self):
        out = win_input.SendInputOutput({"KEY_A", "BTN_LEFT"})
        out.move(0, 0)  # um movimento nulo: inofensivo, mas passa pelo SendInput
        self.assertFalse(out.blocked)

    def test_job_object(self):
        self.assertIsNotNone(gst_pipe._job())

    def test_sockets(self):
        import pspstream
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            pspstream.no_udp_connreset(u)
        a = socket.create_server(("127.0.0.1", 0))
        b = socket.create_connection(a.getsockname())
        gst_pipe.quick_ack(b)
        b.close()
        a.close()

    def test_utf8_in_a_pipe(self):
        # num pipe o Python do Windows escreveria em cp1252
        out = subprocess.run([sys.executable, str(ROOT / "server" / "pspstream.py"), "--lang", "pt", "--help"],
                             capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("porta TCP e UDP (padrão", out.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
