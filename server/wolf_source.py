"""Fonte --source wolf: o que roda no Wolf (Games on Whales), pela API.

Conferido no código do Wolf stable (facb8e0). Cada lobby ou sessão do Wolf
tem um produtor de vídeo (interpipesink <id>_video) que só existe dentro do
processo do Wolf. Por isso o PSPStream cria uma sessão própria pela API
(sessions/add) e o Wolf roda um pipeline nosso (sessions/start,
video_session.gst_pipeline):

  interpipesrc listen-to=<alvo>_video ! <conversão> ! I420 480x272
      ! gdppay ! tcpclientsink host=127.0.0.1 port=<porta>

e o PSPStream recebe com tcpserversrc ! gdpdepay (porta aleatória, só em
127.0.0.1), seguindo como as outras fontes (JPEG, h264, h264p). Os dois
containers precisam de network_mode: host, o mesmo 127.0.0.1.

- A conversão desce a imagem da GPU para a memória comum e reduz para
  480x272: nvidia (CUDAMemory, o padrão do Wolf com NVIDIA), va (DMABuf, com
  Intel/AMD) ou cpu (WOLF_USE_ZERO_COPY=FALSE). A API não diz qual é o caso:
  "auto" tenta nessa ordem e guarda a que funcionou.
- O Wolf só roda o pipeline depois de um ping UDP na porta de ping de vídeo
  (48100, WOLF_VIDEO_PING_PORT) com o segredo de 16 bytes da sessão
  (rtp_secret_payload, escolhido aqui). O ping de som (48200) também vai:
  sem ele, uma thread do Wolf fica esperando para sempre.
- O modelo pull continua: o interpipesrc guarda 1 buffer (leaky), e aqui o
  appsink fica só com o mais novo.
- Uma thread consulta a API a cada 2 s. Se o alvo some (o lobby parou),
  a sessão é encerrada e a fonte espera ele voltar; o servidor não cai.
  Ao sair (também no SIGTERM do docker stop), a sessão é encerrada.

Limites do Wolf, que só com a API não dá para mudar:
- Toda sessão criada sem client_id tem o mesmo id (o hash de um certificado
  vazio): um PSPStream por Wolf. Uma sessão nossa que sobrou de uma
  execução que morreu (marcada com rtsp_fake_ip = "pspstream") é encerrada
  antes de criar a nova.
- Sem app_id, a sessão usa um app "dummy" (um sleep em loop) com um
  compositor e um sink de som próprios, e o Wolf cria uma pasta vazia
  <uuid>/dummy no diretório de estado dele a cada sessão nova.
"""
import logging
import re
import secrets
import socket
import struct
import threading
import time
from dataclasses import dataclass

from gst_source import GstSource
from sources import FrameSource
from wolf_api import WolfApiError

log = logging.getLogger("pspstream.wolf")

MARKER = "pspstream"      # rtsp_fake_ip das nossas sessões (o Wolf só usa esse campo no RTSP do Moonlight)
CLIENT_IP = "127.0.0.1"   # não o IP de um cliente de verdade: o Wolf às vezes acha a sessão pelo IP
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # ids que entram no texto do pipeline

# Conversão para I420 na memória comum. {width}/{height}/{scale}/{borders} vêm daqui, não do Wolf.
CONVERTS = {
    "nvidia": ("cudaupload ! cudaconvertscale add-borders={borders} "
               "! video/x-raw(memory:CUDAMemory),format=I420,width={width},height={height},"
               "pixel-aspect-ratio=1/1 ! cudadownload"),
    "va": "vapostproc add-borders={borders}",
    "cpu": "videoconvertscale method={scale} add-borders={borders}",
}
AUTO_ORDER = ("nvidia", "va", "cpu")

# Fase 1: o som ainda não vem do Wolf; o pipeline de som só termina (o ping de som vai igual).
NO_AUDIO_PIPELINE = "audiotestsrc num-buffers=1 ! fakesink"


# ---- textos para a API (funções puras, testadas sem o Wolf) ----

def fmt_escape(text: str) -> str:
    """O Wolf passa o pipeline pelo fmt::format: chaves literais viram {{ e }}."""
    return text.replace("{", "{{").replace("}", "}}")


