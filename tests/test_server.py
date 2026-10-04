"""Testes do servidor que não precisam de GStreamer, uinput nem PSP.

  python3 -m unittest discover tests
"""
import argparse
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

import protocol  # noqa: E402
from adaptive import AdaptiveQuality  # noqa: E402
from inject import Injector, PSP_BUTTONS, load_profile  # noqa: E402
from jpeginfo import jpeg_info  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def test_request_roundtrip(self):
        req = protocol.Request(buttons=0x4010, lx=12, ly=250, flags=protocol.REQ_FRAME, ack_frame=7,
                               echo_ts=0xFFFFFFF0, net_t=123, local_t=45, since_t=6, decode_t=108,
                               first_t=210, burst_t=260, signal=87, wflags=protocol.WIFI_POWER_SAVE, lost=3)
        data = req.pack()
        self.assertEqual(len(data), 36)
        self.assertEqual(data[:4], b"PSC2")
        self.assertEqual(protocol.Request.unpack(data), req)

    def test_frame_header(self):
        hdr = protocol.pack_frame_header(3, 12345, 2**32 + 5)  # send_ts dá a volta em 32 bits
        self.assertEqual(len(hdr), 16)
        self.assertEqual(protocol.unpack_frame_header(hdr), (3, 12345, 5))

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            protocol.Request.unpack(b"XXXX" + bytes(32))

    def test_old_eboot_rejected_clearly(self):
        with self.assertRaisesRegex(ValueError, "versão antiga"):
            protocol.Request.unpack(b"PSC1" + bytes(32))

    def test_matches_c_header(self):
        header = (ROOT / "psp/src/protocol.h").read_text()
        self.assertIn(f"#define PS_DEFAULT_PORT {protocol.DEFAULT_PORT}", header)
        self.assertIn("#define PS_MAX_JPEG (256 * 1024)", header)
        self.assertEqual(protocol.MAX_JPEG, 256 * 1024)
        self.assertIn('0x32435350u /* "PSC2"', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_REQ, "little"), 0x32435350)
        self.assertIn('_Static_assert(sizeof(ps_req_t) == 36', header)
        self.assertIn(f"#define PS_WIFI_POWER_SAVE 0x{protocol.WIFI_POWER_SAVE:02x}", header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_FRAME, "little"), 0x31465350)


class UdpChunkTest(unittest.TestCase):
    def test_chunk_roundtrip(self):
        jpeg = bytes(range(256)) * 23  # 5888 bytes -> 5 pedaços, o último com 288
        count = protocol.chunk_count(len(jpeg))
        self.assertEqual(count, 5)
        rebuilt = bytearray(len(jpeg))
        for i in reversed(range(count)):  # fora de ordem de propósito
            frame_no, size, ts, idx, n, payload = protocol.unpack_chunk(protocol.pack_chunk(9, jpeg, 77, i))
            self.assertEqual((frame_no, size, ts, idx, n), (9, len(jpeg), 77, i, count))
            rebuilt[idx * protocol.CHUNK_PAYLOAD: idx * protocol.CHUNK_PAYLOAD + len(payload)] = payload
        self.assertEqual(bytes(rebuilt), jpeg)
        self.assertEqual(len(protocol.pack_chunk(9, jpeg, 77, 4)), 20 + 288)

    def test_datagram_fits_802_11(self):
        jpeg = bytes(protocol.MAX_JPEG)
        self.assertLessEqual(len(protocol.pack_chunk(1, jpeg, 0, 0)) + 28, 1500)  # + IP/UDP
        self.assertLessEqual(protocol.chunk_count(len(jpeg)), protocol.MAX_CHUNKS)

    def test_nack_roundtrip(self):
        self.assertEqual(protocol.unpack_nack(protocol.pack_nack(3, [0, 5, 31, 32, 200, 255])),
                         (3, [0, 5, 31, 32, 200, 255]))

    def test_matches_c_header(self):
        header = (ROOT / "psp/src/protocol.h").read_text()
        self.assertIn('0x31555350u /* "PSU1" */', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_CHUNK, "little"), 0x31555350)
        self.assertIn(f"#define PS_CHUNK_PAYLOAD {protocol.CHUNK_PAYLOAD}", header)
        self.assertIn(f"#define PS_MAX_CHUNKS {protocol.MAX_CHUNKS}", header)
        self.assertIn(f"#define PS_REQ_BYE 0x{protocol.REQ_BYE:04x}", header)


class UdpEndToEndTest(unittest.TestCase):
    """Servidor UDP de verdade + cliente falso (mesma lógica do PSP) com perda."""

    def run_stream(self, early_kb, loss=0.05, seconds=2.0):
        import pspstream
        from sources import StaticSource
        import fake_client

        card = (ROOT / "assets/testcard.jpg").read_bytes()
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, target_fps=30, q_min=25,
                                  q_max=90, udp_pace=0, source="static", size=(480, 272))
        server = pspstream.Server(StaticSource(card), args, None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        threading.Thread(target=server.serve_udp, args=(sock,), daemon=True).start()
        try:
            client_args = argparse.Namespace(host="127.0.0.1", port=port, transport="udp", loss=loss, kbps=2000,
                                             decode_ms=5, no_prefetch=False, frames=0, seconds=seconds,
                                             input_demo=False, rtt_ms=0, early_kb=early_kb)
            summary, jpeg = fake_client.FakePSP(client_args).run()
        finally:
            server.close()
            sock.close()
        return summary, jpeg, card

    def test_nack_recovers_losses(self):
        summary, jpeg, card = self.run_stream(early_kb=0)
        self.assertGreater(summary["frames"], 20)
        self.assertGreater(summary["nacks"], 0)  # perdas aconteceram e foram pedidas de novo
        self.assertEqual(jpeg, card)             # e o frame chegou inteiro

    def test_early_request_keeps_streaming(self):
        # Com pedido antecipado, uma perda no fim do frame N vira pulo para o
        # N+1 (que já está chegando) em vez de esperar o NACK.
        summary, jpeg, card = self.run_stream(early_kb=10)
        self.assertGreater(summary["frames"], 20)
        self.assertEqual(jpeg, card)


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

    def test_watchdog_releases_stuck_keys(self):
        inj = Injector(load_profile(str(ROOT / "server/keymap.json"), "jogo"), dry_run=True, timeout=0.1)
        inj.update(PSP_BUTTONS["CROSS"], 255, 128)  # tecla + analógico segurados
        time.sleep(0.35)                             # PSP "sumiu"
        self.assertIn(("KEY_SPACE", False), self.keys(inj))
        self.assertEqual((inj.ax, inj.ay), (0.0, 0.0))
        inj.update(PSP_BUTTONS["CROSS"], 128, 128)  # voltou ainda segurando: aperta de novo
        self.assertEqual(self.keys(inj)[-1], ("KEY_SPACE", True))
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
