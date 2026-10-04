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
import stats  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def test_request_roundtrip(self):
        req = protocol.Request(buttons=0x4010, lx=12, ly=250, flags=protocol.REQ_FRAME, ack_frame=7,
                               echo_ts=0xFFFFFFF0, net_t=123, local_t=45, since_t=6, decode_t=108,
                               first_t=210, burst_t=260, signal=87, wflags=protocol.WIFI_POWER_SAVE, lost=3,
                               hdr_have=0x7ABCDEF1, ping_select=52, ping_poll=31, ping_live=250, ping_live_min=61,
                               idle_t=-123, early_b=2150)
        data = req.pack()
        self.assertEqual(len(data), 52)
        self.assertEqual(data[:4], b"PSC5")
        self.assertEqual(protocol.Request.unpack(data), req)
        self.assertEqual(protocol.Request().idle_t, protocol.IDLE_NONE)  # TCP / não medido

    def test_frame_header(self):
        hdr = protocol.pack_frame_header(3, 12345, 2**32 + 5)  # send_ts dá a volta em 32 bits
        self.assertEqual(len(hdr), 16)
        self.assertEqual(protocol.unpack_frame_header(hdr), (3, 12345, 5))

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            protocol.Request.unpack(b"XXXX" + bytes(48))
        with self.assertRaisesRegex(ValueError, "curto"):
            protocol.Request.unpack(protocol.MAGIC_REQ + bytes(44))

    def test_old_eboot_rejected_clearly(self):
        for magic in (b"PSC1", b"PSC2", b"PSC3", b"PSC4"):
            with self.assertRaisesRegex(protocol.OldEbootError, "versão antiga"):
                protocol.Request.unpack(magic + bytes(44))

    def test_matches_c_header(self):
        header = (ROOT / "psp/src/protocol.h").read_text()
        self.assertIn(f"#define PS_DEFAULT_PORT {protocol.DEFAULT_PORT}", header)
        self.assertIn("#define PS_MAX_JPEG (256 * 1024)", header)
        self.assertEqual(protocol.MAX_JPEG, 256 * 1024)
        self.assertIn('0x35435350u /* "PSC5"', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_REQ, "little"), 0x35435350)
        self.assertIn(f"#define PS_IDLE_NONE (-0x{-protocol.IDLE_NONE:04x})", header)
        self.assertIn(f'_Static_assert(sizeof(ps_req_t) == {protocol.REQ_STRUCT.size}', header)
        self.assertIn(f"#define PS_WIFI_POWER_SAVE 0x{protocol.WIFI_POWER_SAVE:02x}", header)
        self.assertIn(f"#define PS_WIFI_RX_POLL 0x{protocol.WIFI_RX_POLL:02x}", header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_FRAME, "little"), 0x31465350)


class UdpChunkTest(unittest.TestCase):
    def test_chunk_roundtrip(self):
        jpeg = bytes(range(256)) * 23  # 5888 bytes -> 5 pedaços, o último com 288
        count = protocol.chunk_count(len(jpeg))
        self.assertEqual(count, 5)
        rebuilt = bytearray(len(jpeg))
        for i in reversed(range(count)):  # fora de ordem de propósito
            frame_no, size, ts, idx, n, hdr, payload = protocol.unpack_chunk(protocol.pack_chunk(9, jpeg, 77, i, 5))
            self.assertEqual((frame_no, size, ts, idx, n, hdr), (9, len(jpeg), 77, i, count, 5))
            rebuilt[idx * protocol.CHUNK_PAYLOAD: idx * protocol.CHUNK_PAYLOAD + len(payload)] = payload
        self.assertEqual(bytes(rebuilt), jpeg)
        self.assertEqual(len(protocol.pack_chunk(9, jpeg, 77, 4)), protocol.CHUNK_HDR_STRUCT.size + 288)

    def test_datagram_fits_802_11(self):
        jpeg = bytes(protocol.MAX_JPEG)
        self.assertLessEqual(len(protocol.pack_chunk(1, jpeg, 0, 0)) + 28, 1500)  # + IP/UDP
        self.assertLessEqual(protocol.chunk_count(len(jpeg)), protocol.MAX_CHUNKS)

    def test_nack_roundtrip(self):
        self.assertEqual(protocol.unpack_nack(protocol.pack_nack(3, [0, 5, 31, 32, 200, 255])),
                         (3, [0, 5, 31, 32, 200, 255]))

    def test_jpeg_header_split(self):
        # O que o servidor tira e o PSP põe de volta: SOI até o fim do SOS.
        card = (ROOT / "assets/testcard.jpg").read_bytes()
        n = protocol.jpeg_header_len(card)
        self.assertGreater(n, 100)
        self.assertEqual(card[n - 14:n - 12], b"\xff\xda")  # SOS de 3 componentes: 12 bytes + marcador
        self.assertEqual(protocol.jpeg_header_len(b"not a jpeg"), 0)
        hid = protocol.jpeg_header_id(card[:n])
        self.assertTrue(0 < hid <= 0x7FFFFFFF)

    def test_matches_c_header(self):
        header = (ROOT / "psp/src/protocol.h").read_text()
        self.assertIn('0x32555350u /* "PSU2" */', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_CHUNK, "little"), 0x32555350)
        self.assertIn('0x314F5350u /* "PSO1" */', header)
        self.assertEqual(int.from_bytes(protocol.MAGIC_PONG, "little"), 0x314F5350)
        self.assertIn(f"_Static_assert(sizeof(ps_chunk_hdr_t) == {protocol.CHUNK_HDR_STRUCT.size}", header)
        self.assertIn(f"#define PS_HDR_STRIPPED 0x{protocol.HDR_STRIPPED:08x}u", header)
        self.assertIn(f"#define PS_MAX_JPEG_HEADER {protocol.MAX_JPEG_HEADER}", header)
        self.assertIn(f"#define PS_REQ_PING 0x{protocol.REQ_PING:04x}", header)
        self.assertIn(f"#define PS_CHUNK_PAYLOAD {protocol.CHUNK_PAYLOAD}", header)
        self.assertIn(f"#define PS_MAX_CHUNKS {protocol.MAX_CHUNKS}", header)
        self.assertIn(f"#define PS_REQ_BYE 0x{protocol.REQ_BYE:04x}", header)


