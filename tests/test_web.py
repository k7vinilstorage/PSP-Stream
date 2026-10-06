"""Interface web: esquema e arquivo de configuração, HTTP, controlador e a
troca de captura com o PSP conectado.

  python3 -m unittest discover tests
"""
import argparse
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

import capture  # noqa: E402
import control  # noqa: E402
import pspstream  # noqa: E402
import settings  # noqa: E402
import web  # noqa: E402


def base_args(argv=()):
    args = pspstream.build_parser().parse_args(list(argv))
    args.codec_choice = args.codec
    return args


class SettingsTest(unittest.TestCase):
    def test_coerce(self):
        b = settings.BY_KEY
        self.assertIs(settings.coerce(b["stretch"], True), True)
        with self.assertRaises(ValueError):
            settings.coerce(b["stretch"], "sim")
        self.assertEqual(settings.coerce(b["fps"], "40"), 40)
        for bad in (4, 241, 30.5, "x", True, None, float("nan")):
            with self.assertRaises(ValueError, msg=bad):
                settings.coerce(b["fps"], bad)
        self.assertEqual(settings.coerce(b["mouse_speed"], 1.5), 1.5)
        self.assertEqual(settings.coerce(b["audio_rate"], "32000"), 32000)
        with self.assertRaises(ValueError):
            settings.coerce(b["audio_rate"], 16000)
        self.assertEqual(settings.coerce(b["codec"], "jpeg"), "jpeg")
        self.assertEqual(settings.coerce(b["audio_device"], " alsa_output.pci-0000_00_1f.3.analog-stereo.monitor "),
                         "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor")

    def test_audio_device_cannot_inject_pipeline(self):
        # vai para dentro de pulsesrc device="..." na descrição do GStreamer
        for bad in ('x" ! filesink location=/tmp/x', "a b", "", "a;b", "$(id)"):
            with self.assertRaises(ValueError, msg=bad):
                settings.coerce(settings.BY_KEY["audio_device"], bad)

    def test_schema_matches_parser(self):
        args = base_args()
        for s in settings.SETTINGS:
            self.assertTrue(hasattr(args, s.dest), s.key)
            value = settings.arg_value(s, args)
            if s.kind == "choice" and s.choices:
                self.assertIn(value, s.choices, s.key)
            else:
                settings.coerce(s, value, None if s.choices or s.kind != "choice" else (value,))
        self.assertEqual(settings.arg_value(settings.BY_KEY["audio"], base_args(["--no-audio"])), False)

    def test_explicit_dests(self):
        parser = pspstream.build_parser()
        got = settings.explicit_dests(parser, ["--fps", "40", "--no-audio", "--fixed-quality", "-q", "70",
                                               "--bench", "--codec=jpeg"])
        self.assertEqual(got, {"fps", "no_audio", "adaptive", "quality", "bench", "codec"})
        self.assertEqual(settings.explicit_dests(parser, []), set())

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "server.json"
            defaults = {"fps": 60, "quality": 60}
            store = settings.ConfigStore(path, defaults)
            store.update({"fps": 30, "quality": 60})  # igual ao padrão: não grava
            store.save()
            self.assertEqual(json.loads(path.read_text()), {"fps": 30})
            path.write_text(json.dumps({"fps": 30, "nada": 1, "quality": 500, "q_min": 80, "q_max": 40,
                                        "profile": "xbox"}))
            with self.assertLogs("pspstream.settings", "WARNING"):
                loaded = settings.ConfigStore(path, defaults).load()
            self.assertEqual(loaded, {"fps": 30, "q_max": 40, "profile": "xbox"})

    def test_load_config_precedence(self):
        # padrão < arquivo < linha de comando
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.json"
            path.write_text(json.dumps({"fps": 30, "quality": 70, "audio": False, "codec": "jpeg"}))
            parser = pspstream.build_parser()
            argv = ["--fps", "45", "--config", str(path)]
            args = parser.parse_args(argv)
            args.codec_choice = args.codec
            with self.assertLogs("pspstream", "INFO"):
                store, explicit, from_file = pspstream.load_config(parser, args, argv)
            self.assertEqual((args.fps, args.quality, args.no_audio, args.codec, args.codec_choice),
                             (45, 70, True, "jpeg", "jpeg"))
            self.assertEqual(sorted(from_file), ["audio", "codec", "quality"])
            self.assertIn("fps", explicit)


