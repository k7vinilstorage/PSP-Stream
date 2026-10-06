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
        self.assertEqual(got["profiles"], ["arrows", "desktop", "game"])

    def test_linux_keeps_its_options(self):
        if WINDOWS:
            self.skipTest("no Windows")
        import settings
        self.assertEqual(settings.BY_KEY["source"].choices[0], "portal")
        self.assertNotIn("monitor", settings.BY_KEY)


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


if __name__ == "__main__":
    unittest.main()
