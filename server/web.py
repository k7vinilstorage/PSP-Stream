"""Interface web das configurações gerais (http://127.0.0.1:5124 por padrão).

Só a biblioteca padrão (http.server): sem dependência nova, e serve igual
no servidor de Windows. A página (web/) lê /api/config uma vez e
/api/status a cada 2 s, e manda as mudanças para POST /api/config
(control.Controller.apply).

Por padrão escuta só no próprio PC (127.0.0.1). Proteções, porque qualquer
site aberto no navegador consegue mandar pedidos para o localhost:
  - Host: só "localhost", o nome do PC, um IP ou um nome liberado com
    --web-allow-host (ex.: um do DNS do roteador). Um domínio qualquer
    que aponte para 127.0.0.1 (DNS rebinding) é recusado.
  - Senha (PSPSTREAM_WEB_PASSWORD): autenticação básica do HTTP em tudo,
    página e API, com espera de 1 s a cada senha errada. Ela vai em texto
    (HTTP): serve para a rede de casa.
  - POST: só com Content-Type application/json (um site de fora não manda
    isso sem a permissão do CORS, que este servidor nunca dá) e com Origin,
    se houver, igual ao Host.
  - Nada aqui recebe caminho de arquivo nem pipeline do GStreamer; o nome da
    fonte de som é conferido (settings.AUDIO_DEVICE_RE).
Com --web 0.0.0.0:5124 e sem senha, qualquer um na rede local muda as
configurações.
"""
import base64
import binascii
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from i18n import catalog, language, tr

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
        raise ValueError(tr("invalid port"))
    return host, port


AUTH_DELAY_S = 1.0  # espera a cada senha errada (o servidor tem uma thread por pedido)


def host_name(host: str) -> str:
    """O nome do cabeçalho Host, sem a porta e em minúsculas."""
    if host.startswith("["):
        name = host[1:host.find("]")] if "]" in host else ""
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return name.lower().rstrip(".")


def host_allowed(host: str, extra=()) -> bool:
    """Cabeçalho Host aceito: localhost, o nome deste PC, um IP ou um nome de `extra`."""
    if not host:
        return False
    name = host_name(host)
    if name == "localhost" or (name and name in extra):
        return True
    pc = socket.gethostname().lower()
    if name in (pc, pc + ".local"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def password_ok(header: str, password: str) -> bool:
    """Authorization: Basic base64(usuário:senha). Qualquer usuário; a senha comparada em tempo constante."""
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    given = decoded.partition(":")[2]
    return hmac.compare_digest(given.encode(), password.encode())


def make_handler(controller, ring, password=None, allow_hosts=()):
    """password: senha da autenticação básica (None = sem senha); allow_hosts: nomes aceitos no Host."""
    extra = {host_name(h) for h in allow_hosts if h}

    class Handler(BaseHTTPRequestHandler):
        server_version = "PSPStream"
        sys_version = ""

        def log_message(self, fmt, *args):  # sem uma linha por pedido no terminal
            log.debug("web: %s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, extra_headers=()) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in extra_headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _checked(self) -> bool:
            if not host_allowed(self.headers.get("Host", ""), extra):
                self._json(403, {"error": tr("address not allowed (use http://localhost, the PC's IP or a name "
                                             "allowed with --web-allow-host)")})
                return False
            if password and not password_ok(self.headers.get("Authorization", ""), password):
                if self.headers.get("Authorization"):
                    log.warning(tr("web: wrong password from %s"), self.client_address[0])
                    time.sleep(AUTH_DELAY_S)
                body = json.dumps({"error": tr("password")}, ensure_ascii=False).encode()
                self._send(401, body, "application/json; charset=utf-8",
                           [("WWW-Authenticate", 'Basic realm="PSPStream", charset="UTF-8"')])
                return False
            return True

        def do_GET(self):
            if not self._checked():
                return
            path, _, query = self.path.partition("?")
            if path in STATIC:
                name, ctype = STATIC[path]
                try:
                    body = (WEB_DIR / name).read_bytes()
                except OSError:
                    self._json(500, {"error": tr("missing file {name}").format(name=name)})
                    return
                self._send(200, body, ctype)
            elif path == "/api/config":
                self._json(200, controller.config())
            elif path == "/api/i18n":
                self._json(200, {"lang": language(), "messages": catalog()})
            elif path == "/api/status":
                try:
                    since = int(parse_qs(query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                self._json(200, {**controller.status(), "log": ring.since(since) if ring else []})
            else:
                self._json(404, {"error": tr("not found")})

        do_HEAD = do_GET

        def do_POST(self):
            if not self._checked():
                return
            if self.path.partition("?")[0] != "/api/config":
                self._json(404, {"error": tr("not found")})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower():
                self._json(403, {"error": tr("origin not allowed")})
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
                self._json(413, {"error": tr("empty or too large request")})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._json(400, {"error": tr("invalid JSON")})
                return
            values = body.get("values") if isinstance(body, dict) else None
            if not isinstance(values, dict):
                self._json(400, {"error": tr('expected {"values": {...}}')})
                return
            self._json(200, controller.apply(values))

    return Handler


class WebServer:
    def __init__(self, controller, host: str = "127.0.0.1", port: int = 5124, ring=None, password=None,
                 allow_hosts=()):
        self.httpd = ThreadingHTTPServer((host, port), make_handler(controller, ring, password, allow_hosts))
        self.password = bool(password)
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