class FakeController:
    def __init__(self):
        self.applied = []

    def config(self):
        return {"settings": [], "values": {}}

    def status(self):
        return {"ok": True}

    def apply(self, values):
        self.applied.append(values)
        return {"ok": True, "applied": list(values)}


class WebPasswordTest(unittest.TestCase):
    """Senha (PSPSTREAM_WEB_PASSWORD) e nomes liberados (--web-allow-host), para a interface web na rede local."""

    def setUp(self):
        self.ctl = FakeController()
        self.srv = web.WebServer(self.ctl, "127.0.0.1", 0, web.LogRing(), password="segredo çã",
                                 allow_hosts=["psp.exemplo.com", "Outro.Exemplo.com:443"])
        self.srv.start()
        self.port = self.srv.httpd.server_address[1]
        self.addCleanup(self.srv.close)
        patcher = mock.patch.object(web, "AUTH_DELAY_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    @staticmethod
    def basic(password, user="psp"):
        import base64
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}

    def test_password(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 401)
        self.assertIn('Basic realm="PSPStream"', headers["WWW-Authenticate"])
        self.assertEqual(self.request("GET", "/api/status")[0], 401)  # a API também
        with self.assertLogs("pspstream.web", "WARNING"):
            self.assertEqual(self.request("GET", "/", self.basic("errada"))[0], 401)
        self.assertEqual(self.request("GET", "/", {"Authorization": "Basic !!!"})[0], 401)
        self.assertEqual(self.request("GET", "/", {"Authorization": "Bearer segredo"})[0], 401)
        self.assertEqual(self.request("GET", "/", self.basic("segredo çã"))[0], 200)
        self.assertEqual(self.request("GET", "/", self.basic("segredo çã", user="qualquer"))[0], 200)
        body = json.dumps({"values": {"fps": 30}})
        post = {"Content-Type": "application/json", **self.basic("segredo çã")}
        self.assertEqual(self.request("POST", "/api/config", {"Content-Type": "application/json"}, body)[0], 401)
        self.assertEqual(self.request("POST", "/api/config", post, body)[0], 200)
        self.assertEqual(self.ctl.applied, [{"fps": 30}])
        self.assertTrue(self.srv.password)

    def test_allowed_host(self):
        """Nomes liberados (sem contar maiúsculas, ponto no fim e porta), e o Origin com o mesmo nome."""
        auth = self.basic("segredo çã")
        for host in ("psp.exemplo.com", "PSP.exemplo.com.", "outro.exemplo.com"):
            self.assertEqual(self.request("GET", "/api/config", {"Host": host, **auth})[0], 200, host)
        self.assertEqual(self.request("GET", "/api/config", {"Host": "evil.example", **auth})[0], 403)
        body = json.dumps({"values": {"fps": 30}})
        ok = {"Host": "psp.exemplo.com", "Origin": "https://psp.exemplo.com", "Content-Type": "application/json",
              **auth}
        self.assertEqual(self.request("POST", "/api/config", ok, body)[0], 200)
        bad = {**ok, "Origin": "https://evil.example"}
        self.assertEqual(self.request("POST", "/api/config", bad, body)[0], 403)

    def test_cli_and_env(self):
        with mock.patch.dict("os.environ", {"PSPSTREAM_WEB": "0.0.0.0:5124",
                                            "PSPSTREAM_WEB_HOSTS": "psp.exemplo.com, outro.exemplo.com"}):
            args = pspstream.build_parser().parse_args([])
        self.assertEqual(args.web, "0.0.0.0:5124")
        self.assertEqual(args.web_allow_host, ["psp.exemplo.com", "outro.exemplo.com"])
        with mock.patch.dict("os.environ", {}, clear=True):
            args = pspstream.build_parser().parse_args(["--web-allow-host", "a.b", "--web-allow-host", "c.d"])
        self.assertEqual((args.web, args.web_allow_host), ("127.0.0.1:5124", ["a.b", "c.d"]))