def convert_chain(choice: str, width: int, height: int, scale: str = "bilinear2", keep_aspect: bool = True) -> str:
    """Trecho de conversão: um dos CONVERTS, ou os elementos que o usuário deu."""
    template = CONVERTS.get(choice)
    if template is None:
        return choice.strip()
    return template.format(width=width, height=height, scale=scale, borders="true" if keep_aspect else "false")


def video_pipeline(producer: str, session_id: str, convert: str, width: int, height: int, port: int) -> str:
    """O pipeline que o Wolf roda, já escapado para o fmt::format.

    O interpipesrc não se chama interpipesrc_<sessão>_video (o nome que o Wolf
    procura ao trocar de lobby): a imagem segue sempre o alvo, mesmo quando a
    sessão entra ou sai de um lobby por causa dos controles."""
    for value in (producer, session_id):
        if not ID_RE.match(value):
            raise ValueError(f"id inesperado do Wolf: {value!r}")
    text = (f"interpipesrc name=pspstream_{session_id}_video listen-to={producer}_video is-live=true "
            "stream-sync=restart-ts max-bytes=0 max-buffers=1 leaky-type=downstream "
            f"! {convert} ! video/x-raw,format=I420,width={width},height={height},pixel-aspect-ratio=1/1 "
            f"! gdppay ! tcpclientsink host=127.0.0.1 port={port} sync=false")
    return fmt_escape(text)


def new_secret() -> bytes:
    """16 bytes ASCII visíveis, como os do próprio Wolf (o JSON leva números de 0 a 127)."""
    return bytes(33 + secrets.randbelow(94) for _ in range(16))


def ping_packet(secret: bytes, seq: int) -> bytes:
    """SS_PING do Moonlight: o segredo (16 bytes) e um número de sequência (o Wolf não lê)."""
    return secret + struct.pack(">I", seq & 0xFFFFFFFF)


def session_request(width: int, height: int) -> dict:
    """sessions/add. O app "dummy" sobe um compositor próprio, que ninguém vê: pequeno e a 30 Hz."""
    return {"client_ip": CLIENT_IP, "aes_key": secrets.token_hex(16), "aes_iv": str(secrets.randbelow(1 << 31)),
            "rtsp_fake_ip": MARKER, "video_width": width, "video_height": height, "video_refresh_rate": 30,
            "audio_channel_count": 2}


def video_session(session_id: str, pipeline: str, width: int, height: int, fps: int, ping_port: int,
                  secret: bytes) -> dict:
    """Todos os campos de VideoSession são obrigatórios; os de RTP não importam sem o wolf_udp_sink."""
    return {"display_mode": {"width": width, "height": height, "refreshRate": max(1, int(fps))},
            "gst_pipeline": pipeline, "render_node": "", "session_id": session_id, "port": ping_port,
            "timeout_ms": 1000, "wait_for_ping": True, "packet_size": 1024, "frames_with_invalid_ref_threshold": 0,
            "fec_percentage": 0, "min_required_fec_packets": 0, "bitrate_kbps": 1000, "slices_per_frame": 1,
            "color_range": "MPEG", "color_space": "BT601", "client_ip": CLIENT_IP,
            "rtp_secret_payload": list(secret)}


def audio_session(session_id: str, pipeline: str, ping_port: int, secret: bytes, aes_key: str, aes_iv: str,
                  channels: int = 2) -> dict:
    speakers = ["FRONT_LEFT", "FRONT_RIGHT"] if channels == 2 else ["FRONT_CENTER"]
    return {"gst_pipeline": pipeline, "session_id": session_id, "encrypt_audio": False, "aes_key": aes_key,
            "aes_iv": aes_iv, "port": ping_port, "wait_for_ping": True, "client_ip": CLIENT_IP,
            "rtp_secret_payload": list(secret), "packet_duration": 5,
            "audio_mode": {"channels": channels, "streams": 1, "coupled_streams": 1 if channels == 2 else 0,
                           "speakers": speakers, "bitrate": 96000, "sample_rate": 48000}}


# ---- alvo ----

class TargetError(RuntimeError):
    """Não dá para escolher o alvo sozinho (vários lobbies abertos)."""


@dataclass(frozen=True)
class Target:
    kind: str       # "lobby" ou "sessão"
    id: str
    name: str
    producer: str   # de quem é o <id>_video que o pipeline escuta

    def describe(self) -> str:
        via = f", que está no lobby {self.producer}" if self.producer != self.id else ""
        return f"{self.kind} {self.name or self.id} ({self.id}{via})"


