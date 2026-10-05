"""Imita o Wolf nos testes: a API num socket Unix (HTTP/1.0, uma requisição
por conexão, como o Wolf), o ping UDP das sessões e o pipeline que a
sessão pede, com videotestsrc no lugar do interpipesrc (o interpipe só
existe dentro do processo do Wolf).

Imita também o que foi conferido no código do Wolf stable (facb8e0):
- os campos obrigatórios de StreamSession, VideoSession e AudioSession;
- o fmt::format do pipeline: chave sozinha é erro;
- todas as sessões criadas sem client_id com o mesmo id;
- o pipeline só roda depois do ping com o segredo da sessão;
- sessions/stop manda EOS para os pipelines da sessão.
Não imita: GPU, compositor, PulseAudio, controles de verdade.
"""
import json
import os
import re
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)

DUMMY_ID = "10594003729173467913"  # o Wolf: hash do certificado vazio, igual para toda sessão sem client_id
FAKE_VIDEO = "videotestsrc is-live=true pattern=ball ! video/x-raw,width=1280,height=720,framerate=30/1"
FAKE_AUDIO = "audiotestsrc is-live=true wave=sine freq=440 ! audio/x-raw,rate=48000,channels=2"

SESSION_FIELDS = ("client_ip", "aes_key", "aes_iv", "rtsp_fake_ip", "video_width", "video_height",
                  "video_refresh_rate", "audio_channel_count")
VIDEO_FIELDS = ("display_mode", "gst_pipeline", "render_node", "session_id", "port", "timeout_ms", "wait_for_ping",
                "packet_size", "frames_with_invalid_ref_threshold", "fec_percentage", "min_required_fec_packets",
                "bitrate_kbps", "slices_per_frame", "color_range", "color_space", "client_ip", "rtp_secret_payload")
AUDIO_FIELDS = ("gst_pipeline", "session_id", "encrypt_audio", "aes_key", "aes_iv", "port", "wait_for_ping",
                "client_ip", "rtp_secret_payload", "packet_duration", "audio_mode")


def fmt_unescape(text: str) -> str:
    """O que o fmt::format do Wolf faz com um pipeline sem placeholders: {{ -> {. Chave sozinha é erro
    (no Wolf, um placeholder que ele não conhece derruba o pipeline)."""
    rest = text.replace("{{", "").replace("}}", "")
    if "{" in rest or "}" in rest:
        raise ValueError("fmt::format: chave sem escape no pipeline")
    return text.replace("{{", "{").replace("}}", "}")