class WebServerTest(unittest.TestCase):
    def setUp(self):
        self.ctl = FakeController()
        self.ring = web.LogRing()
        self.srv = web.WebServer(self.ctl, "127.0.0.1", 0, self.ring)
        self.srv.start()
        self.port = self.srv.httpd.server_address[1]

    def tearDown(self):
        self.srv.close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    def test_static_files(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/app.js", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        for path, ctype in (("/app.js", "text/javascript"), ("/style.css", "text/css")):
            status, headers, _ = self.request("GET", path)
            self.assertEqual((status, headers["Content-Type"].split(";")[0]), (200, ctype))
        self.assertEqual(self.request("GET", "/../settings.py")[0], 404)

    def test_api(self):
        status, _, body = self.request("GET", "/api/config")
        self.assertEqual((status, json.loads(body)["values"]), (200, {}))
        import logging
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "linha %d", (1,), None)
        self.ring.emit(rec)
        log = json.loads(self.request("GET", "/api/status?since=0")[2])["log"]
        self.assertEqual(log[-1][1:], ["info", log[-1][2]])
        self.assertTrue(log[-1][2].endswith("linha 1"))
        self.assertEqual(json.loads(self.request("GET", f"/api/status?since={log[-1][0]}")[2])["log"], [])

    def test_post(self):
        body = json.dumps({"values": {"fps": 30}})
        status, _, out = self.request("POST", "/api/config", body, {"Content-Type": "application/json"})
        self.assertEqual((status, json.loads(out)["ok"]), (200, True))
        self.assertEqual(self.ctl.applied, [{"fps": 30}])

    def test_post_protections(self):
        body = json.dumps({"values": {"fps": 30}})
        cases = [
            ({"Content-Type": "text/plain"}, 415),           # formulário/fetch simples de outro site
            ({"Content-Type": "application/json", "Origin": "http://evil.example"}, 403),
            ({"Content-Type": "application/json", "Host": "evil.example:5124"}, 403),  # DNS rebinding
        ]
        for headers, code in cases:
            self.assertEqual(self.request("POST", "/api/config", body, headers)[0], code, headers)
        self.assertEqual(self.request("POST", "/api/config", "{", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/config", json.dumps([1]),
                                      {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.ctl.applied, [])
        # a própria página: Origin igual ao Host
        ok = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.request("POST", "/api/config", body, ok)[0], 200)

    def test_host_allowed(self):
        for host in ("localhost", "localhost:5124", "127.0.0.1:5124", "[::1]:5124", "192.168.0.5:5124",
                     socket.gethostname() + ":5124"):
            self.assertTrue(web.host_allowed(host), host)
        for host in ("", "evil.example", "localhost.evil.example:5124", "127.0.0.1.nip.io"):
            self.assertFalse(web.host_allowed(host), host)

    def test_parse_addr(self):
        self.assertEqual(web.parse_addr("0.0.0.0:8000"), ("0.0.0.0", 8000))
        self.assertEqual(web.parse_addr(":8000"), ("127.0.0.1", 8000))
        self.assertEqual(web.parse_addr("8000"), ("127.0.0.1", 8000))
        with self.assertRaises(ValueError):
            web.parse_addr("x:y")


class StubSource:
    repeat = True
    raw_i420 = False
    capture_ms = 0.0

    def __init__(self, name="a", failed=None, frame=b"\xff\xd8x"):
        self.name, self.failed, self.frame = name, failed, frame
        self.quality = 60
        self.fps = None
        self.stopped = False
        self.keepalive = None

    def latest(self):
        return 1, self.frame, 0.0

    def set_quality(self, q):
        self.quality = q

    def set_fps(self, fps):
        self.fps = fps

    def stop(self):
        self.stopped = True


class StubServer:
    def __init__(self, source):
        self.source, self.audio, self.injector = source, None, None
        self.sess = None

    def session(self):
        return self.sess

    def set_source(self, s):
        self.source = s

    def set_audio(self, a):
        self.audio = a

    def set_injector(self, i):
        self.injector = i


class ControllerTest(unittest.TestCase):
    def make(self, argv=("--source", "static", "--codec", "jpeg")):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        args = base_args(argv)
        defaults = base_args()
        store = settings.ConfigStore(Path(self.tmp.name) / "server.json",
                                     {s.key: settings.arg_value(s, defaults) for s in settings.SETTINGS})
        self.server = StubServer(StubSource())
        self.ctl = control.Controller(args, store, self.server, None, explicit={"source"}, version="t")
        return self.ctl

    def saved(self):
        path = Path(self.tmp.name) / "server.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def test_live(self):
        ctl = self.make()
        r = ctl.apply({"fps": 30, "quality": 75})
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.server.source.fps, self.server.source.quality, ctl.args.fps), (30, 75, 30))
        self.assertEqual(self.saved(), {"fps": 30, "quality": 75})
        self.assertEqual(ctl.apply({"fps": 30})["applied"], [])  # sem mudança

    def test_adaptive_on_session(self):
        ctl = self.make()
        sess = mock.Mock()
        sess.stats.adaptive = mock.Mock()
        self.server.sess = sess
        self.assertTrue(ctl.apply({"adaptive": False})["ok"])
        self.assertIsNone(sess.stats.adaptive)
        self.assertTrue(ctl.apply({"adaptive": True, "target_fps": 25, "q_max": 80})["ok"])
        a = sess.stats.adaptive
        self.assertEqual((round(a.budget_ms), a.q_max), (40, 80))

    def test_validation_applies_nothing(self):
        ctl = self.make()
        r = ctl.apply({"fps": 30, "q_min": 95, "nada": 1, "codec": "mpeg"})
        self.assertFalse(r["ok"])
        self.assertEqual(set(r["errors"]), {"q_min", "nada", "codec"})
        self.assertEqual((ctl.args.fps, self.server.source.fps), (60, None))
        self.assertEqual(self.saved(), {})

    def test_capture_swap(self):
        ctl = self.make()
        old = self.server.source
        new = StubSource("b")
        with mock.patch.object(capture, "start_source", return_value=new) as start:
            r = ctl.apply({"source": "test", "fps": 30, "scale": "lanczos"})
        self.assertTrue(r["ok"], r)
        built = start.call_args[0][0]
        self.assertEqual((built.source, built.fps, built.scale), ("test", 30, "lanczos"))  # nasce com o fps novo
        self.assertIs(self.server.source, new)
        self.assertTrue(old.stopped)
        self.assertEqual((ctl.args.source, ctl.args.fps), ("test", 30))
        self.assertEqual(self.saved(), {"source": "test", "fps": 30, "scale": "lanczos"})

    def test_capture_failure_keeps_old(self):
        ctl = self.make()
        old = self.server.source
        with mock.patch.object(capture, "start_source", side_effect=RuntimeError("sem monitor")):
            with self.assertLogs("pspstream.control", "WARNING"):
                r = ctl.apply({"source": "kms", "quality": 70})
        self.assertFalse(r["ok"])
        self.assertIn("sem monitor", r["errors"]["source"])
        self.assertEqual(r["applied"], ["quality"])  # o resto vale
        self.assertIs(self.server.source, old)
        self.assertFalse(old.stopped)
        self.assertEqual(ctl.args.source, "static")
        self.assertEqual(self.saved(), {"quality": 70})
        broken = StubSource("c", failed="erro no pipeline")
        with mock.patch.object(capture, "start_source", return_value=broken):
            with self.assertLogs("pspstream.control", "WARNING"):
                r = ctl.apply({"source": "x11"})
        self.assertIn("erro no pipeline", r["errors"]["source"])
        self.assertTrue(broken.stopped)
        self.assertIs(self.server.source, old)

    def test_codec_resolved(self):
        ctl = self.make()
        with mock.patch.object(capture, "resolve_codec", return_value="precisa do openh264"):
            with self.assertLogs("pspstream.control", "WARNING"):
                r = ctl.apply({"codec": "h264p"})
        self.assertIn("openh264", r["errors"]["codec"])
        self.assertEqual(ctl.args.codec_choice, "jpeg")

        def resolve(a):
            a.codec = "h264p"
        with mock.patch.object(capture, "resolve_codec", side_effect=resolve), \
                mock.patch.object(capture, "start_source", return_value=StubSource()):
            self.assertTrue(ctl.apply({"codec": "auto"})["ok"])
        self.assertEqual((ctl.args.codec_choice, ctl.args.codec), ("auto", "h264p"))
        self.assertEqual(ctl.values()["codec"], "auto")

    def test_audio_restart(self):
        ctl = self.make()
        old = mock.Mock(seq=1234)
        self.server.audio = old
        new = mock.Mock()
        with mock.patch.object(capture, "open_audio", return_value=new) as open_audio:
            self.assertTrue(ctl.apply({"audio_rate": 32000})["ok"])
        self.assertEqual(open_audio.call_args.kwargs["seq0"], 1234)  # o PSP não vê a numeração voltar
        self.assertIs(self.server.audio, new)
        old.stop.assert_called_once()
        self.assertTrue(ctl.apply({"audio": False})["ok"])
        self.assertIsNone(self.server.audio)
        new.stop.assert_called_once()
        self.assertEqual(self.saved(), {"audio_rate": 32000, "audio": False})

    def test_input_restart(self):
        ctl = self.make(("--source", "static", "--codec", "jpeg", "--input-dry-run"))
        old = mock.Mock()
        self.server.injector = old
        new = mock.Mock()
        with mock.patch.object(capture, "open_injector", return_value=(new, "teclado e mouse")):
            self.assertTrue(ctl.apply({"profile": "desktop"})["ok"])
        old.release_all.assert_called_once()
        old.close.assert_called_once()
        self.assertIs(self.server.injector, new)
        self.assertEqual(ctl.status()["input"], {"on": True, "profile": "desktop", "note": "teclado e mouse"})
        with self.assertRaises(ValueError):  # perfil que não existe no keymap
            settings.coerce(settings.BY_KEY["profile"], "nada", ctl.choices(settings.BY_KEY["profile"]))

    def test_restart_and_next(self):
        ctl = self.make()
        r = ctl.apply({"port": 6000, "p_redundancy_ms": 0})
        self.assertTrue(r["ok"])
        self.assertEqual([x["key"] for x in r["later"]], ["p_redundancy_ms", "port"])
        self.assertEqual((ctl.args.port, ctl.args.p_redundancy_ms), (5123, 0))  # a porta só ao reiniciar
        self.assertEqual((ctl.values()["port"], r["pending"]), (6000, {"port": 6000}))
        self.assertEqual(self.saved(), {"port": 6000, "p_redundancy_ms": 0})

    def test_config_marks_cli(self):
        cfg = self.make().config()
        by = {s["key"]: s for s in cfg["settings"]}
        self.assertTrue(by["source"]["cli"])
        self.assertFalse(by["fps"]["cli"])
        self.assertIn("xbox", by["profile"]["choices"])
        self.assertEqual(cfg["profiles"]["xbox"], "gamepad")
        self.assertIn("monitor", by["audio_device"]["suggestions"])
        self.assertNotIn("gst", by["source"]["choices"])  # pipeline próprio só pela linha de comando


