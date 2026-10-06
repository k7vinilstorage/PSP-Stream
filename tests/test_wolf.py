"""Fonte do Wolf (Games on Whales) contra um Wolf falso (tests/fake_wolf.py):
a API num socket Unix, o ping e o pipeline da sessão com videotestsrc no
lugar do interpipesrc. Nada aqui roda contra um Wolf de verdade.

  python3 -m unittest discover tests
"""
import copy
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))

import settings  # noqa: E402
import wolf_api  # noqa: E402
from wolf_api import WolfApi, WolfApiError  # noqa: E402

try:
    import fake_wolf
    import wolf_input
    import wolf_source
    from wolf_source import Target, TargetError, WolfSource, resolve_target
except (ImportError, ValueError):  # sem GStreamer
    fake_wolf = wolf_source = wolf_input = None

LOBBY = "8f0b2c6e-0d6a-4c1e-9a52-3f2f5d7a1b10"
LOBBY2 = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
FRAME_I420 = 480 * 272 * 3 // 2


def lobby(id_=LOBBY, name="Steam", **kw):
    return {"id": id_, "name": name, "multi_user": True, "pin_required": False, "connected_sessions": [], **kw}


@unittest.skipIf(fake_wolf is None, "sem GStreamer")
class WolfCase(unittest.TestCase):
    lobbies = (lobby(),)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wolf = fake_wolf.FakeWolf(self.tmp.name, copy.deepcopy(list(self.lobbies)))
        self.addCleanup(self.wolf.close)
        self.api = WolfApi(self.wolf.socket_path, timeout=2)

    def source(self, target="", convert="cpu", codec="h264p", first_frame_s=5.0, **kw):
        src = WolfSource(self.api, target, convert, 480, 272, 30, 60, codec=codec,
                         video_ping_port=self.wolf.ports["video"], audio_ping_port=self.wolf.ports["audio"],
                         poll_s=0.2, first_frame_s=first_frame_s, **kw)
        self.addCleanup(src.stop)
        return src

    def video_started(self):
        with self.wolf.lock:
            return [s for s in self.wolf.started if s[0] == "video"]


class WolfApiTest(WolfCase):
    def test_reads(self):
        self.assertEqual([lb["id"] for lb in self.api.lobbies()], [LOBBY])
        self.assertEqual(self.api.sessions(), [])

    def test_sessions(self):
        sid = self.api.add_session(wolf_source.session_request(480, 272))
        self.assertEqual(sid, fake_wolf.DUMMY_ID)
        self.assertEqual([s["client_id"] for s in self.api.sessions()], [sid])
        with self.assertRaises(WolfApiError) as cm:
            self.api.start_session(sid, {"gst_pipeline": "x"}, {})
        self.assertIn("display_mode", str(cm.exception))
        self.api.send_input(sid, b"\x06\x02\x00\x01")
        self.assertEqual(self.wolf.inputs, [b"\x06\x02\x00\x01"])
        self.assertEqual(self.wolf.bodies("/sessions/input")[0]["input_packet_hex"], "06020001")
        self.api.stop_session(sid)
        with self.assertRaises(WolfApiError) as cm:
            self.api.stop_session(sid)
        self.assertIn("Invalid session_id", str(cm.exception))

    def test_lobby_join_and_pin(self):
        with self.wolf.lock:
            self.wolf.lobbies[0]["_pin"] = [1, 2, 3, 4]
        sid = self.api.add_session(wolf_source.session_request(480, 272))
        with self.assertRaises(WolfApiError) as cm:
            self.api.join_lobby(LOBBY, sid)
        self.assertIn("Invalid PIN", str(cm.exception))
        self.api.join_lobby(LOBBY, sid, pin="1234")
        self.assertEqual(self.wolf.bodies("/lobbies/join")[-1],
                         {"lobby_id": LOBBY, "moonlight_session_id": sid, "pin": [1, 2, 3, 4]})
        self.assertEqual(self.api.lobbies()[0]["connected_sessions"], [sid])
        self.api.leave_lobby(LOBBY, sid)
        self.assertEqual(self.api.lobbies()[0]["connected_sessions"], [])

    def test_error_reply(self):
        self.wolf.fail = (500, "algo deu errado")
        with self.assertRaises(WolfApiError) as cm:
            self.api.lobbies()
        self.assertIn("algo deu errado", str(cm.exception))
        self.assertIn("HTTP 500", str(cm.exception))