class UdpEndToEndTest(unittest.TestCase):
    """Servidor UDP de verdade + cliente falso (mesma lógica do PSP) com perda."""

    def run_stream(self, early_kb, loss=0.05, seconds=2.0, rtt_ms=0, source=None, hdr_cache=True):
        import pspstream
        from sources import StaticSource
        import fake_client

        card = (ROOT / "assets/testcard.jpg").read_bytes()
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, target_fps=30, q_min=25,
                                  q_max=90, udp_pace=0, source="static", size=(480, 272), hdr_cache=hdr_cache,
                                  dscp="ef", codec="jpeg")
        server = pspstream.Server(source or StaticSource(card), args, None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        threading.Thread(target=server.serve_udp, args=(sock,), daemon=True).start()
        try:
            client_args = argparse.Namespace(host="127.0.0.1", port=port, transport="udp", loss=loss, kbps=2000,
                                             decode_ms=5, no_prefetch=False, frames=0, seconds=seconds,
                                             input_demo=False, rtt_ms=rtt_ms, early_kb=early_kb, loss_up=0)
            summary, jpeg = fake_client.FakePSP(client_args).run()
            self.session = server.current[0] if server.current else None
        finally:
            server.close()
            sock.close()
        return summary, jpeg, card

    def test_nack_recovers_losses(self):
        summary, jpeg, card = self.run_stream(early_kb=0)
        self.assertGreater(summary["frames"], 20)
        self.assertGreater(summary["nacks"], 0)  # perdas aconteceram e foram pedidas de novo
        self.assertEqual(jpeg, card)             # e o frame chegou inteiro

    def test_nack_waits_for_round_trip(self):
        # Ida e volta de 30 ms, como no PSP real. Um NACK repetido antes de o
        # reenvio voltar pede os mesmos pedaços de novo: chegam repetidos e
        # ocupam o ar (regressão vista no PSP-3000 com espera de 6 ms).
        summary, jpeg, card = self.run_stream(early_kb=0, loss=0.05, seconds=3.0, rtt_ms=30)
        self.assertGreater(summary["frames"], 20)
        self.assertGreater(summary["nacks"], 0)
        self.assertLessEqual(summary["dup_chunks"], max(3, summary["lost_chunks"] // 5), summary)
        self.assertLessEqual(summary["lost"], 2, summary)
        self.assertEqual(jpeg, card)

    def test_header_cache(self):
        # Depois do primeiro frame, o cabeçalho JPEG não vai mais; o PSP o põe de
        # volta e o JPEG fica idêntico ao original.
        summary, jpeg, card = self.run_stream(early_kb=0, loss=0.02)
        self.assertEqual(jpeg, card)
        self.assertGreaterEqual(summary["stripped"], summary["frames"] - 2, summary)
        self.assertGreater(summary["ping_ms"], 0)
        off, _, _ = self.run_stream(early_kb=0, loss=0.02, hdr_cache=False)
        self.assertEqual(off["stripped"], 0)

    def test_header_cache_follows_quality(self):
        # Qualidade mudando (adaptativo): cada cabeçalho novo vem inteiro uma
        # vez; com o cabeçalho errado, o JPEG sairia com outras tabelas.
        from sources import FrameSource
        card = (ROOT / "assets/testcard.jpg").read_bytes()
        # segundo cabeçalho: a mesma imagem com um comentário (COM) a mais
        variants = [card, card[:2] + b"\xff\xfe\x00\x06test" + card[2:]]

        class Alternating(FrameSource):
            repeat = True

            def __init__(self):
                super().__init__()
                self.k = 0
                self.publish(variants[0])

            def latest(self):
                self.k += 1
                seq, _, ready = super().latest()
                return seq, variants[self.k // 5 % 2], ready

        summary, jpeg, _ = self.run_stream(early_kb=0, loss=0.02, source=Alternating())
        self.assertIn(jpeg, variants)
        self.assertGreater(summary["stripped"], summary["frames"] // 2, summary)

    def test_h264_static(self):
        # --codec h264: todo frame é um AU Annex B (SPS + PPS + IDR), sem cache de cabeçalho
        try:
            import h264
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        if not h264.available():
            self.skipTest("sem openh264enc")
        from sources import StaticSource
        raw = h264.image_to_i420(str(ROOT / "assets/testcard.jpg"), 480, 272)
        au = h264.H264Encoder(480, 272, 60).encode(raw)
        self.assertTrue(h264.is_h264(au))
        self.assertEqual(protocol.jpeg_header_len(au), 0)
        summary, got, _ = self.run_stream(early_kb=0, loss=0.02, source=StaticSource(au))
        self.assertGreater(summary["frames"], 20)
        self.assertEqual(got, au)
        self.assertEqual(summary["stripped"], 0)

    def test_early_request_keeps_streaming(self):
        # Com pedido antecipado, uma perda no fim do frame N vira pulo para o
        # N+1 (que já está chegando) em vez de esperar o NACK.
        summary, jpeg, card = self.run_stream(early_kb=10)
        self.assertGreater(summary["frames"], 20)
        self.assertEqual(jpeg, card)

    def test_early_auto_reports_threshold(self):
        # early_kb=auto (padrão do PSP): limite = ping x vazão, informado ao
        # servidor junto com o tempo morto entre frames
        summary, jpeg, card = self.run_stream(early_kb="auto", loss=0.02, rtt_ms=8)
        self.assertGreater(summary["frames"], 20)
        self.assertEqual(jpeg, card)
        w = self.session.stats.phase
        self.assertTrue(w.early, "o PSP falso não informou o limite")
        self.assertTrue(all(0 < e <= 8 * 1024 for e in w.early), w.early)
        self.assertTrue(w.idle)
        s = w.summary(60)
        self.assertGreater(s["early_kb"], 0)
        self.assertIn("tempo morto", stats.format_summary(s))

    def test_no_new_session_while_closing(self):
        # Ctrl+C com o PSP mandando pedidos: um pedido que chega durante o
        # encerramento não abre sessão nova ("PSP conectado" depois de "encerrando")
        import pspstream
        from sources import StaticSource
        from transports import UdpTransport
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, udp_pace=0, hdr_cache=True,
                                  codec="jpeg")
        server = pspstream.Server(StaticSource((ROOT / "assets/testcard.jpg").read_bytes()), args, None)
        server.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.assertIsNone(server.replace(UdpTransport(sock, ("127.0.0.1", 9), 0, True)))
            self.assertIsNone(server.current)
        finally:
            sock.close()

    def test_old_eboot_warned_once(self):
        import pspstream
        from sources import StaticSource
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, udp_pace=0, hdr_cache=True,
                                  codec="jpeg")
        server = pspstream.Server(StaticSource((ROOT / "assets/testcard.jpg").read_bytes()), args, None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        threading.Thread(target=server.serve_udp, args=(sock,), daemon=True).start()
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with self.assertLogs("pspstream", "WARNING") as logs:
                for _ in range(3):
                    client.sendto(b"PSC4" + bytes(44), sock.getsockname())
                time.sleep(0.3)
        finally:
            server.close()
            sock.close()
            client.close()
        self.assertEqual(len(logs.output), 1, logs.output)
        self.assertIn("v4", logs.output[0])


class CaptureRateTest(unittest.TestCase):
    """O portal entrega taxa variável com horários tremidos: o limite de --fps
    não pode cortar uma fonte de 60 Hz (o videorate deixava ~38 fps)."""

    def rate(self, src_hz, fps, jitter_ms=2.0, seconds=10):
        try:
            import gst_source
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        import random
        rnd = random.Random(1)
        lim = gst_source.RateLimiter(fps)
        n = int(src_hz * seconds)
        kept = sum(lim.keep(int(max(0.0, i / src_hz + rnd.uniform(-jitter_ms, jitter_ms) / 1000) * 1e9))
                   for i in range(n))
        return kept / seconds

    def test_60hz_source_passes_whole(self):
        self.assertGreater(self.rate(60, 60), 59.5)
        self.assertGreater(self.rate(59.94, 60, jitter_ms=3), 59.4)

    def test_limits_faster_sources(self):
        self.assertLess(self.rate(144, 60), 75)
        self.assertGreater(self.rate(144, 60), 55)
        self.assertAlmostEqual(self.rate(60, 30), 30, delta=1.5)

    def test_slower_source_untouched(self):
        self.assertGreater(self.rate(40, 60, jitter_ms=4), 39.5)

    def test_pipeline_has_no_videorate(self):
        try:
            import gst_source
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        desc = gst_source.build_pipeline("videotestsrc", 480, 272, 60, 60, codec="h264")
        self.assertNotIn("videorate", desc)
        self.assertIn("queue name=q", desc)


class DmabufCaptureTest(unittest.TestCase):
    """--dmabuf: redução no OpenGL. Sem GPU aqui, o teste usa EGL sem tela
    (llvmpipe) e uma fonte em memória comum no lugar do DMA-BUF do portal."""

    SRC = "videotestsrc is-live=true pattern=white ! video/x-raw,width=2240,height=1400,format=BGRA,framerate=30/1"

    def setUp(self):
        try:
            import gst_source
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        from gi.repository import Gst
        if not Gst.ElementFactory.find("glupload"):
            self.skipTest("sem os elementos OpenGL do GStreamer")
        import os
        os.environ.setdefault("GST_GL_WINDOW", "surfaceless")
        os.environ.setdefault("GST_GL_PLATFORM", "egl")
        self.gst_source = gst_source

    def test_gpu_size_keeps_aspect(self):
        self.assertEqual(self.gst_source.gpu_size((2240, 1400), 480, 272), (436, 272))
        self.assertEqual(self.gst_source.gpu_size((1920, 1080), 480, 272), (480, 270))
        self.assertEqual(self.gst_source.gpu_size((2240, 1400), 480, 272, keep_aspect=False), (480, 272))

    def test_gl_chain_letterboxes(self):
        desc = self.gst_source.build_pipeline(self.SRC, 480, 272, 60, 60, codec="h264", gpu_from=(2240, 1400))
        self.assertIn('caps="video/x-raw(memory:DMABuf)"', desc)
        from gi.repository import Gst
        pipe = Gst.parse_launch(desc.replace('! capsfilter caps="video/x-raw(memory:DMABuf)" ', ""))
        pipe.set_state(Gst.State.PLAYING)
        try:
            sample = pipe.get_by_name("sink").emit("try-pull-sample", 10 * Gst.SECOND)
        finally:
            pipe.set_state(Gst.State.NULL)
        self.assertIsNotNone(sample, "o OpenGL não entregou frame")
        caps = sample.get_caps().get_structure(0)
        self.assertEqual((caps.get_value("width"), caps.get_value("height")), (480, 272))
        data = sample.get_buffer().extract_dup(0, 480 * 272)  # plano Y
        row = data[136 * 480:137 * 480]
        self.assertLess(row[2], 40)      # borda preta (436 de 480 com imagem)
        self.assertGreater(row[240], 200)  # fonte branca no meio

    def test_fallback_without_dmabuf(self):
        # o pipeline nem monta (a fonte não oferece DMA-BUF)
        self.check_fallback(self.SRC)

    def test_fallback_when_negotiation_fails_later(self):
        # como o pipewiresrc: monta, e a negociação falha só com o pipeline rodando
        self.check_fallback(self.SRC + " ! identity")

    def check_fallback(self, src):
        import argparse
        import pspstream

        class FakePortal:
            size = (2240, 1400)

            def gst_source(_, dmabuf=False):
                return src

        args = argparse.Namespace(source="portal", dmabuf=True, size=(480, 272), fps=60, quality=60,
                                  scale="bilinear", stretch=False, codec="h264")
        with self.assertLogs("pspstream", "WARNING") as logs:
            source = pspstream.start_source(args, FakePortal())
        try:
            self.assertFalse(source.gpu)
            self.assertFalse(args.dmabuf)
            self.assertIsNotNone(source.wait_newer(0, 5), "o modo normal não entregou frame")
        finally:
            source.stop()
        self.assertIn("--dmabuf não funcionou", logs.output[0])


class H264QualityTest(unittest.TestCase):
    def test_qp_mapping(self):
        try:
            import h264
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        # calibrado na mesma SSIM do jpegenc (docs/MEASUREMENTS.md)
        self.assertEqual([h264.qp_for_quality(q) for q in (30, 50, 70, 90)], [40, 37, 33, 30])
        self.assertEqual(h264.qp_for_quality(1), 44)
        self.assertEqual(h264.qp_for_quality(100), 29)
        self.assertTrue(h264.is_h264(b"\x00\x00\x00\x01\x67"))
        self.assertFalse(h264.is_h264(b"\xff\xd8\xff"))


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