def _options(lobbies, sessions) -> str:
    parts = [f"lobby {lb.get('id')} ({lb.get('name', '')})" for lb in lobbies]
    parts += [f"sessão {s.get('client_id')} ({s.get('client_ip', '')})" for s in sessions]
    return "; ".join(parts) if parts else "nenhum lobby nem sessão"


def resolve_target(wanted: str, lobbies: list, sessions: list, own=None):
    """(Target, None), ou (None, motivo de esperar). TargetError: vários lobbies e nenhum escolhido.

    wanted: id ou nome do lobby, ou id da sessão; vazio = o único lobby aberto.
    Uma sessão que está num lobby vê o lobby: o pipeline escuta o lobby."""
    others = [s for s in sessions if str(s.get("client_id")) != str(own) and s.get("rtsp_fake_ip") != MARKER]
    wanted = (wanted or "").strip()
    if not wanted:
        if not lobbies:
            return None, "nenhum lobby aberto no Wolf; esperando um (abra um jogo pelo Wolf UI)"
        if len(lobbies) > 1:
            raise TargetError("há vários lobbies abertos no Wolf; escolha um com --wolf-target: "
                              + _options(lobbies, []))
        lb = lobbies[0]
        return Target("lobby", str(lb["id"]), lb.get("name", ""), str(lb["id"])), None
    for lb in lobbies:
        if str(lb.get("id")) == wanted:
            return Target("lobby", wanted, lb.get("name", ""), wanted), None
    named = [lb for lb in lobbies if str(lb.get("name", "")).casefold() == wanted.casefold()]
    if len(named) == 1:
        return Target("lobby", str(named[0]["id"]), named[0].get("name", ""), str(named[0]["id"])), None
    for s in others:
        if str(s.get("client_id")) == wanted:
            lobby = next((lb for lb in lobbies if wanted in [str(x) for x in lb.get("connected_sessions", [])]),
                         None)
            producer = str(lobby["id"]) if lobby else wanted
            return Target("sessão", wanted, s.get("client_ip", ""), producer), None
    if len(named) > 1:
        return None, f"há {len(named)} lobbies com o nome '{wanted}'; use o id: {_options(named, [])}"
    return None, f"alvo '{wanted}' não está aberto no Wolf; esperando ({_options(lobbies, others)})"


# ---- a fonte ----

# Todas as sessões sem client_id têm o mesmo id no Wolf: uma por vez neste processo
# (a interface web sobe a fonte nova antes de parar a velha).
_SESSION_LOCK = threading.Lock()


class _Receiver(GstSource):
    """O lado do PSPStream: tcpserversrc ! gdpdepay, e o resto como as outras fontes."""

    def __init__(self, outer, *args, **kwargs):
        self.outer = outer
        self.closing = False  # o fim do pipeline é esperado (a sessão foi encerrada)
        super().__init__(*args, **kwargs)

    def publish(self, data, capture_ms=None):
        super().publish(data)    # o relatório da captura conta os frames por aqui
        self.outer.publish(data)  # o horário do buffer é o do Wolf: a idade da captura não dá para medir

    def _ended(self, reason: str) -> None:
        self.failed = reason
        if self.closing:
            log.debug("Wolf: recepção encerrada (%s)", reason)
        else:
            log.warning("Wolf: o vídeo parou de chegar (%s)", reason)

    @property
    def port(self) -> int:
        return self.pipeline.get_by_name("tcp").get_property("current-port")