class HotSwapTest(unittest.TestCase):
    """Captura trocada com o PSP (falso) conectado: a sessão continua."""

    def run_swap(self, first, second, h264p=False):
        import fake_client
        args = argparse.Namespace(adaptive=False, bench=None, stats_interval=60, target_fps=30, q_min=25,
                                  q_max=90, udp_pace=0, source="static", size=(480, 272), hdr_cache=True,
                                  dscp="ef", codec="h264p" if h264p else "jpeg", quality=70, p_redundancy_ms=6)
        server = pspstream.Server(first, args, None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        threading.Thread(target=server.serve_udp, args=(sock,), daemon=True).start()
        swapped = threading.Event()

        def swap():
            time.sleep(0.8)
            server.set_source(second)
            swapped.set()

        threading.Thread(target=swap, daemon=True).start()
        try:
            client_args = argparse.Namespace(host="127.0.0.1", port=sock.getsockname()[1], transport="udp", loss=0,
                                             kbps=2000, decode_ms=5, frames=0, seconds=1.8, rtt_ms=0,
                                             early_kb="auto", h264p=h264p)
            summary, last = fake_client.FakePSP(client_args).run()
        finally:
            server.close()
            sock.close()
        self.assertTrue(swapped.is_set())
        return summary, last

    def test_jpeg_to_jpeg(self):
        from sources import StaticSource
        card = (ROOT / "assets/testcard.jpg").read_bytes()
        other = card[:2] + b"\xff\xfe\x00\x07outra" + card[2:]  # comentário: outro JPEG válido
        summary, last = self.run_swap(StaticSource(card), StaticSource(other))
        self.assertGreater(summary["frames"], 20, summary)
        self.assertEqual(last, other)

    def test_jpeg_to_h264p(self):
        try:
            import h264
            import openh264
            if not openh264.available():
                raise ImportError
            raw = h264.image_to_i420(str(ROOT / "assets/testcard.jpg"), 480, 272)
        except (ImportError, ValueError, OSError):
            self.skipTest("sem openh264/GStreamer")
        import fake_client
        from sources import StaticSource
        card = (ROOT / "assets/testcard.jpg").read_bytes()
        summary, last = self.run_swap(StaticSource(card), StaticSource(raw, quality=70, raw_i420=True), h264p=True)
        self.assertEqual(summary["broken"], 0, summary)  # o primeiro frame P depois da troca é um IDR
        self.assertEqual(summary["idr_requests"], 0, summary)
        self.assertEqual(fake_client.h264_packet_kind(last), 1)


if __name__ == "__main__":
    unittest.main()