class WolfApiErrorsTest(unittest.TestCase):
    """Erros claros sem o Wolf: socket ausente, ninguém escutando, resposta estranha, sem resposta."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "wolf.sock")

    def serve_once(self, reply=None, delay=None):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        srv.listen(1)
        self.addCleanup(srv.close)

        def run():
            conn, _ = srv.accept()
            with conn:
                conn.recv(65536)
                if delay:
                    time.sleep(delay)
                if reply is not None:
                    conn.sendall(reply)
        threading.Thread(target=run, daemon=True).start()

    def test_missing_socket(self):
        with self.assertRaises(WolfApiError) as cm:
            WolfApi(self.path).lobbies()
        self.assertIn(self.path, str(cm.exception))
        self.assertIn("--wolf-socket", str(cm.exception))

    def test_nobody_listening(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)  # o arquivo existe, sem listen
        self.addCleanup(sock.close)
        with self.assertRaises(WolfApiError) as cm:
            WolfApi(self.path).lobbies()
        self.assertIn("o Wolf está rodando", str(cm.exception))

    def test_not_json(self):
        body = b"Chunked encoding not supported, use HTTP/1.0 instead"
        self.serve_once(b"HTTP/1.0 500 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        with self.assertRaises(WolfApiError) as cm:
            WolfApi(self.path).lobbies()
        self.assertIn("não é JSON", str(cm.exception))
        self.assertIn("Chunked", str(cm.exception))

    def test_timeout(self):
        self.serve_once(delay=1.5)
        with self.assertRaises(WolfApiError) as cm:
            WolfApi(self.path, timeout=0.3).lobbies()
        self.assertIn("não respondeu", str(cm.exception))

    @unittest.skipIf(os.geteuid() == 0, "root abre o socket mesmo sem permissão")
    def test_permission(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        sock.listen(1)
        self.addCleanup(sock.close)
        os.chmod(self.path, 0o755)  # como o Wolf cria, com umask 022, mas de outro dono: aqui tira a escrita
        os.chmod(self.path, 0o555)
        with self.assertRaises(WolfApiError) as cm:
            WolfApi(self.path).lobbies()
        self.assertIn("sem permissão", str(cm.exception))

    def test_env_defaults(self):
        with mock.patch.dict(os.environ, {"WOLF_SOCKET_PATH": "/run/x/wolf.sock", "WOLF_VIDEO_PING_PORT": "49100",
                                          "WOLF_AUDIO_PING_PORT": "lixo"}):
            self.assertEqual(wolf_api.default_socket(), "/run/x/wolf.sock")
            self.assertEqual(wolf_api.env_port("WOLF_VIDEO_PING_PORT", 48100), 49100)
            self.assertEqual(wolf_api.env_port("WOLF_AUDIO_PING_PORT", 48200), 48200)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(wolf_api.default_socket(), "/var/run/wolf/wolf.sock")


@unittest.skipIf(wolf_source is None, "sem GStreamer")
class PipelineTextTest(unittest.TestCase):
    def test_escape_and_ids(self):
        convert = 'capsfilter caps="video/x-raw(memory:DMABuf), drm-format={NV12}" ! vapostproc'
        text = wolf_source.video_pipeline(LOBBY, "123", convert, 480, 272, 4567)
        self.assertIn("{{NV12}}", text)
        plain = fake_wolf.fmt_unescape(text)  # o que o fmt::format do Wolf devolve
        self.assertIn("drm-format={NV12}", plain)
        self.assertTrue(plain.startswith(f"interpipesrc name=pspstream_123_video listen-to={LOBBY}_video "))
        self.assertIn("leaky-type=downstream", plain)
        self.assertTrue(plain.endswith("! gdppay ! tcpclientsink host=127.0.0.1 port=4567 sync=false"))
        for bad in ("x ! filesink location=/etc/passwd", "", "a" * 65):
            with self.assertRaises(ValueError):
                wolf_source.video_pipeline(bad, "123", "videoconvertscale", 480, 272, 1)
        with self.assertRaises(ValueError):
            fake_wolf.fmt_unescape("listen-to={session_id}_video")  # sem escape: erro no Wolf

    def test_converts(self):
        cpu = wolf_source.convert_chain("cpu", 480, 272, "lanczos", True)
        self.assertEqual(cpu, "videoconvertscale method=lanczos add-borders=true")
        self.assertIn("add-borders=false", wolf_source.convert_chain("cpu", 480, 272, "bilinear", False))
        nvidia = wolf_source.convert_chain("nvidia", 480, 272)
        self.assertTrue(nvidia.startswith("cudaupload ! cudaconvertscale"))
        self.assertIn("format=I420,width=480,height=272", nvidia)
        self.assertTrue(nvidia.endswith("cudadownload"))
        self.assertEqual(wolf_source.convert_chain("va", 480, 272), "vapostproc add-borders=true")
        self.assertEqual(wolf_source.convert_chain("  glupload ! gldownload ", 480, 272), "glupload ! gldownload")
        self.assertEqual(wolf_source.AUTO_ORDER, ("nvidia", "va", "cpu"))

    def test_audio_pipeline(self):
        text = wolf_source.audio_pipeline(LOBBY, "123", 44100, 2, 4568)
        plain = fake_wolf.fmt_unescape(text)
        self.assertTrue(plain.startswith(f"interpipesrc name=pspstream_123_audio listen-to={LOBBY}_audio "))
        self.assertIn("format=S16LE,layout=interleaved,rate=44100,channels=2", plain)
        self.assertTrue(plain.endswith("! gdppay ! tcpclientsink host=127.0.0.1 port=4568 sync=false"))
        with self.assertRaises(ValueError):
            wolf_source.audio_pipeline("x y", "123", 44100, 2, 1)

    def test_ping(self):
        secret = wolf_source.new_secret()
        self.assertEqual(len(secret), 16)
        self.assertTrue(all(33 <= b <= 126 for b in secret))
        pkt = wolf_source.ping_packet(secret, 258)
        self.assertEqual(len(pkt), 20)  # sizeof(SS_PING): o Wolf lê o segredo de pacotes com 20 bytes ou mais
        self.assertEqual(pkt[:16], secret)
        self.assertEqual(pkt[16:], b"\x00\x00\x01\x02")

    def test_session_fields(self):
        req = wolf_source.session_request(480, 272)
        self.assertEqual(set(req), set(fake_wolf.SESSION_FIELDS))
        self.assertEqual((req["client_ip"], req["rtsp_fake_ip"]), ("127.0.0.1", "pspstream"))
        secret = wolf_source.new_secret()
        video = wolf_source.video_session("1", "p", 480, 272, 60, 48100, secret)
        self.assertEqual(set(video), set(fake_wolf.VIDEO_FIELDS))
        self.assertEqual(video["rtp_secret_payload"], list(secret))
        self.assertGreater(video["packet_size"], 0)  # o Wolf divide por ele
        self.assertEqual(video["display_mode"], {"width": 480, "height": 272, "refreshRate": 60})
        audio = wolf_source.audio_session("1", "p", 48200, secret, "k", "1")
        self.assertEqual(set(audio), set(fake_wolf.AUDIO_FIELDS))
        self.assertEqual(audio["audio_mode"]["speakers"], ["FRONT_LEFT", "FRONT_RIGHT"])


@unittest.skipIf(wolf_source is None, "sem GStreamer")
class ResolveTargetTest(unittest.TestCase):
    def test_auto(self):
        self.assertIsNone(resolve_target("", [], [])[0])
        self.assertIn("nenhum lobby", resolve_target("", [], [])[1])
        target, _ = resolve_target("", [lobby()], [])
        self.assertEqual(target, Target("lobby", LOBBY, "Steam", LOBBY))
        with self.assertRaises(TargetError) as cm:
            resolve_target("", [lobby(), lobby(LOBBY2, "Retroarch")], [])
        self.assertIn(LOBBY, str(cm.exception))
        self.assertIn("Retroarch", str(cm.exception))

    def test_by_id_name_and_session(self):
        lobbies = [lobby(), lobby(LOBBY2, "Retroarch", connected_sessions=["555"])]
        sessions = [{"client_id": "555", "client_ip": "192.168.0.20"},
                    {"client_id": "777", "client_ip": "192.168.0.30"},
                    {"client_id": "999", "client_ip": "127.0.0.1", "rtsp_fake_ip": "pspstream"}]
        self.assertEqual(resolve_target(LOBBY2, lobbies, sessions)[0].producer, LOBBY2)
        self.assertEqual(resolve_target("retroarch", lobbies, sessions)[0].id, LOBBY2)
        # uma sessão Moonlight num lobby vê o lobby; fora dele, o próprio compositor
        self.assertEqual(resolve_target("555", lobbies, sessions)[0].producer, LOBBY2)
        self.assertEqual(resolve_target("777", lobbies, sessions)[0], Target("sessão", "777", "192.168.0.30", "777"))
        # a nossa sessão e as que sobraram de outro PSPStream não são alvo
        self.assertIsNone(resolve_target("777", lobbies, sessions, own="777")[0])
        target, why = resolve_target("999", lobbies, sessions)
        self.assertIsNone(target)
        self.assertIn("lobby " + LOBBY, why)
        self.assertIn("sessão 555", why)
        self.assertNotIn("sessão 999", why)
        twins = [lobby(), lobby(LOBBY2, "Steam")]
        self.assertIn("use o id", resolve_target("steam", twins, [])[1])


class WolfSourceTest(WolfCase):
    def test_streams_a_lobby(self):
        src = self.source()
        src.start()
        got = src.wait_newer(0, 10)
        self.assertIsNotNone(got, self.wolf.started)
        self.assertEqual(len(got[1]), FRAME_I420)  # I420 cru para o h264p
        self.assertTrue(src.raw_i420)
        (_, sid, text, ok, err), = self.video_started()
        self.assertTrue(ok, err)
        self.assertIn(f"listen-to={LOBBY}_video", text)
        self.assertIn("videoconvertscale method=bilinear2 add-borders=true", text)
        self.assertEqual(self.wolf.paths("POST")[:2], ["/sessions/add", "/sessions/start"])
        self.assertIsNone(src.failed)
        n = src.latest()[0]
        self.assertIsNotNone(src.wait_newer(n, 5))  # continua chegando
        src.stop()
        self.assertEqual(self.wolf.paths("POST")[-1], "/sessions/stop")
        self.assertEqual(self.wolf.sessions, [])

    def test_jpeg(self):
        src = self.source(codec="jpeg")
        src.start()
        got = src.wait_newer(0, 10)
        self.assertIsNotNone(got)
        self.assertEqual(got[1][:2], b"\xff\xd8")
        src.set_quality(30)
        self.assertEqual(src.quality, 30)

    def test_auto_convert(self):
        """Sem CUDA nem VA aqui: o Wolf falso não monta nvidia nem va, e o auto chega no cpu."""
        src = self.source(convert="auto", first_frame_s=1.5)
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 20), self.wolf.started)
        self.assertTrue(self.wolf.wait_for(lambda: src.working_convert == "cpu"))  # gravada logo depois do frame
        kinds = [("cudaupload" in t, "vapostproc" in t, ok) for _, _, t, ok, _ in self.video_started()]
        self.assertEqual(kinds, [(True, False, False), (False, True, False), (False, False, True)])
        posts = self.wolf.paths("POST")
        self.assertEqual(posts.count("/sessions/add"), 3)
        self.assertEqual(posts.count("/sessions/stop"), 2)  # cada tentativa encerra a sua

    def test_lobby_closes_and_comes_back(self):
        src = self.source()
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        with self.wolf.lock:
            saved = self.wolf.lobbies.pop()
        self.assertTrue(self.wolf.wait_for(lambda: not self.wolf.sessions))
        self.assertIsNone(src.failed)  # o servidor não cai
        time.sleep(0.5)
        self.assertEqual(self.wolf.paths("POST").count("/sessions/add"), 1)  # esperando, sem sessão nova
        with self.wolf.lock:
            self.wolf.lobbies.append(saved)
        self.assertTrue(self.wolf.wait_for(lambda: self.wolf.paths("POST").count("/sessions/add") == 2))
        n = src.latest()[0]
        self.assertIsNotNone(src.wait_newer(n, 10))

    def test_wolf_pipeline_dies(self):
        src = self.source()
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        with self.wolf.lock:
            pipes = [p for ps in self.wolf.pipelines.values() for p in ps]
        with self.assertLogs("pspstream.wolf", "WARNING") as logs:
            for pipe in pipes:  # o pipeline do Wolf morreu: a conexão fecha
                pipe.set_state(fake_wolf.Gst.State.NULL)
            self.assertTrue(self.wolf.wait_for(lambda: self.wolf.paths("POST").count("/sessions/add") == 2, 15))
        self.assertIn("parou de chegar", "\n".join(logs.output))
        n = src.latest()[0]
        self.assertIsNotNone(src.wait_newer(n, 10))
        self.assertIsNone(src.failed)

    def test_leftover_session_is_stopped(self):
        with self.wolf.lock:
            self.wolf.sessions.append({"client_id": fake_wolf.DUMMY_ID, "client_ip": "127.0.0.1",
                                       "rtsp_fake_ip": "pspstream"})
        src = self.source()
        with self.assertLogs("pspstream.wolf", "WARNING") as logs:
            src.start()
            self.assertIsNotNone(src.wait_newer(0, 10))
        self.assertIn("sobrou", "\n".join(logs.output))
        posts = self.wolf.paths("POST")
        self.assertLess(posts.index("/sessions/stop"), posts.index("/sessions/add"))

    def test_session_target_follows_its_lobby(self):
        with self.wolf.lock:
            self.wolf.sessions.append({"client_id": "555", "client_ip": "192.168.0.20", "rtsp_fake_ip": "1.2.3.4"})
        src = self.source(target="555")
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        self.assertIn("listen-to=555_video", self.video_started()[0][2])
        with self.wolf.lock:  # o cliente Moonlight entrou no lobby
            self.wolf.lobbies[0]["connected_sessions"] = ["555"]
        self.assertTrue(self.wolf.wait_for(lambda: len(self.video_started()) == 2 and self.video_started()[1][3]))
        self.assertIn(f"listen-to={LOBBY}_video", self.video_started()[1][2])

    def test_waits_for_the_wolf(self):
        self.wolf.close()
        os.unlink(self.wolf.socket_path)
        src = self.source()
        with self.assertLogs("pspstream.wolf", "WARNING") as logs:
            src.start()  # não falha: espera o Wolf subir
        self.assertIn("não existe", "\n".join(logs.output))
        self.assertIsNone(src.failed)
        self.wolf = fake_wolf.FakeWolf(self.tmp.name, [lobby()])
        self.addCleanup(self.wolf.close)
        # as portas de ping do Wolf novo
        src.video_ping_port, src.audio_ping_port = self.wolf.ports["video"], self.wolf.ports["audio"]
        self.assertIsNotNone(src.wait_newer(0, 15))
        src.stop()  # antes de o Wolf falso fechar
        self.assertEqual(self.wolf.paths("POST")[-1], "/sessions/stop")

    def test_waits_for_a_lobby_and_stops_quickly(self):
        with self.wolf.lock:
            self.wolf.lobbies.clear()
        src = self.source()
        src.start()
        time.sleep(0.5)
        t = time.monotonic()
        src.stop()
        self.assertLess(time.monotonic() - t, 2)
        self.assertNotIn("/sessions/add", self.wolf.paths("POST"))

    def test_one_session_at_a_time(self):
        """A interface web sobe a captura nova antes de parar a velha: no Wolf, as duas teriam o mesmo id."""
        old = self.source()
        old.start()
        self.assertIsNotNone(old.wait_newer(0, 10))
        new = self.source()
        new.start()
        time.sleep(1)
        self.assertEqual(self.wolf.paths("POST").count("/sessions/add"), 1)
        old.stop()
        self.assertIsNotNone(new.wait_newer(0, 10))
        self.assertEqual(len(self.wolf.sessions), 1)


class WolfStatusTest(WolfCase):
    def test_status_line(self):
        with self.wolf.lock:
            self.wolf.lobbies.clear()
        src = self.source()
        src.start()
        self.assertTrue(self.wolf.wait_for(lambda: "nenhum lobby" in src.status()["text"]))
        self.assertTrue(src.status()["warn"])
        with self.wolf.lock:
            self.wolf.lobbies.append(lobby())
        self.assertIsNotNone(src.wait_newer(0, 10))
        st = src.status()
        self.assertIn("espelhando lobby Steam", st["text"])
        self.assertIn("conversão cpu", st["text"])
        self.assertFalse(st["warn"])


class WebSuggestionsTest(WolfCase):
    def test_lobby_names(self):
        import control
        import pspstream
        args = pspstream.build_parser().parse_args(["--wolf-socket", self.wolf.socket_path])
        args.codec_choice = args.codec  # o main() faz isso
        ctl = control.Controller(args, None, mock.Mock(), None)
        item = next(i for i in ctl.config()["settings"] if i["key"] == "wolf_target")
        self.assertEqual(item["suggestions"], ["Steam"])
        self.assertEqual(control.wolf_lobbies(self.tmp.name + "/nada.sock"), [])


class WolfAudioTest(WolfCase):
    """Fase 2: o som do alvo pelo pipeline de som da sessão (o Wolf falso toca um seno de 440 Hz)."""

    def collect(self, hub):
        got = []
        hub.add_listener(lambda *a: got.append(a))
        return got

    def test_sine_reaches_the_listener(self):
        import audio
        src = self.source(audio=(44100, 2))
        src.start()
        hub = wolf_source.WolfAudio(src, 44100, 2)
        hub.start()
        got = self.collect(hub)
        self.assertTrue(self.wolf.wait_for(lambda: len(got) >= 25), self.wolf.started)
        audio_runs = [x for x in self.wolf.started if x[0] == "audio"]
        self.assertTrue(audio_runs[0][3], audio_runs)
        self.assertIn(f"listen-to={LOBBY}_audio", audio_runs[0][2])
        seqs = [g[0] for g in got[:25]]
        self.assertEqual(seqs, list(range(1, 26)))
        seq, pos, rate, channels, samples, block = got[-1]
        self.assertEqual((rate, channels, len(block)), (44100, 2, hub.align))
        pcm = audio.ima_decode_block(block, 2)
        self.assertGreater(max(abs(x) for x in pcm), 3000)  # o seno, não silêncio
        self.assertEqual(self.wolf.paths("POST").count("/sessions/add"), 1)  # a sessão já nasceu com som

    def test_numbering_survives_a_new_session(self):
        src = self.source(audio=(44100, 2))
        src.start()
        hub = wolf_source.WolfAudio(src, 44100, 2)
        hub.start()
        got = self.collect(hub)
        self.assertTrue(self.wolf.wait_for(lambda: len(got) >= 5))
        with self.wolf.lock:
            saved = self.wolf.lobbies.pop()
        self.assertTrue(self.wolf.wait_for(lambda: not self.wolf.sessions))
        n = len(got)
        with self.wolf.lock:
            self.wolf.lobbies.append(saved)
        self.assertTrue(self.wolf.wait_for(lambda: len(got) >= n + 5))
        self.assertEqual([g[0] for g in got], list(range(1, len(got) + 1)))  # sem recomeçar do 1
        positions = [g[1] for g in got]
        self.assertEqual(positions, sorted(positions))

    def test_rate_change_redoes_the_session(self):
        src = self.source(audio=(44100, 2))
        src.start()
        hub = wolf_source.WolfAudio(src, 44100, 2)
        hub.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        mono = wolf_source.WolfAudio(src, 22050, 1)  # a interface web: 22050 Hz, mono
        got = self.collect(mono)
        mono.start()
        hub.stop()
        self.assertTrue(self.wolf.wait_for(lambda: len(got) >= 5, 15))
        self.assertEqual(self.wolf.paths("POST").count("/sessions/add"), 2)
        self.assertEqual({(g[2], g[3], len(g[5])) for g in got}, {(22050, 1, mono.align)})
        self.assertIn("rate=22050,channels=1", [x for x in self.wolf.started if x[0] == "audio"][-1][2])

    def test_without_audio(self):
        src = self.source()
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        audio_runs = [x for x in self.wolf.started if x[0] == "audio"]
        self.assertEqual(audio_runs[0][2], wolf_source.NO_AUDIO_PIPELINE)  # o ping de som foi, o pipeline acaba
        # ligar o som depois refaz a sessão com o pipeline de som
        hub = wolf_source.WolfAudio(src, 44100, 2)
        got = self.collect(hub)
        hub.start()
        self.assertTrue(self.wolf.wait_for(lambda: len(got) >= 3, 15))

    def test_open_audio(self):
        import capture
        import pspstream
        args = pspstream.build_parser().parse_args(["--source", "wolf", "--wolf-socket", self.wolf.socket_path,
                                                    "--audio-mono", "--codec", "jpeg"])
        self.assertTrue(capture.wolf_audio(args))
        with self.assertRaises(RuntimeError):  # sem a captura do Wolf rodando
            capture.open_audio(args)
        src = capture.build_source(args)
        self.assertEqual(src.audio_config, (44100, 1))
        src.ping_host = "127.0.0.1"
        src.video_ping_port, src.audio_ping_port = self.wolf.ports["video"], self.wolf.ports["audio"]
        src.poll_s = 0.2
        self.addCleanup(src.stop)
        src.start()
        cap = capture.open_audio(args, seq0=41)
        self.assertIsInstance(cap, wolf_source.WolfAudio)
        got = self.collect(cap)
        self.assertTrue(self.wolf.wait_for(lambda: got))
        self.assertEqual(got[0][0], 42)  # continua a numeração de antes
        cap.stop()
        # outra fonte de som com a fonte wolf: a sessão sem som do Wolf
        other = pspstream.build_parser().parse_args(["--source", "wolf", "--audio-device", "test"])
        self.assertFalse(capture.wolf_audio(other))
        with self.assertRaises(RuntimeError):
            capture.open_audio(pspstream.build_parser().parse_args(["--source", "static", "--audio-device", "wolf"]))


# Exemplo de CONTROLLER_MULTI nos testes do próprio Wolf (tests/testWolfAPI.cpp): A apertado, controle 0
WOLF_EXAMPLE = "060222000000001E0C0000001A000000010014000010000000000000000000009C0000005500"


def parse_input(pkt: bytes) -> dict:
    """Lê como o Wolf: INPUT_PKT (packet_type, packet_len, data_size, type) e a struct do tipo."""
    ptype, plen, _, kind = struct.unpack_from("<HHII", pkt)
    assert ptype == 0x0206 and plen == len(pkt) - 4, pkt.hex()
    assert struct.unpack_from(">I", pkt, 4)[0] == len(pkt) - 8, pkt.hex()  # data_size em big endian
    if kind == 0x0C:
        names = ("header_b", "number", "mask", "mid_b", "buttons", "lt", "rt", "lsx", "lsy", "rsx", "rsy",
                 "tail_a", "buttons2", "tail_b")
        assert len(pkt) == 12 + 26
        return {"kind": "multi", **dict(zip(names, struct.unpack_from("<hhhhHBBhhhhhHh", pkt, 12)))}
    if kind == 0x55000004:
        # struct do Wolf: controller_number, controller_type, capabilities (1 byte), support_button_flags
        number, ctype, caps = struct.unpack_from("<BBB", pkt, 12)
        return {"kind": "arrival", "number": number, "type": ctype, "caps": caps,
                "moonlight": struct.unpack_from("<BBHI", pkt, 12)}
    raise AssertionError(f"tipo {kind:#x}")


@unittest.skipIf(wolf_input is None, "sem GStreamer")
class InputPacketTest(unittest.TestCase):
    def test_multi_matches_the_wolf_example(self):
        pkt = wolf_input.multi_packet(0, 1, 0x1000, 0, 0, 0, 0, 0, 0)
        self.assertEqual(pkt.hex().upper(), WOLF_EXAMPLE)
        self.assertEqual(parse_input(pkt)["buttons"], 0x1000)

    def test_arrival(self):
        pkt = wolf_input.arrival_packet(0)
        self.assertEqual(len(pkt), 20)  # SS_CONTROLLER_ARRIVAL_PACKET do moonlight-common-c + 0x0206 e tamanho
        got = parse_input(pkt)
        self.assertEqual((got["number"], got["type"], got["caps"]), (0, 1, 0x01))  # Xbox, gatilhos analógicos
        self.assertEqual(got["moonlight"], (0, 1, 0x01, wolf_input.SUPPORTED))
        self.assertEqual(wolf_input.SUPPORTED, 0xF7FF)

    def test_state(self):
        import gamepad
        state = gamepad.GamepadInjector._neutral()
        state.update({("key", "BTN_A"): 1, ("key", "BTN_TR"): 1, ("key", "BTN_MODE"): 1, ("abs", "ABS_HAT0Y"): -1,
                      ("abs", "ABS_HAT0X"): 1, ("abs", "ABS_Z"): 255, ("abs", "ABS_X"): 32767,
                      ("abs", "ABS_Y"): -32767, ("abs", "ABS_RY"): 16000})
        got = parse_input(wolf_input.state_packet(state))
        self.assertEqual(got["buttons"], 0x1000 | 0x0200 | 0x0400 | 0x0001 | 0x0008)
        self.assertEqual((got["lt"], got["rt"], got["lsx"]), (255, 0, 32767))
        self.assertEqual((got["lsy"], got["rsy"]), (32767, -16000))  # para cima é positivo (o inputtino inverte)
        self.assertEqual((got["mask"], got["header_b"], got["mid_b"], got["tail_a"], got["tail_b"]),
                         (1, 0x1A, 0x14, 0x9C, 0x55))
        self.assertEqual(parse_input(wolf_input.state_packet(state, connected=False))["mask"], 0)


class WolfInputTest(WolfCase):
    """Fase 3: os botões do PSP viram o controle virtual da sessão, que entra no lobby."""
    CROSS, UP_PSP, START, R = 0x4000, 0x0010, 0x0008, 0x0200

    def injector(self, src, profile="xbox", timeout=0.5):
        import inject
        prof = inject.load_profile(str(ROOT / "server" / "keymap.json"), profile)
        inj = wolf_input.WolfInjector(prof, timeout=timeout, source_getter=lambda: src)
        inj.out.idle_s = 0.1
        self.addCleanup(inj.close)
        return inj

    def packets(self):
        with self.wolf.lock:
            return [parse_input(p) for p in self.wolf.inputs]

    def joined(self):
        with self.wolf.lock:
            return fake_wolf.DUMMY_ID in self.wolf.lobbies[0].get("connected_sessions", [])

    def test_buttons_reach_the_wolf(self):
        src = self.source()
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        self.assertEqual(self.wolf.paths("POST").count("/lobbies/join"), 0)  # sem controles, não entra
        inj = self.injector(src)
        self.assertTrue(self.wolf.wait_for(self.joined, 10))
        self.assertEqual(self.wolf.bodies("/lobbies/join")[0],
                         {"lobby_id": LOBBY, "moonlight_session_id": fake_wolf.DUMMY_ID})
        inj.update(self.CROSS, 128, 0)  # X do PSP (A no Xbox) e o analógico todo para cima
        self.assertTrue(self.wolf.wait_for(lambda: any(p.get("buttons") == 0x1000 for p in self.packets())))
        pkts = self.packets()
        self.assertEqual(pkts[0]["kind"], "arrival")  # o controle chega antes do estado
        a = next(p for p in pkts if p.get("buttons") == 0x1000)
        self.assertEqual(a["mask"], 1)
        self.assertGreater(a["lsy"], 30000)
        inj.update(0, 128, 128)
        self.assertTrue(self.wolf.wait_for(lambda: self.packets()[-1].get("buttons") == 0))
        inj.close()
        last = self.packets()[-1]
        self.assertEqual((last["kind"], last["mask"]), ("multi", 0))  # o Wolf desliga o controle
        self.assertTrue(self.wolf.wait_for(lambda: "/lobbies/leave" in self.wolf.paths("POST"), 10))

    def test_timeout_releases(self):
        src = self.source()
        src.start()
        inj = self.injector(src, timeout=0.3)
        self.assertTrue(self.wolf.wait_for(self.joined, 15))
        with self.assertLogs("pspstream.gamepad", "WARNING"):
            inj.update(self.CROSS, 128, 128)
            self.assertTrue(self.wolf.wait_for(lambda: any(p.get("buttons") == 0x1000 for p in self.packets())))
            # o PSP para de mandar: tudo volta ao neutro
            self.assertTrue(self.wolf.wait_for(lambda: self.packets()[-1].get("buttons") == 0, 3))

    def test_new_session_announces_the_controller_again(self):
        src = self.source()
        src.start()
        inj = self.injector(src, timeout=0)  # o "PSP" segura o X sem reafirmar: sem o --input-timeout
        self.assertTrue(self.wolf.wait_for(self.joined, 15))
        inj.update(self.CROSS, 128, 128)
        self.assertTrue(self.wolf.wait_for(lambda: len(self.packets()) >= 2))
        with self.wolf.lock:
            saved = self.wolf.lobbies.pop()
        self.assertTrue(self.wolf.wait_for(lambda: not self.wolf.sessions))
        with self.wolf.lock:
            saved["connected_sessions"] = []
            self.wolf.lobbies.append(saved)
        def announced_again():
            pkts = self.packets()
            return [p["kind"] for p in pkts].count("arrival") == 2 and pkts[-1].get("buttons") == 0x1000
        self.assertTrue(self.wolf.wait_for(announced_again, 15))  # o botão ainda segurado vai logo depois

    def test_wolf_ui_combo_rejoins(self):
        src = self.source()
        src.start()
        self.injector(src)
        self.assertTrue(self.wolf.wait_for(self.joined, 15))
        with self.wolf.lock:  # START + cima + RB: o Wolf tira a sessão do lobby
            self.wolf.lobbies[0]["connected_sessions"] = []
        with self.assertLogs("pspstream.wolf", "INFO") as logs:
            self.assertTrue(self.wolf.wait_for(self.joined, 10))
        self.assertIn("Wolf UI", "\n".join(logs.output))
        self.assertEqual(self.wolf.paths("POST").count("/lobbies/join"), 2)

    def test_session_target_is_view_only(self):
        with self.wolf.lock:
            self.wolf.sessions.append({"client_id": "555", "client_ip": "192.168.0.20", "rtsp_fake_ip": "1.2.3.4"})
        src = self.source(target="555")
        with self.assertLogs("pspstream.wolf", "WARNING") as logs:
            src.start()
            inj = self.injector(src)
            self.assertIsNotNone(src.wait_newer(0, 10))
            inj.update(self.CROSS, 128, 128)
            time.sleep(1)
        self.assertIn("só visualização", "\n".join(logs.output))
        self.assertNotIn("/lobbies/join", self.wolf.paths("POST"))
        self.assertEqual(self.wolf.inputs, [])  # nada vai para o compositor vazio da nossa sessão

    def test_full_lobby_waits(self):
        with self.wolf.lock:
            self.wolf.lobbies[0].update(multi_user=False, connected_sessions=["777"])
        src = self.source()
        with self.assertLogs("pspstream.wolf", "WARNING") as logs:
            src.start()
            self.injector(src)
            self.assertTrue(self.wolf.wait_for(lambda: "/lobbies/join" in self.wolf.paths("POST"), 15))
            time.sleep(0.5)
        self.assertIn("Lobby is full", "\n".join(logs.output))
        with self.wolf.lock:  # o outro jogador saiu
            self.wolf.lobbies[0]["connected_sessions"] = []
        self.assertTrue(self.wolf.wait_for(self.joined, 10))

    def test_pin(self):
        with self.wolf.lock:
            self.wolf.lobbies[0]["_pin"] = [4, 2, 4, 2]
        src = self.source(pin="4242")
        src.start()
        self.injector(src)
        self.assertTrue(self.wolf.wait_for(self.joined, 15))
        self.assertEqual(self.wolf.bodies("/lobbies/join")[0]["pin"], [4, 2, 4, 2])

    def test_open_injector(self):
        import capture
        import pspstream
        args = pspstream.build_parser().parse_args(["--source", "wolf", "--wolf-pin", "1234"])  # perfil jogo
        with self.assertLogs("pspstream.capture", "INFO") as logs:  # o padrão: só informa
            inj, kind = capture.open_injector(args)
        self.addCleanup(inj.close)
        self.assertIsInstance(inj, wolf_input.WolfInjector)
        self.assertIn("INFO:pspstream.capture:controles: o perfil 'jogo' é de teclado", "\n".join(logs.output))
        desktop = pspstream.build_parser().parse_args(["--source", "wolf", "--profile", "desktop"])
        with self.assertLogs("pspstream.capture", "WARNING"):  # escolhido de propósito: avisa
            inj2, _ = capture.open_injector(desktop)
        self.addCleanup(inj2.close)
        self.assertIn("Wolf", kind)
        self.assertEqual(capture.build_source(args).pin, "1234")
        with self.assertRaises(SystemExit):
            pspstream.build_parser().parse_args(["--wolf-pin", "12a4"])


class SeveralLobbiesTest(WolfCase):
    lobbies = (lobby(), lobby(LOBBY2, "Retroarch"))

    def test_error_lists_the_lobbies(self):
        src = self.source()
        with self.assertRaises(TargetError) as cm:
            src.start()
        self.assertIn("Retroarch", str(cm.exception))
        self.assertIn("--wolf-target", str(cm.exception))
        src = self.source(target="Retroarch")
        src.start()
        self.assertIsNotNone(src.wait_newer(0, 10))
        self.assertIn(f"listen-to={LOBBY2}_video", self.video_started()[0][2])


@unittest.skipIf(wolf_source is None, "sem GStreamer")
class WolfOptionsTest(unittest.TestCase):
    def test_cli_and_build(self):
        import capture
        import pspstream
        with mock.patch.dict(os.environ, {"WOLF_VIDEO_PING_PORT": "49100", "WOLF_SOCKET_PATH": "/run/w.sock"}):
            args = pspstream.build_parser().parse_args(["--source", "wolf", "--codec", "jpeg",
                                                        "--wolf-target", "Steam"])
        self.assertEqual((args.wolf_rtp_port, args.wolf_audio_rtp_port), (49100, 48200))
        self.assertEqual((args.wolf_socket, args.wolf_video_convert), ("/run/w.sock", "auto"))
        src = capture.build_source(args)
        self.assertIsInstance(src, WolfSource)
        self.assertEqual((src.wanted, src.api.socket_path, src.video_ping_port), ("Steam", "/run/w.sock", 49100))

    def test_web_choices(self):
        import control
        import pspstream
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock = Path(tmp.name) / "wolf.sock"
        args = pspstream.build_parser().parse_args(["--wolf-socket", str(sock), "--wolf-video-convert",
                                                    "glupload ! gldownload"])
        ctl = control.Controller(args, None, mock.Mock(), None)
        source = settings.BY_KEY["source"]
        self.assertNotIn("wolf", ctl.choices(source))  # sem o socket do Wolf, a opção não aparece
        sock.touch()
        self.assertIn("wolf", ctl.choices(source))
        convert = settings.BY_KEY["wolf_video_convert"]
        self.assertEqual(ctl.choices(convert), ("auto", "nvidia", "va", "cpu", "glupload ! gldownload"))
        with self.assertRaises(ValueError):
            settings.coerce(convert, "filesink location=/x")  # elementos próprios só pela linha de comando
        target = settings.BY_KEY["wolf_target"]
        self.assertEqual(settings.coerce(target, " Steam "), "Steam")
        with self.assertRaises(ValueError):
            settings.coerce(target, "a\nb")


if __name__ == "__main__":
    unittest.main()
