"""Testes do servidor que não precisam de GStreamer, uinput nem PSP.

  python3 -m unittest discover tests
"""
import argparse
import math
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
        self.assertIn(f"#define PS_CAP_H264 0x{protocol.CAP_H264:02x}", header)
        self.assertIn(f"#define PS_CAP_H264P 0x{protocol.CAP_H264P:02x}", header)
        self.assertIn(f"#define PS_REQ_IDR 0x{protocol.REQ_IDR:04x}", header)
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

    def run_stream(self, early_kb, loss=0.05, seconds=2.0, rtt_ms=0, source=None, hdr_cache=True,
                   codec="jpeg", h264p=False, decode_ms=5, p_redundancy_ms=6, req_dup=True):
        import pspstream
        from sources import StaticSource
        import fake_client

        card = (ROOT / "assets/testcard.jpg").read_bytes()
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, target_fps=30, q_min=25,
                                  q_max=90, udp_pace=0, source="static", size=(480, 272), hdr_cache=hdr_cache,
                                  dscp="ef", codec=codec, quality=70, p_redundancy_ms=p_redundancy_ms)
        server = pspstream.Server(source or StaticSource(card), args, None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        threading.Thread(target=server.serve_udp, args=(sock,), daemon=True).start()
        try:
            client_args = argparse.Namespace(host="127.0.0.1", port=port, transport="udp", loss=loss, kbps=2000,
                                             decode_ms=decode_ms, no_prefetch=False, frames=0, seconds=seconds,
                                             input_demo=False, rtt_ms=rtt_ms, early_kb=early_kb, loss_up=0,
                                             h264p=h264p, loss_burst_ms=0, no_req_dup=not req_dup,
                                             old_retry=False, req_dup_ms=6)
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

    def h264_or_skip(self):
        try:
            import h264
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        if not h264.available():
            self.skipTest("sem openh264enc")
        return h264

    def h264p_with_loss(self, **kw):
        h264 = self.h264_or_skip()
        from sources import StaticSource
        raw = h264.image_to_i420(str(ROOT / "assets/testcard.jpg"), 480, 272)
        return self.run_stream(early_kb="auto", loss=0.05, seconds=3.0, rtt_ms=5, codec="h264p", h264p=True,
                               decode_ms=12, source=StaticSource(raw, quality=70, raw_i420=True), **kw)

    def test_h264p_survives_loss(self):
        # --codec h264p com perda: nenhum frame P pode ser decodificado sem o
        # anterior (imagem errada no PSP). Sem as cópias (v0.9), o frame pequeno
        # perdido inteiro volta pelo pedido repetido com NACK, sem precisar de IDR.
        import fake_client
        summary, got, _ = self.h264p_with_loss(p_redundancy_ms=0, req_dup=False)
        self.assertGreater(summary["frames"], 30, summary)
        self.assertEqual(summary["broken"], 0, summary)
        self.assertEqual(fake_client.h264_packet_kind(got), 1)  # frame P (imagem parada: quase nada)
        self.assertLess(summary["kb_per_frame"], 1.0, summary)
        self.assertGreater(summary["retries"], 0, summary)  # perdas de frame inteiro aconteceram
        self.assertLessEqual(summary["idr_requests"], 2, summary)
        self.assertGreater(self.session.transport.retry_resends, 0)

    def test_h264p_redundancy_under_loss(self):
        # v1.0: último pedaço em dobro (servidor) e pedido em dobro (PSP): a
        # corrente continua inteira.
        summary, _, _ = self.h264p_with_loss()
        self.assertGreater(summary["frames"], 30, summary)
        self.assertEqual(summary["broken"], 0, summary)
        self.assertLessEqual(summary["idr_requests"], 2, summary)
        self.assertGreater(summary["req_dups"], 0, summary)
        self.assertGreater(self.session.transport.redundant_chunks, 0)

    def test_h264p_old_eboot_gets_intra(self):
        # EBOOT sem PS_CAP_H264P (v0.5-v0.8): todo frame IDR, sem AUD
        h264 = self.h264_or_skip()
        import fake_client
        from sources import StaticSource
        raw = h264.image_to_i420(str(ROOT / "assets/testcard.jpg"), 480, 272)
        with self.assertLogs("pspstream", "WARNING") as logs:
            summary, got, _ = self.run_stream(early_kb=0, loss=0, seconds=1.0, codec="h264p",
                                              source=StaticSource(raw, quality=70, raw_i420=True))
        self.assertGreater(summary["frames"], 10)
        self.assertEqual(fake_client.h264_packet_kind(got), 0)
        self.assertTrue(h264.is_idr(got))
        self.assertTrue(any("frames P" in m for m in logs.output), logs.output)

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


class KmsCaptureTest(unittest.TestCase):
    """--source kms. Sem placa de vídeo aqui: o auxiliar de verdade é testado no
    caminho de erro, e o protocolo e os buffers com um auxiliar falso (memfd)."""

    def setUp(self):
        try:
            import kms
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        self.kms = kms
        self.fake = [sys.executable, str(ROOT / "tests/fake_kms_helper.py")]

    def build_helper(self):
        import shutil
        import subprocess
        if not shutil.which("cc") or subprocess.run(["pkg-config", "--exists", "libdrm"]).returncode:
            self.skipTest("sem compilador C ou libdrm")
        r = subprocess.run(["make", "-s", "-C", str(ROOT / "tools/kms")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("warning", r.stderr)
        return ROOT / "tools/kms/pspstream-kms"

    def test_real_helper_reports_errors(self):
        helper = self.build_helper()
        with self.assertRaisesRegex(self.kms.KmsError, "precisa ser /dev/dri/cardN"):
            self.kms.KmsHelper(helper, card="/etc/passwd")
        if not Path("/dev/dri").exists():
            with self.assertRaisesRegex(self.kms.KmsError, "nenhum monitor ligado"):
                self.kms.KmsHelper(helper)

    def test_missing_helper_explains_build(self):
        with self.assertRaisesRegex(self.kms.KmsError, "make -C tools/kms"):
            self.kms.KmsHelper(ROOT / "tools/kms/nao-existe")

    def test_protocol_and_buffer(self):
        from gi.repository import GstAllocators, GstVideo
        helper = self.kms.KmsHelper(self.fake[1], argv_prefix=self.fake[:1])
        try:
            self.assertEqual((helper.hello.width, helper.hello.height), (64, 32))
            reply, fds = helper.next_frame(100)
            self.assertEqual(reply.status, self.kms.ST_FRAME)
            self.assertEqual(len(fds), 2)
            buf = self.kms.make_buffer(reply, fds, GstAllocators.DmaBufAllocator.new())
        finally:
            helper.close()
        self.assertEqual(buf.n_memory(), 1)  # os dois planos estão no mesmo buffer
        meta = GstVideo.buffer_get_video_meta(buf)
        self.assertEqual(meta.n_planes, 2)
        self.assertEqual(list(meta.offset)[:2], [0, 64 * 4 * 32])
        caps = self.kms.caps_for(reply).to_string()
        self.assertIn("memory:DMABuf", caps)
        self.assertIn("drm-format=(string)XR24:0x0100000000000002", caps)
        self.assertEqual(helper.proc.returncode, 0)

    def test_permission_error_at_startup(self):
        import os
        os.environ["FAKE_KMS_NOPERM"] = "1"
        try:
            with self.assertRaisesRegex(self.kms.KmsError, "sem permissão"):
                self.kms.KmsSource(480, 272, 60, 60, helper=self.fake[1], argv_prefix=self.fake[:1])
        finally:
            del os.environ["FAKE_KMS_NOPERM"]

    def test_source_feeds_appsrc(self):
        from gi.repository import Gst
        if not Gst.ElementFactory.find("glupload"):
            self.skipTest("sem os elementos OpenGL do GStreamer")
        src = self.kms.KmsSource(480, 272, 60, 60, codec="jpeg", helper=self.fake[1], argv_prefix=self.fake[:1])
        pushed, caps = [], []

        class FakeAppsrc:
            def set_property(self, name, value):
                caps.append(value.to_string())

            def emit(self, signal, buf):
                pushed.append(buf)
                if len(pushed) >= 5:
                    src._stop.set()

        src.appsrc = FakeAppsrc()  # o upload de memfd para o OpenGL não existe aqui
        t = threading.Thread(target=src._capture)
        t.start()
        t.join(5)
        src.helper.close()
        self.assertFalse(t.is_alive())
        self.assertIsNone(src.failed)
        self.assertEqual(len(pushed), 5)
        self.assertEqual(len(caps), 1)  # o formato não mudou: caps uma vez só
        factories = set()
        src.pipeline.iterate_elements().foreach(lambda e: factories.add(e.get_factory().get_name()))
        self.assertTrue({"appsrc", "glupload", "glcolorscale", "gldownload"} <= factories, factories)
        self.assertNotIn("pipewiresrc", factories)


class NetcheckTest(unittest.TestCase):
    def test_pc_band(self):
        import logging
        import netcheck
        out = "Connected to aa:bb:cc:dd:ee:ff (on wlp0s20f3)\n\tSSID: casa\n\tfreq: 5180.0\n\tsignal: -52 dBm\n"
        self.assertEqual(netcheck.link_freq(out), 5180.0)
        self.assertIsNone(netcheck.link_freq("Not connected."))
        level, msg = netcheck.band_advice(5180.0, "wlp0s20f3")
        self.assertEqual(level, logging.INFO)
        level, msg = netcheck.band_advice(2437, "wlp0s20f3")
        self.assertEqual(level, logging.WARNING)
        self.assertIn("canal 6", msg)
        self.assertIn("802-11-wireless.band a", msg)
        self.assertEqual(netcheck.channel_24(2484), 14)


class H264PEncoderTest(unittest.TestCase):
    def setUp(self):
        try:
            import h264
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        if not h264.available():
            self.skipTest("sem openh264enc")
        self.h264 = h264
        self.raw = h264.image_to_i420(str(ROOT / "assets/testcard.jpg"), 480, 272)

    def aus(self, packet):
        h264 = self.h264
        self.assertTrue(packet.startswith(h264.AUD))
        parts = packet.split(h264.AUD)[1:]
        self.assertEqual(len(parts), 1 + h264.COPIES)
        return parts

    def test_packet_layout_and_idr(self):
        # pelo GStreamer: trocar o QP refaz o encoder (IDR), então a troca espera
        h264 = self.h264
        enc = h264.H264PEncoder(480, 272, 70, backend="gstreamer")
        try:
            first = self.aus(enc.encode(self.raw))
            self.assertTrue(h264.is_idr(first[0]))          # começa com IDR (SPS + PPS + IDR)
            self.assertEqual(h264.nal_types(first[1]), [1])  # cópias: P sem mudança
            self.assertLess(len(first[1]), 100)
            p = self.aus(enc.encode(self.raw))
            self.assertEqual(h264.nal_types(p[0]), [1])
            # pedido de IDR logo depois de um IDR: é o que ainda está a caminho
            self.assertFalse(enc.request_idr())
            enc._last_idr = 0.0
            self.assertTrue(enc.request_idr())
            idr70 = self.aus(enc.encode(self.raw))[0]
            self.assertTrue(h264.is_idr(idr70))
            # qualidade nova = encoder novo = IDR: espera QP_CHANGE_MIN_S...
            enc.set_quality(30)
            self.assertIsNone(enc.applied_quality)
            self.assertEqual(h264.nal_types(self.aus(enc.encode(self.raw))[0]), [1])
            enc._qp_t = 0.0
            idr30 = self.aus(enc.encode(self.raw))[0]
            self.assertTrue(h264.is_idr(idr30))
            self.assertLess(len(idr30), len(idr70) * 0.8)  # QP maior, IDR menor
            self.assertEqual(enc.applied_quality, 30)
            # ... ou vai junto de um IDR que o PSP pediu
            enc.set_quality(70)
            enc._last_idr = 0.0
            self.assertTrue(enc.request_idr())
            again = self.aus(enc.encode(self.raw))[0]
            self.assertTrue(h264.is_idr(again))
            self.assertAlmostEqual(len(again), len(idr70), delta=len(idr70) * 0.05)
        finally:
            enc.close()

    def direct_or_skip(self):
        import openh264
        if not openh264.available():
            self.skipTest("sem libopenh264")
        return openh264

    def test_direct_matches_gstreamer(self):
        # o caminho direto produz o mesmo fluxo que o openh264enc medido no PSP
        self.direct_or_skip()
        h264 = self.h264
        w, h = 480, 272
        y = self.raw[:w * h]
        frames = [y[(k * 2 % h) * w:] + y[:(k * 2 % h) * w] + self.raw[w * h:] for k in range(6)]
        out = {}
        for backend in ("openh264", "gstreamer"):
            enc = h264.H264PEncoder(w, h, 70, backend=backend)
            try:
                out[backend] = [enc.encode(f) for f in frames]
            finally:
                enc.close()
        self.assertEqual(out["openh264"], out["gstreamer"])

    def test_direct_quality_without_idr(self):
        self.direct_or_skip()
        h264 = self.h264
        enc = h264.H264PEncoder(480, 272, 90, backend="openh264")
        try:
            self.assertTrue(enc.live_qp)
            self.assertTrue(h264.is_idr(self.aus(enc.encode(self.raw))[0]))
            w, h = 480, 272
            moved = self.raw[w * 8:w * h] + self.raw[:w * 8] + self.raw[w * h:]
            hi = self.aus(enc.encode(moved))[0]
            enc.set_quality(30)
            lo = self.aus(enc.encode(self.raw))[0]
            self.assertEqual(h264.nal_types(lo), [1])        # qualidade nova, sem IDR
            self.assertEqual(enc.applied_quality, 30)
            self.assertLess(len(lo), len(hi))
        finally:
            enc.close()

    def test_direct_failure_falls_back(self):
        openh264 = self.direct_or_skip()
        h264 = self.h264
        real = openh264.Encoder.encode

        def broken(self_, i420):
            raise openh264.OpenH264Error("layout inesperado (teste)")
        openh264.Encoder.encode = broken
        try:
            enc = h264.H264PEncoder(480, 272, 70)
            with self.assertLogs("pspstream.h264", "WARNING"):
                pkt = enc.encode(self.raw)
            self.assertTrue(h264.is_idr(self.aus(pkt)[0]))
            self.assertEqual(enc.backend, "gstreamer")
            self.assertFalse(enc.live_qp)
            enc.close()
        finally:
            openh264.Encoder.encode = real

    def test_intra_direct(self):
        self.direct_or_skip()
        h264 = self.h264
        enc = h264.H264Encoder(480, 272, 70, backend="openh264")
        try:
            a = enc.encode(self.raw)
            enc.set_quality(40)
            b = enc.encode(self.raw)
            for au in (a, b):
                self.assertEqual(h264.nal_types(au), [7, 8, 5])  # SPS + PPS + IDR em todo frame
            self.assertLess(len(b), len(a))
        finally:
            enc.close()

    def test_periodic_idr(self):
        # sem IDR, frame_num (15 bits) e POC (16 bits) dariam a volta em ~3 min
        h264 = self.h264
        enc = h264.H264PEncoder(480, 272, 70, idr_every=5)
        try:
            kinds = [h264.is_idr(self.aus(enc.encode(self.raw))[0]) for _ in range(12)]
        finally:
            enc.close()
        self.assertEqual([i for i, k in enumerate(kinds) if k], [0, 5, 10])

    def test_no_periodic_idr_by_default(self):
        # a volta dos contadores passou na sonda v4.1 (passo 7): IDR só quando o PSP pede
        h264 = self.h264
        enc = h264.H264PEncoder(480, 272, 70)
        try:
            kinds = [h264.is_idr(self.aus(enc.encode(self.raw))[0]) for _ in range(40)]
            time.sleep(h264.IDR_MIN_INTERVAL_S)  # pedido logo depois de um IDR é ignorado
            self.assertTrue(enc.request_idr())
            kinds.append(h264.is_idr(self.aus(enc.encode(self.raw))[0]))
        finally:
            enc.close()
        self.assertEqual([i for i, k in enumerate(kinds) if k], [0, 40])


class UdpRetryResendTest(unittest.TestCase):
    """Pedido repetido com NACK (frames P): reenvia o frame que se perdeu inteiro."""

    def setUp(self):
        from transports import UdpTransport
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", 0))
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.t = UdpTransport(self.tx, self.rx.getsockname())
        self.flags = []

        class Session:
            def on_request(s, req):
                self.flags.append(req.flags)

        self.t.session = Session()

    def tearDown(self):
        self.rx.close()
        self.tx.close()

    def retry(self, frame_no):
        req = protocol.Request(flags=protocol.REQ_FRAME | protocol.REQ_NACK)
        self.t.feed(req, (frame_no, list(range(protocol.MAX_CHUNKS))))
        return self.flags[-1]

    def test_resends_lost_frame_instead_of_new_one(self):
        self.t.send_frame(7, b"x" * 3000, 0)
        self.assertEqual(self.retry(8), protocol.REQ_FRAME | protocol.REQ_NACK)  # não saiu: pedido normal
        self.assertEqual(self.retry(7), protocol.REQ_NACK)  # ainda pode estar no ar: nem reenvia nem manda outro
        self.assertEqual(self.t.resent_chunks, 0)
        self.t.recent[7] = self.t.recent[7][:3] + (time.monotonic() - 1,)
        self.assertEqual(self.retry(7), protocol.REQ_NACK)
        self.assertEqual(self.t.resent_chunks, 3)  # o frame inteiro de novo, sem frame novo
        self.assertEqual(self.t.retry_resends, 1)

    def test_duplicate_request_is_recognized(self):
        # EBOOT v1.0: o pedido de frame novo leva o número do frame e vai de novo
        # ~6 ms depois. A cópia não pode virar um frame a mais.
        self.assertEqual(self.retry(12), protocol.REQ_FRAME | protocol.REQ_NACK)  # original: frame novo
        self.assertEqual(self.retry(12), protocol.REQ_FRAME | protocol.REQ_NACK)  # cópia antes do envio:
        # a sessão junta os dois pedidos pendentes (um só frame)
        self.t.send_frame(12, b"z" * 100, 0)
        self.assertEqual(self.retry(12), protocol.REQ_NACK)  # cópia logo depois do envio: nada
        self.assertEqual(self.t.resent_chunks, 0)

    def test_redundant_last_chunk(self):
        from transports import UdpTransport
        self.t = UdpTransport(self.tx, self.rx.getsockname(), redundancy_s=0.004)

        class Session:
            alive = True

        sess = Session()
        self.t.session = sess
        self.t.start(sess)
        self.rx.settimeout(1)
        try:
            t0 = time.monotonic()
            self.t.send_frame(9, b"y" * 3000, 0, redundant=True)
            got = []
            for _ in range(4):
                fn, size, _, idx, count, _, _ = protocol.unpack_chunk(self.rx.recv(2048))
                got.append((fn, idx, count, time.monotonic() - t0))
            self.assertEqual([g[:3] for g in got], [(9, 0, 3), (9, 1, 3), (9, 2, 3), (9, 2, 3)])
            self.assertGreaterEqual(got[3][3], 0.004)  # a cópia sai depois, fora da mesma rajada
            self.assertEqual(self.t.redundant_chunks, 1)
            self.t.send_frame(10, b"y" * 3000, 0)  # sem redundant (JPEG, H.264 intra): sem cópia
            for _ in range(3):
                self.rx.recv(2048)
            self.rx.settimeout(0.05)
            with self.assertRaises(socket.timeout):
                self.rx.recv(2048)
        finally:
            sess.alive = False


class HitchStatsTest(unittest.TestCase):
    """Engasgos no log do servidor, com a causa provável."""

    class Transport:
        sent_chunks = resent_chunks = retry_resends = 0

    def test_causes(self):
        t = self.Transport()
        s = stats.SessionStats(60, transport=t)
        ms = 1000
        s.on_send(1, ms, 0, 1500, 5)
        s.on_send(2, ms + 17, 0, 1500, 5)          # normal
        t.resent_chunks += 1
        s.on_send(3, ms + 80, 0, 1500, 5)          # reenvio no intervalo
        s.on_send(4, ms + 140, 0, 9000, 2, idr=True)
        s.on_send(5, ms + 200, 0, 1500, 50)        # esperou o PC ter frame novo
        s.on_send(6, ms + 260, 0, 1500, 3)         # o pedido demorou a chegar
        s.on_send(7, ms + 1300, 0, 1500, 1000, resend=True)  # tela parada: não conta
        s.on_send(8, ms + 1400, 0, 1500, 3)
        summary = s.window.summary()
        self.assertEqual(summary["hitches"], 4)
        self.assertEqual(summary["hitch_max_ms"], 63)
        self.assertEqual(summary["hitch_causes"], {"perda": 1, "IDR": 1, "pedido atrasado": 1, "captura": 1})
        self.assertEqual(summary["idrs"], 1)
        line = stats.format_summary(summary)
        self.assertIn("engasgos 4 (pior 63 ms: 1 perda, 1 IDR, 1 pedido atrasado, 1 captura)", line)
        self.assertIn("1 IDR", line)


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


class GamepadTest(unittest.TestCase):
    """Controle de Xbox 360 virtual (server/gamepad.py), sem uinput: só os eventos."""

    def make(self, profile="xbox", timeout=0.5):
        from gamepad import GamepadInjector
        return GamepadInjector(load_profile(str(ROOT / "server/keymap.json"), profile), dry_run=True,
                               timeout=timeout)

    def state(self, pad):
        return {code: v for (kind, code), v in pad.state.items() if v}

    def test_sdl_layout(self):
        # O SDL numera botões e eixos pela ordem dos códigos do kernel e usa o
        # mapeamento do 045e:028e: a:b0 b:b1 x:b2 y:b3 leftshoulder:b4
        # rightshoulder:b5 back:b6 start:b7 guide:b8 leftstick:b9 rightstick:b10,
        # leftx:a0 lefty:a1 lefttrigger:a2 rightx:a3 righty:a4 righttrigger:a5.
        try:
            from evdev import ecodes
        except ImportError:
            self.skipTest("sem python-evdev")
        import gamepad
        by_code = sorted(gamepad.BUTTON_CODES.items(), key=lambda kv: getattr(ecodes, kv[1]))
        self.assertEqual([k for k, _ in by_code], ["A", "B", "X", "Y", "LB", "RB", "BACK", "START", "GUIDE",
                                                   "L3", "R3"])
        axes = sorted(["ABS_X", "ABS_Y", "ABS_Z", "ABS_RX", "ABS_RY", "ABS_RZ"], key=lambda c: getattr(ecodes, c))
        self.assertEqual(axes, ["ABS_X", "ABS_Y", "ABS_Z", "ABS_RX", "ABS_RY", "ABS_RZ"])
        self.assertEqual((gamepad.VENDOR, gamepad.PRODUCT), (0x045E, 0x028E))

    def test_buttons_triggers_dpad(self):
        pad = self.make()
        pad.update(PSP_BUTTONS["CROSS"] | PSP_BUTTONS["R"] | PSP_BUTTONS["UP"] | PSP_BUTTONS["LEFT"], 128, 128)
        self.assertEqual(self.state(pad), {"BTN_A": 1, "ABS_RZ": 255, "ABS_HAT0Y": -1, "ABS_HAT0X": -1})
        pad.update(PSP_BUTTONS["TRIANGLE"], 128, 128)
        self.assertEqual(self.state(pad), {"BTN_Y": 1})
        pad.update(0, 128, 128)
        self.assertEqual(self.state(pad), {})
        pad.close()

    def test_analog_deadzone_and_full_range(self):
        pad = self.make()
        pad.update(0, 140, 118)  # ruído perto do centro (o analógico do PSP não para em 128)
        self.assertEqual(self.state(pad), {})
        pad.update(0, 245, 128)  # ~92% já é o fim do curso
        self.assertEqual(pad.state[("abs", "ABS_X")], 32767)
        pad.update(0, 128, 0)    # para cima = negativo, como no xpad
        self.assertEqual(pad.state[("abs", "ABS_Y")], -32767)
        pad.update(0, 220, 220)  # diagonal: o vetor é limitado ao círculo
        x, y = pad.state[("abs", "ABS_X")], pad.state[("abs", "ABS_Y")]
        self.assertAlmostEqual(x, y, delta=2)
        self.assertLessEqual(math.hypot(x, y), 32767 * 1.001)
        pad.close()

    def test_shift_layer_and_tap(self):
        pad = self.make()
        sel, l_btn, up = PSP_BUTTONS["SELECT"], PSP_BUTTONS["L"], PSP_BUTTONS["UP"]
        pad.update(sel, 128, 128)
        pad.update(sel | l_btn | up, 128, 128)  # SELECT + L = LB, SELECT + cima = analógico direito
        self.assertEqual(self.state(pad), {"BTN_TL": 1, "ABS_RY": -32767})
        pad.update(l_btn | up, 128, 128)        # soltou o SELECT antes: continuam LB e RS
        self.assertEqual(self.state(pad), {"BTN_TL": 1, "ABS_RY": -32767})
        pad.update(0, 128, 128)
        self.assertEqual(self.state(pad), {})
        self.assertNotIn(("BTN_SELECT", 1), pad.out.events)  # usado como shift: sem BACK
        # toque rápido no SELECT sozinho = BACK, solto logo depois
        pad.update(sel, 128, 128)
        pad.update(0, 128, 128)
        self.assertEqual(self.state(pad), {"BTN_SELECT": 1})
        time.sleep(0.15)
        self.assertEqual(self.state(pad), {})
        # segurado e solto sem nada no meio, mas devagar: não é toque
        pad.update(sel, 128, 128)
        pad.shift_t -= 1.0
        pad.update(0, 128, 128)
        self.assertEqual(self.state(pad), {})
        pad.close()

    def test_camera_profile(self):
        pad = self.make("xbox-camera")
        pad.update(PSP_BUTTONS["CIRCLE"] | PSP_BUTTONS["DOWN"], 255, 128)
        st = self.state(pad)
        self.assertEqual(st["BTN_A"], 1)                         # direcional para baixo = A
        self.assertEqual(st["ABS_RX"], round(0.8 * 32767))       # bola = câmera para a direita
        self.assertEqual(st["ABS_X"], 32767)                     # analógico = andar
        pad.close()

    def test_release_all_and_timeout(self):
        pad = self.make(timeout=0.05)
        pad.update(PSP_BUTTONS["CROSS"] | PSP_BUTTONS["L"], 255, 128)
        self.assertTrue(self.state(pad))
        time.sleep(0.25)  # o PSP sumiu: tudo volta ao neutro
        self.assertEqual(self.state(pad), {})
        pad.update(PSP_BUTTONS["CROSS"], 128, 128)
        pad.release_all()
        self.assertEqual(self.state(pad), {})
        pad.close()

    def test_bad_target(self):
        from gamepad import GamepadInjector
        with self.assertRaises(SystemExit):
            GamepadInjector({"type": "gamepad", "buttons": {"CROSS": "BOTAO_X"}}, dry_run=True)


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