class FakeWolf:
    def __init__(self, directory, lobbies=None, video_port=0, audio_port=0):
        self.socket_path = os.path.join(directory, "wolf.sock")
        self.lock = threading.Lock()
        self.calls = []        # (método, caminho, corpo)
        self.lobbies = list(lobbies or [])
        self.sessions = []     # como o GET /sessions devolve
        self.waiting = []      # (tipo, segredo, id da sessão, pipeline) esperando o ping
        self.pipelines = {}    # id da sessão -> [pipelines rodando]
        self.started = []      # (tipo, id, pipeline do Wolf, ok, erro)
        self.inputs = []       # pacotes recebidos (bytes)
        self.fail = None       # (código, erro) para a próxima requisição
        self.udp = {}
        self.ports = {}
        for kind, port in (("video", video_port), ("audio", audio_port)):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", port))
            sock.settimeout(0.2)
            self.udp[kind], self.ports[kind] = sock, sock.getsockname()[1]
        self.running = True
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                if self.headers.get("Transfer-Encoding") == "chunked":
                    self._reply(500, None, b"Chunked encoding not supported, use HTTP/1.0 instead")
                    return
                body = json.loads(self.rfile.read(length)) if length else None
                code, obj = fake.handle(method, self.path, body)
                self._reply(code, obj)

            def _reply(self, code, obj, raw=None):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code, "OK")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.server = Server(self.socket_path, Handler)
        self.threads = [threading.Thread(target=self.server.serve_forever, daemon=True)]
        self.threads += [threading.Thread(target=self._ping_loop, args=(k,), daemon=True) for k in self.udp]
        for t in self.threads:
            t.start()

    # ---- API ----

    def handle(self, method, path, body):
        with self.lock:
            self.calls.append((method, path, body))
            if self.fail is not None:
                code, error = self.fail
                self.fail = None
                return code, {"success": False, "error": error}
        route = (method, path.removeprefix("/api/v1"))
        if route == ("GET", "/lobbies"):
            with self.lock:
                return 200, {"success": True, "lobbies": [dict(lb) for lb in self.lobbies]}
        if route == ("GET", "/sessions"):
            with self.lock:
                return 200, {"success": True, "sessions": [dict(s) for s in self.sessions]}
        if route == ("POST", "/sessions/add"):
            return self._add(body)
        if route == ("POST", "/sessions/start"):
            return self._start(body)
        if route == ("POST", "/sessions/stop"):
            return self._stop(body)
        if route == ("POST", "/sessions/input"):
            if not self._has(body["session_id"]):
                return 500, {"success": False, "error": "Invalid session_id"}
            with self.lock:
                self.inputs.append(bytes.fromhex(body["input_packet_hex"]))
            return 200, {"success": True}
        if route == ("POST", "/lobbies/join"):
            return self._join(body)
        if route == ("POST", "/lobbies/leave"):
            with self.lock:
                for lb in self.lobbies:
                    if lb["id"] == body["lobby_id"]:
                        lb["connected_sessions"] = [s for s in lb.get("connected_sessions", [])
                                                    if s != body["moonlight_session_id"]]
            return 200, {"success": True}
        return 404, {"success": False, "error": f"not found: {method} {path}"}

    def _has(self, sid):
        with self.lock:
            return any(s["client_id"] == str(sid) for s in self.sessions)

    def _add(self, body):
        missing = [f for f in SESSION_FIELDS if f not in body]
        if missing:
            return 500, {"success": False, "error": f"Field named '{missing[0]}' not found."}
        entry = {k: body[k] for k in SESSION_FIELDS}
        entry.update(app_id=body.get("app_id", "dummy"), client_id=DUMMY_ID)
        with self.lock:
            self.sessions.append(entry)  # o Wolf também guarda a repetida
        return 200, {"success": True, "session_id": DUMMY_ID}

    def _start(self, body):
        if not self._has(body.get("session_id")):
            return 500, {"success": False, "error": "Invalid session_id"}
        for kind, fields in (("video", VIDEO_FIELDS), ("audio", AUDIO_FIELDS)):
            part = body.get(f"{kind}_session") or {}
            missing = [f for f in fields if f not in part]
            if missing:
                return 500, {"success": False, "error": f"{kind}_session: Field named '{missing[0]}' not found."}
            secret = bytes(part["rtp_secret_payload"])
            if len(secret) != 16:
                return 500, {"success": False, "error": "rtp_secret_payload: 16 itens"}
            with self.lock:
                self.waiting.append((kind, secret, body["session_id"], part["gst_pipeline"]))
        return 200, {"success": True}

    def _stop(self, body):
        sid = str(body.get("session_id"))
        if not self._has(sid):
            return 500, {"success": False, "error": "Invalid session_id"}
        with self.lock:
            self.sessions = [s for s in self.sessions if s["client_id"] != sid]
            self.waiting = [w for w in self.waiting if w[2] != sid]
            pipes = self.pipelines.pop(sid, [])
            for lb in self.lobbies:
                lb["connected_sessions"] = [s for s in lb.get("connected_sessions", []) if s != sid]
        for pipe in pipes:  # o Wolf: EOS no pipeline; ele termina e vai para NULL
            pipe.send_event(Gst.Event.new_eos())
            threading.Timer(0.2, pipe.set_state, (Gst.State.NULL,)).start()
        return 200, {"success": True}

    def _join(self, body):
        with self.lock:
            lobby = next((lb for lb in self.lobbies if lb["id"] == body["lobby_id"]), None)
            if lobby is None:
                return 500, {"success": False, "error": "Invalid lobby ID"}
            pin = lobby.get("_pin")
            if pin != body.get("pin"):
                return 500, {"success": False, "error": "Invalid PIN"}
            connected = lobby.setdefault("connected_sessions", [])
            if not lobby.get("multi_user", True) and connected:
                return 500, {"success": False, "error": "Lobby is full"}
            connected.append(str(body["moonlight_session_id"]))
        return 200, {"success": True}

    # ---- ping e pipelines ----

    def _ping_loop(self, kind):
        sock = self.udp[kind]
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if len(data) < 20:  # o Wolf: 4 bytes = ping antigo (IP + porta), sem segredo
                continue
            with self.lock:
                match = next((w for w in self.waiting if w[0] == kind and w[1] == data[:16]), None)
                if match is not None:
                    self.waiting.remove(match)
            if match is not None:
                self._run(kind, match[2], match[3])

    def _run(self, kind, sid, text):
        fake_src = FAKE_VIDEO if kind == "video" else FAKE_AUDIO
        try:
            desc = fmt_unescape(text)
            desc = re.sub(r"^interpipesrc [^!]*", fake_src + " ", desc)
            pipe = Gst.parse_launch(desc)
            if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("PLAYING falhou")
        except (GLib.Error, ValueError, RuntimeError) as exc:  # o Wolf: só um erro no log dele
            with self.lock:
                self.started.append((kind, sid, text, False, str(exc)))
            return
        with self.lock:
            self.started.append((kind, sid, text, True, None))
            self.pipelines.setdefault(sid, []).append(pipe)

    # ---- testes ----

    def paths(self, method=None):
        with self.lock:
            return [p.removeprefix("/api/v1") for m, p, _ in self.calls if method is None or m == method]

    def bodies(self, path):
        with self.lock:
            return [b for _, p, b in self.calls if p == "/api/v1" + path]

    def wait_for(self, cond, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def close(self):
        self.running = False
        with self.lock:
            pipes = [p for ps in self.pipelines.values() for p in ps]
            self.pipelines.clear()
        for pipe in pipes:
            pipe.set_state(Gst.State.NULL)
        self.server.shutdown()
        self.server.server_close()
        for sock in self.udp.values():
            sock.close()


def main(argv=None) -> int:
    """Wolf falso de pé (teste da imagem Docker e do CI): imprime um resumo em JSON ao receber SIGTERM."""
    import argparse
    import signal
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default="/var/run/wolf", help="onde criar o wolf.sock")
    p.add_argument("--video-port", type=int, default=48100)
    p.add_argument("--audio-port", type=int, default=48200)
    p.add_argument("--lobby", default="Steam", help="nome do lobby aberto")
    args = p.parse_args(argv)
    os.makedirs(args.dir, exist_ok=True)
    if os.path.exists(os.path.join(args.dir, "wolf.sock")):
        os.unlink(os.path.join(args.dir, "wolf.sock"))
    lobby = {"id": "8f0b2c6e-0d6a-4c1e-9a52-3f2f5d7a1b10", "name": args.lobby, "multi_user": True,
             "pin_required": False, "connected_sessions": []}
    wolf = FakeWolf(args.dir, [lobby], args.video_port, args.audio_port)
    print(f"Wolf falso: {wolf.socket_path}, ping {wolf.ports['video']}/{wolf.ports['audio']}", flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: done.set())
    signal.signal(signal.SIGINT, lambda *a: done.set())
    done.wait()
    with wolf.lock:
        summary = {"calls": [path.removeprefix("/api/v1") for method, path, _ in wolf.calls if method == "POST"],
                   "started": [(k, ok, err) for k, _, _, ok, err in wolf.started],
                   "inputs": len(wolf.inputs), "joined": lobby.get("connected_sessions", [])}
    print(json.dumps(summary), flush=True)
    wolf.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
