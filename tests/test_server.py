"""Testes do servidor que não precisam de GStreamer, uinput nem PSP.

  python3 -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import protocol  # noqa: E402
from adaptive import AdaptiveQuality  # noqa: E402
from inject import Injector, PSP_BUTTONS, load_profile  # noqa: E402
from jpeginfo import jpeg_info  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def test_request_roundtrip(self):
        req = protocol.Request(buttons=0x4010, lx=12, ly=250, flags=protocol.REQ_FRAME, ack_frame=7,
                               echo_ts=0xFFFFFFF0, net_t=123, local_t=45, since_t=6, decode_t=108)
        data = req.pack()
        self.assertEqual(len(data), 28)
        self.assertEqual(data[:4], b"PSC1")
        self.assertEqual(protocol.Request.unpack(data), req)

    def test_frame_header(self):
        hdr = protocol.pack_frame_header(3, 12345, 2**32 + 5)  # send_ts dá a volta em 32 bits
        self.assertEqual(len(hdr), 16)
        self.assertEqual(protocol.unpack_frame_header(hdr), (3, 12345, 5))

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            protocol.Request.unpack(b"XXXX" + bytes(24))

    def test_matches_c_header(self):
        header = (ROOT / "psp/src/protocol.h").read_text()
        self.assertIn(f"#define PS_DEFAULT_PORT {protocol.DEFAULT_PORT}", header)
        self.assertIn("#define PS_MAX_JPEG (256 * 1024)", header)
        self.assertEqual(protocol.MAX_JPEG, 256 * 1024)
        self.assertIn('0x31435350u /* "PSC1" */', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_REQ, "little"), 0x31435350)
        self.assertEqual(int.from_bytes(protocol.MAGIC_FRAME, "little"), 0x31465350)


class JpegInfoTest(unittest.TestCase):
    def test_testcard(self):
        info = jpeg_info((ROOT / "assets/testcard.jpg").read_bytes())
        self.assertEqual((info.width, info.height), (480, 272))
        self.assertTrue(info.is_420)
        self.assertFalse(info.progressive)
        self.assertEqual(info.problems(), [])

    def test_not_jpeg(self):
        with self.assertRaises(ValueError):
            jpeg_info(b"\x89PNG....")


class InjectorTest(unittest.TestCase):
    def make(self, profile="jogo"):
        return Injector(load_profile(str(ROOT / "server/keymap.json"), profile), dry_run=True)

    def keys(self, inj):
        return [e[1:] for e in inj.out.events if e[0] == "key"]

    def test_press_release(self):
        inj = self.make()
        inj.update(PSP_BUTTONS["CROSS"], 128, 128)
        inj.update(PSP_BUTTONS["CROSS"], 128, 128)  # repetido: nada novo
        inj.update(0, 128, 128)
        self.assertEqual(self.keys(inj), [("KEY_SPACE", True), ("KEY_SPACE", False)])
        inj.close()

    def test_combo_order(self):
        inj = self.make("desktop")  # SELECT = Alt+Tab
        inj.update(PSP_BUTTONS["SELECT"], 128, 128)
        inj.update(0, 128, 128)
        self.assertEqual(self.keys(inj), [("KEY_LEFTALT", True), ("KEY_TAB", True),
                                          ("KEY_TAB", False), ("KEY_LEFTALT", False)])
        inj.close()

    def test_release_all_on_disconnect(self):
        inj = self.make()
        inj.update(PSP_BUTTONS["UP"] | PSP_BUTTONS["R"], 128, 128)
        inj.release_all()
        released = {k for k, down in self.keys(inj) if not down}
        self.assertEqual(released, {"KEY_W", "BTN_LEFT"})
        inj.close()

    def test_analog_deadzone_and_curve(self):
        inj = self.make()
        self.assertEqual(inj._axis(128), 0.0)
        self.assertEqual(inj._axis(140), 0.0)  # dentro da zona morta
        self.assertAlmostEqual(inj._axis(255), 1.0)
        self.assertAlmostEqual(inj._axis(1), -1.0)
        self.assertLess(inj._axis(192), 0.5)  # curva: meio curso < metade da velocidade
        inj.close()

    def test_analog_keys_mode(self):
        inj = self.make("setas")
        inj.update(0, 255, 128)
        inj.update(0, 128, 128)
        self.assertEqual(self.keys(inj), [("KEY_RIGHT", True), ("KEY_RIGHT", False)])
        inj.close()


class FakeSource:
    """Tamanho do frame cresce com a qualidade (aprox. do que medimos)."""

    def __init__(self, quality):
        self.quality = quality

    def set_quality(self, q):
        self.quality = q

    def frame_size(self):
        return int(6000 + 250 * self.quality)  # q50 ~ 18 KB, q90 ~ 28 KB


class AdaptiveTest(unittest.TestCase):
    def run_link(self, kbps, decode_ms, start_q, steps=200):
        src = FakeSource(start_q)
        ctl = AdaptiveQuality(src, target_fps=30, q_min=25, q_max=90, interval=0)
        rate = kbps * 1024 / 1000  # bytes/ms
        for _ in range(steps):
            size = src.frame_size()
            ctl.on_ack(size, size / rate + 2.0, decode_ms)  # +2 ms fixos de RTT
            ctl.update()
        return src.quality, src.frame_size() / rate + 2.0

    def test_slow_link_lowers_quality(self):
        q, transfer = self.run_link(kbps=500, decode_ms=11, start_q=90)
        self.assertLess(q, 60)
        self.assertLess(transfer, 33.3 * 1.1)  # cabe em ~1/30 s

    def test_unreachable_target_stops_at_q_min(self):
        q, _ = self.run_link(kbps=200, decode_ms=11, start_q=90)
        self.assertEqual(q, 25)

    def test_fast_link_raises_quality(self):
        q, _ = self.run_link(kbps=2000, decode_ms=11, start_q=30)
        self.assertEqual(q, 90)

    def test_slow_decode_allows_bigger_frames(self):
        # decode de 60 ms: não adianta transferir em 33 ms, então a qualidade sobe
        q_fast, _ = self.run_link(kbps=400, decode_ms=11, start_q=60)
        q_slow, _ = self.run_link(kbps=400, decode_ms=60, start_q=60)
        self.assertGreater(q_slow, q_fast)


if __name__ == "__main__":
    unittest.main()
