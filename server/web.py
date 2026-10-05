"""Interface web das configurações gerais (http://127.0.0.1:5124 por padrão).

Só a biblioteca padrão (http.server): sem dependência nova, e serve igual
no servidor de Windows. A página (web/) lê /api/config uma vez e
/api/status a cada 2 s, e manda as mudanças para POST /api/config
(control.Controller.apply).

Por padrão escuta só no próprio PC (127.0.0.1). Proteções, porque qualquer
site aberto no navegador consegue mandar pedidos para o localhost:
  - Host: só "localhost", o nome do PC ou um IP. Um domínio qualquer que
    aponte para 127.0.0.1 (DNS rebinding) é recusado.
  - POST: só com Content-Type application/json (um site de fora não manda
    isso sem a permissão do CORS, que este servidor nunca dá) e com Origin,
    se houver, igual ao Host.
  - Nada aqui recebe caminho de arquivo nem pipeline do GStreamer; o nome da
    fonte de som é conferido (settings.AUDIO_DEVICE_RE).
Com --web 0.0.0.0:5124, qualquer um na rede local muda as configurações.
"""
import ipaddress
import json
import logging
import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("pspstream.web")

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_ADDR = "127.0.0.1:5124"
MAX_BODY = 64 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class LogRing(logging.Handler):
    """As últimas linhas do log, para a página."""

    def __init__(self, size: int = 300):
        super().__init__()
        self.lines = deque(maxlen=size)
        self.next_id = 1
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record) -> None:
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 - um log com erro de formato não derruba quem logou
            return
        self.lines.append((self.next_id, record.levelname.lower(), text))
        self.next_id += 1

    def since(self, last_id: int) -> list:
        self.acquire()
        try:
            return [list(line) for line in self.lines if line[0] > last_id]
        finally:
            self.release()


def parse_addr(text: str):
    """'HOST:PORTA', ':PORTA' ou 'PORTA' -> (host, porta). ValueError se inválido."""
    host, sep, port = text.rpartition(":")
    if not sep:
        host, port = "", text
    host = host.strip("[]") or "127.0.0.1"
    port = int(port)
    if not 0 <= port <= 65535:
        raise ValueError("porta inválida")
    return host, port


def host_allowed(host: str) -> bool:
    """Cabeçalho Host aceito: localhost, o nome deste PC ou um IP (sem domínios)."""
    if not host:
        return False
    if host.startswith("["):
        name = host[1:host.find("]")] if "]" in host else ""
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.lower().rstrip(".")
    if name == "localhost":
        return True
    pc = socket.gethostname().lower()
    if name in (pc, pc + ".local"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def make_handler(controller, ring):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PSPStream"
        sys_version = ""

        def log_message(self, fmt, *args):  # sem uma linha por pedido no terminal
            log.debug("web: %s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _checked(self) -> bool:
            if host_allowed(self.headers.get("Host", "")):
                return True
            self._json(403, {"error": "endereço não permitido (use http://localhost ou o IP do PC)"})
            return False

        def do_GET(self):
            if not self._checked():
                return
            path, _, query = self.path.partition("?")
            if path in STATIC:
                name, ctype = STATIC[path]
                try:
                    body = (WEB_DIR / name).read_bytes()
                except OSError:
                    self._json(500, {"error": f"faltou o arquivo {name}"})
                    return
                self._send(200, body, ctype)
            elif path == "/api/config":
                self._json(200, controller.config())
            elif path == "/api/status":
                try:
                    since = int(parse_qs(query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                self._json(200, {**controller.status(), "log": ring.since(since) if ring else []})
            else:
                self._json(404, {"error": "não existe"})

        do_HEAD = do_GET

        def do_POST(self):
            if not self._checked():
                return
            if self.path.partition("?")[0] != "/api/config":
                self._json(404, {"error": "não existe"})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower():
                self._json(403, {"error": "origem não permitida"})
                return
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._json(415, {"error": "use application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                self._json(413, {"error": "pedido vazio ou grande demais"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._json(400, {"error": "JSON inválido"})
                return
            values = body.get("values") if isinstance(body, dict) else None
            if not isinstance(values, dict):
                self._json(400, {"error": "esperava {\"values\": {...}}"})
                return
            self._json(200, controller.apply(values))

    return Handler


class WebServer:
    def __init__(self, controller, host: str = "127.0.0.1", port: int = 5124, ring=None):
        self.httpd = ThreadingHTTPServer((host, port), make_handler(controller, ring))
        self.httpd.daemon_threads = True
        host, port = self.httpd.server_address[:2]
        shown = "localhost" if host in ("127.0.0.1", "::1") else host
        if host in ("0.0.0.0", "::"):
            from netcheck import local_ip
            shown = local_ip()
        self.url = f"http://{shown}:{port}"
        self.public = host not in ("127.0.0.1", "::1", "localhost")

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, name="web", daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