class WolfSource(FrameSource):
    def __init__(self, api, target: str, convert: str, width: int, height: int, fps: int, quality: int,
                 scale: str = "bilinear2", keep_aspect: bool = True, codec: str = "jpeg",
                 video_ping_port: int = 48100, audio_ping_port: int = 48200, ping_host: str = "127.0.0.1",
                 poll_s: float = 2.0, first_frame_s: float = 10.0):
        super().__init__()
        self.api = api
        self.wanted = (target or "").strip()
        self.convert = (convert or "auto").strip()
        self.width, self.height = width, height
        self.fps = fps
        self._quality = quality
        self.scale, self.keep_aspect, self.codec = scale, keep_aspect, codec
        self.raw_i420 = codec == "h264p"  # a sessão codifica na hora de enviar
        self.video_ping_port, self.audio_ping_port, self.ping_host = video_ping_port, audio_ping_port, ping_host
        self.poll_s, self.first_frame_s = poll_s, first_frame_s
        self.failed = None     # a fonte do Wolf não desiste: espera o Wolf e o alvo
        self.keepalive = None
        self.target = None     # Target espelhado agora
        self.session_id = None
        self.working_convert = None  # a conversão que funcionou (auto)
        self._receiver = None
        self._stop = threading.Event()
        self._thread = None
        self._said = None

    # ---- FrameSource ----

    def start(self) -> None:
        # Vários lobbies sem --wolf-target é erro de configuração: aparece já, com a lista.
        # O Wolf fora do ar não: a thread espera ele voltar.
        try:
            target, why = resolve_target(self.wanted, self.api.lobbies(), self.api.sessions())
        except WolfApiError as exc:
            self._say(f"{exc}; tentando de novo a cada {self.poll_s:g} s", logging.WARNING)
        else:
            if target is None:
                self._say(why)
        self._thread = threading.Thread(target=self._run, name="wolf", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def set_fps(self, fps: int) -> None:
        self.fps = fps
        receiver = self._receiver
        if receiver is not None:
            receiver.set_fps(fps)

    def set_quality(self, quality: int) -> None:
        self._quality = max(1, min(100, int(quality)))
        receiver = self._receiver
        if receiver is not None:
            receiver.set_quality(self._quality)

    @property
    def quality(self):
        return self._quality

    # ---- a thread ----

    def _say(self, msg: str, level=logging.INFO) -> None:
        """Log só quando a mensagem muda (esperando o Wolf ou o alvo, sem repetir a cada 2 s)."""
        if msg != self._said:
            self._said = msg
            log.log(level, "Wolf: %s", msg)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if not self._acquire():
                return
            ok = False
            try:
                ok = self._cycle()
            except (WolfApiError, TargetError) as exc:
                self._say(str(exc), logging.WARNING)
            except Exception:  # um erro aqui não pode derrubar o servidor
                log.exception("Wolf: erro inesperado")
            finally:
                self._teardown()
                _SESSION_LOCK.release()
            failures = 0 if ok else failures + 1
            # Cada sessão nova sobe um compositor no Wolf: sem repetir em rajada.
            self._stop.wait(min(30.0, self.poll_s * 2 ** min(failures, 4)) if failures else 1.0)

    def _acquire(self) -> bool:
        waited = False
        while not self._stop.is_set():
            if _SESSION_LOCK.acquire(timeout=0.2):
                return True
            if not waited:
                waited = True
                log.info("Wolf: esperando a captura anterior encerrar a sessão dela")
        return False

    def _cycle(self) -> bool:
        """Uma sessão no Wolf, do alvo encontrado até ele sumir. True = terminou bem."""
        target = self._wait_target()
        if target is None:
            return True
        if self.convert != "auto":
            choices = [self.convert]
        else:
            choices = [self.working_convert] if self.working_convert else list(AUTO_ORDER)
        for choice in choices:
            if self._open(target, choice):
                break
            self._teardown()
            if self._stop.wait(1.0):  # a sessão nova tem o mesmo id: o Wolf termina de encerrar a velha
                return True
        else:
            self.working_convert = None  # a que funcionava falhou: na próxima, testa todas de novo
            tried = ", ".join(choices)
            self._say(f"nenhum frame chegou do Wolf em {self.first_frame_s:g} s (conversão: {tried}). Veja o log "
                      "do Wolf (docker logs) e a opção --wolf-video-convert", logging.WARNING)
            return False
        if self.convert == "auto" and self.working_convert is None:
            self.working_convert = choice
            log.info("Wolf: a conversão '%s' funcionou (--wolf-video-convert %s pula o teste das outras)",
                     choice, choice)
        self._said = None
        return self._watch(target)

    def _wait_target(self):
        while not self._stop.is_set():
            try:
                target, why = resolve_target(self.wanted, self.api.lobbies(), self.api.sessions())
            except (WolfApiError, TargetError) as exc:
                target, why = None, str(exc)
            if target is not None:
                return target
            self._say(why)
            self._stop.wait(self.poll_s)
        return None

    def _cleanup_stale(self) -> None:
        for s in self.api.sessions():
            if s.get("rtsp_fake_ip") == MARKER:
                log.warning("Wolf: encerrando a sessão %s, que sobrou de um PSPStream anterior (um PSPStream por "
                            "Wolf: todas têm o mesmo id)", s.get("client_id"))
                self.api.stop_session(str(s.get("client_id")))

    def _open(self, target: Target, choice: str) -> bool:
        """Cria a sessão, manda o ping e espera o primeiro frame."""
        receiver = _Receiver(self, "tcpserversrc name=tcp host=127.0.0.1 port=0 ! gdpdepay", self.width,
                             self.height, self.fps, self._quality, self.scale, self.keep_aspect, None, self.codec)
        self._receiver = receiver
        receiver.start()
        port = receiver.port
        if not port:
            raise RuntimeError("o tcpserversrc não abriu uma porta")
        self._cleanup_stale()
        request = session_request(self.width, self.height)
        sid = self.api.add_session(request)
        self.session_id = sid
        same = [s for s in self.api.sessions() if str(s.get("client_id")) == sid]
        if len(same) > 1:
            log.warning("Wolf: outra sessão criada pela API sem client_id usa o mesmo id (%s); a imagem pode não "
                        "chegar (só um programa assim por Wolf)", sid)
        secret = new_secret()
        convert = convert_chain(choice, self.width, self.height, self.scale, self.keep_aspect)
        pipeline = video_pipeline(target.producer, sid, convert, self.width, self.height, port)
        log.debug("Wolf: pipeline de vídeo: %s", pipeline)
        self.api.start_session(sid, video_session(sid, pipeline, self.width, self.height, self.fps,
                                                  self.video_ping_port, secret),
                               audio_session(sid, NO_AUDIO_PIPELINE, self.audio_ping_port, secret,
                                             request["aes_key"], request["aes_iv"]))
        self.target = target
        log.info("Wolf: sessão %s espelhando %s, conversão %s", sid, target.describe(), choice)
        seq0 = self.latest()[0]
        deadline = time.monotonic() + self.first_frame_s
        n = 0
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self.latest()[0] > seq0:
                    return True
                if receiver.failed:
                    break
                if n % 5 == 0:  # a cada 0,5 s até o primeiro frame; o Wolf ignora os que sobram
                    pkt = ping_packet(secret, n // 5)
                    for ping_port in (self.video_ping_port, self.audio_ping_port):
                        try:
                            udp.sendto(pkt, (self.ping_host, ping_port))
                        except OSError as exc:
                            log.debug("Wolf: ping: %s", exc)
                n += 1
                self._stop.wait(0.1)
        if not self._stop.is_set():
            log.info("Wolf: nenhum frame com a conversão %s%s", choice,
                     f" ({receiver.failed})" if receiver.failed else "")
        return False

    def _watch(self, target: Target) -> bool:
        """Até o alvo sumir ou mudar, a sessão sumir ou o vídeo parar."""
        errors = 0
        while not self._stop.wait(self.poll_s):
            receiver = self._receiver
            if receiver is not None and receiver.failed:
                return False
            try:
                lobbies, sessions = self.api.lobbies(), self.api.sessions()
            except WolfApiError as exc:
                errors += 1
                if errors >= 3:
                    log.warning("Wolf: %s; recriando a sessão quando o Wolf responder", exc)
                    return False
                continue
            errors = 0
            if not any(str(s.get("client_id")) == self.session_id for s in sessions):
                log.warning("Wolf: a sessão %s sumiu (o Wolf reiniciou?); criando outra", self.session_id)
                return True
            now, _ = resolve_target(target.id, lobbies, sessions, own=self.session_id)
            if now is None:
                log.info("Wolf: %s fechou; esperando", target.describe())
                return True
            if now.producer != target.producer:
                log.info("Wolf: %s mudou de lobby; refazendo a sessão", target.describe())
                return True
        return True

    def _teardown(self) -> None:
        receiver, sid = self._receiver, self.session_id
        if receiver is not None:
            receiver.closing = True
        if sid is not None:
            self.session_id = None
            try:
                self.api.stop_session(sid)
            except WolfApiError as exc:
                log.warning("Wolf: não consegui encerrar a sessão %s: %s", sid, exc)
        if receiver is not None:
            deadline = time.monotonic() + 1.0  # o Wolf manda EOS e fecha a conexão
            while not receiver.failed and time.monotonic() < deadline:
                time.sleep(0.05)
            receiver.stop()
            self._receiver = None
        self.target = None
