"""Cliente mínimo da API do Wolf (Games on Whales): HTTP sobre um socket Unix.

Só biblioteca padrão. O Wolf atende uma requisição por conexão, responde em
HTTP/1.0 com Content-Length e recusa corpo "chunked"; o http.client manda o
corpo com Content-Length. Conferido no código do Wolf stable (facb8e0):
src/moonlight-server/api/unix_socket_server.cpp e api/endpoints.cpp.

Esse socket dá controle total do Wolf (parear clientes, iniciar apps). O
PSPStream só usa os endpoints abaixo e nunca o expõe por TCP: monte-o
apenas no container do PSPStream.

  GET  /api/v1/lobbies          lobbies abertos
  GET  /api/v1/sessions         sessões (o id da sessão vem em client_id)
  POST /api/v1/sessions/add     cria uma sessão sem cliente Moonlight
  POST /api/v1/sessions/start   pipelines de vídeo e de som da sessão
  POST /api/v1/sessions/stop    encerra a sessão
  POST /api/v1/sessions/input   pacote de controle do Moonlight, em hexadecimal
  POST /api/v1/lobbies/join     põe a sessão num lobby (controles)
  POST /api/v1/lobbies/leave    tira a sessão do lobby
"""
import http.client
import json
import os
import socket
from i18n import tr

DEFAULT_SOCKET = "/var/run/wolf/wolf.sock"
VIDEO_PING_PORT = 48100  # do Wolf (state/data-structures.hpp); 47998 é a do Moonlight
AUDIO_PING_PORT = 48200
TIMEOUT_S = 5.0


def default_socket() -> str:
    """WOLF_SOCKET_PATH (a mesma variável do Wolf), ou /var/run/wolf/wolf.sock."""
    return os.environ.get("WOLF_SOCKET_PATH") or DEFAULT_SOCKET


def env_port(name: str, default: int) -> int:
    """Porta de uma variável do Wolf (WOLF_VIDEO_PING_PORT, WOLF_AUDIO_PING_PORT), ou o padrão dele."""
    try:
        port = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return port if 0 < port < 65536 else default


class WolfApiError(RuntimeError):
    """Falha ao falar com a API: socket ausente, sem permissão, resposta de erro..."""


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class WolfApi:
    def __init__(self, socket_path=None, timeout: float = TIMEOUT_S):
        self.socket_path = socket_path or default_socket()
        self.timeout = timeout

    def _request(self, method: str, path: str, body=None) -> dict:
        where = f"{method} /api/v1{path}"
        conn = _UnixConnection(self.socket_path, self.timeout)
        try:
            payload, headers = None, {}
            if body is not None:
                payload = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            conn.request(method, "/api/v1" + path, body=payload, headers=headers)
            resp = conn.getresponse()
            status, data = resp.status, resp.read()
        except FileNotFoundError:
            raise WolfApiError(tr("the Wolf API socket does not exist: {path} (in the Wolf service: "
                                  "WOLF_SOCKET_PATH and the /var/run/wolf volume; here: --wolf-socket)")
                               .format(path=self.socket_path)) from None
        except PermissionError:
            raise WolfApiError(tr("no permission to open {path} (Wolf creates the socket as root; "
                                  "see the Wolf page of the PSPStream wiki)").format(path=self.socket_path)) from None
        except ConnectionRefusedError:
            raise WolfApiError(tr("nobody answers at {path}: is Wolf running?").format(path=self.socket_path)) from None
        except TimeoutError:
            raise WolfApiError(tr("{where}: Wolf did not answer in {seconds:g} s").format(where=where, seconds=self.timeout)) from None
        except (OSError, http.client.HTTPException) as exc:
            raise WolfApiError(f"{where}: {exc}") from None
        finally:
            conn.close()
        try:
            obj = json.loads(data)
        except ValueError:
            text = data[:200].decode("utf-8", "replace")
            raise WolfApiError(tr("{where}: reply that is not JSON (HTTP {status}): {text}").format(
                where=where, status=status, text=repr(text))) from None
        if status != 200 or not isinstance(obj, dict) or not obj.get("success", False):
            error = obj.get("error") if isinstance(obj, dict) else None
            raise WolfApiError(f"{where}: {error or tr('failed')} (HTTP {status})")
        return obj

    # ---- leitura ----

    def lobbies(self) -> list:
        return self._request("GET", "/lobbies").get("lobbies", [])

    def sessions(self) -> list:
        return self._request("GET", "/sessions").get("sessions", [])

    # ---- sessões ----

    def add_session(self, session: dict) -> str:
        """Campos de StreamSession (client_ip, aes_key, aes_iv, rtsp_fake_ip,
        video_width, video_height, video_refresh_rate, audio_channel_count).
        Devolve o id da sessão (texto)."""
        sid = self._request("POST", "/sessions/add", session).get("session_id")
        if not sid:
            raise WolfApiError(tr("POST /api/v1/sessions/add: the reply has no session_id"))
        return str(sid)

    def start_session(self, session_id: str, video_session: dict, audio_session: dict) -> None:
        self._request("POST", "/sessions/start", {"session_id": str(session_id), "video_session": video_session,
                                                  "audio_session": audio_session})

    def stop_session(self, session_id: str) -> None:
        self._request("POST", "/sessions/stop", {"session_id": str(session_id)})

    def send_input(self, session_id: str, packet: bytes) -> None:
        """Pacote completo, com o cabeçalho 0x0206 + tamanho: o Wolf não confere o tamanho."""
        self._request("POST", "/sessions/input", {"session_id": str(session_id), "input_packet_hex": packet.hex()})

    # ---- lobbies ----

    def join_lobby(self, lobby_id: str, session_id: str, pin=None) -> None:
        """pin: os dígitos do PIN ("1234" vira [1, 2, 3, 4]), se o lobby pede."""
        body = {"lobby_id": lobby_id, "moonlight_session_id": str(session_id)}
        if pin:
            body["pin"] = [int(d) for d in str(pin)]
        self._request("POST", "/lobbies/join", body)

    def leave_lobby(self, lobby_id: str, session_id: str) -> None:
        self._request("POST", "/lobbies/leave", {"lobby_id": lobby_id, "moonlight_session_id": str(session_id)})
