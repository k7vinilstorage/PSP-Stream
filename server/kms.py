"""Captura KMS (--source kms): a imagem que a placa de vídeo está mostrando.

No GNOME 50, a captura pelo portal fica em ~40 fps por causa do limitador do
mutter (wiki/Medições.md). O KMS não passa pelo compositor: o auxiliar
tools/kms/pspstream-kms (o único com CAP_SYS_ADMIN) exporta o buffer da tela
como DMA-BUF a cada quadro novo, e este processo, sem privilégio, reduz para
480x272 no OpenGL (o mesmo caminho do --dmabuf).

Diferenças para o portal: não pede permissão na tela, o cursor do mouse não
aparece (ele fica num plano separado da placa) e a captura é do monitor
inteiro.
"""
import logging
import os
import socket
import struct
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstAllocators", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstAllocators, GstVideo  # noqa: E402

from gst_source import GstSource  # noqa: E402
import paths  # noqa: E402

log = logging.getLogger("pspstream.kms")

HELPER = paths.kms_helper()  # repositório (tools/kms) ou pacote (/usr/libexec/pspstream)

REQUEST = struct.Struct("<4sII")                  # magic "PSKQ", cmd, timeout_ms
REPLY = struct.Struct("<4si4IQI4I4III160s")       # ver struct reply em pspstream-kms.c
ST_FRAME, ST_NOFRAME, ST_HELLO, ST_ERROR = 0, 1, 2, -1
CMD_FRAME, CMD_QUIT = 1, 2
MOD_LINEAR = 0
MOD_INVALID = 0x00FFFFFFFFFFFFFF                   # framebuffer sem modificador explícito


class KmsError(RuntimeError):
    pass


class Reply:
    def __init__(self, data: bytes):
        (magic, self.status, self.fb_id, self.width, self.height, self.fourcc, self.modifier, self.n_planes,
         *rest) = REPLY.unpack(data)
        if magic != b"PSK1":
            raise KmsError(f"resposta inválida do auxiliar: {magic!r}")
        self.pitches = list(rest[0:4])
        self.offsets = list(rest[4:8])
        self.refresh_mhz, self.crtc_id = rest[8], rest[9]
        self.msg = rest[10].split(b"\0", 1)[0].decode("utf-8", "replace")


def drm_format(fourcc: int, modifier: int) -> str:
    """Campo drm-format das caps do GStreamer: "XR24" ou "XR24:0x0100000000000002"."""
    name = fourcc.to_bytes(4, "little").decode("ascii", "replace")
    if modifier in (MOD_LINEAR, MOD_INVALID):
        return name
    return f"{name}:0x{modifier:016x}"


class KmsHelper:
    """Conversa com o pspstream-kms por um socketpair (o fd vai como argumento)."""

    def __init__(self, path=HELPER, card=None, monitor=0, argv_prefix=()):
        path = Path(path)
        self.path = path
        if not argv_prefix and not os.access(path, os.X_OK):
            raise KmsError(f"falta o auxiliar {path}: compile com make -C tools/kms e depois "
                           "make -C tools/kms cap (pede a senha do sudo), ou instale o pacote do PSPStream")
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        args = [*argv_prefix, str(path), str(theirs.fileno()), "--monitor", str(monitor)]
        if card:
            args += ["--card", card]
        self.proc = subprocess.Popen(args, pass_fds=(theirs.fileno(),), stdin=subprocess.DEVNULL)
        theirs.close()
        self.sock = ours
        self.lock = threading.Lock()
        hello, fds = self._recv(5.0)
        self._close_fds(fds)
        if hello.status == ST_ERROR:
            self.close()
            raise KmsError(self._explain(hello.msg, f" ({path})"))
        if hello.status != ST_HELLO:
            self.close()
            raise KmsError(f"o auxiliar não se apresentou (status {hello.status})")
        self.hello = hello

    @staticmethod
    def _close_fds(fds):
        for fd in fds:
            os.close(fd)

    def _recv(self, timeout):
        self.sock.settimeout(timeout)
        try:
            data, fds, _, _ = socket.recv_fds(self.sock, REPLY.size, 4)
        except socket.timeout:
            raise KmsError("o auxiliar não respondeu") from None
        if len(data) != REPLY.size:
            self._close_fds(fds)
            if not data:
                code = self.proc.poll()
                raise KmsError(f"o auxiliar saiu (código {code})")
            raise KmsError(f"resposta de {len(data)} bytes do auxiliar (esperado {REPLY.size})")
        return Reply(data), fds

    def next_frame(self, timeout_ms: int):
        """(Reply, fds): status ST_FRAME com um fd por plano, ou ST_NOFRAME sem quadro novo."""
        with self.lock:
            self.sock.send(REQUEST.pack(b"PSKQ", CMD_FRAME, timeout_ms))
            reply, fds = self._recv(timeout_ms / 1000 + 2.0)
        if reply.status == ST_ERROR:
            self._close_fds(fds)
            raise KmsError(self._explain(reply.msg))
        return reply, fds

    def _explain(self, msg: str, where: str = "") -> str:
        """Sem a permissão de ler a tela, o auxiliar só sabe dizer isso: aqui
        entram o arquivo e o comando certo (repositório ou pacote)."""
        if msg.startswith("sem permissão para ler a tela"):
            return f"sem permissão para ler a tela: {paths.kms_permission_problem(self.path)}"
        return msg + where

    def close(self):
        try:
            self.sock.send(REQUEST.pack(b"PSKQ", CMD_QUIT, 0))
        except OSError:
            pass
        self.sock.close()
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def make_buffer(reply: Reply, fds, allocator) -> Gst.Buffer:
    """GstBuffer com os planos do framebuffer como memória DMA-BUF e o GstVideoMeta
    (offsets e strides de cada plano). Planos no mesmo buffer da GPU (ex.: o plano
    de compressão da Intel) viram uma memória só."""
    buf = Gst.Buffer.new()
    seen = {}  # inode do dma-buf -> início dele no GstBuffer
    total = 0
    offsets = [0, 0, 0, 0]
    for i, fd in enumerate(fds[:reply.n_planes]):
        ino = os.fstat(fd).st_ino  # um inode por dma-buf: fds do mesmo buffer se repetem
        if ino in seen:
            os.close(fd)
        else:
            size = os.lseek(fd, 0, os.SEEK_END)
            mem = GstAllocators.DmaBufAllocator.alloc(allocator, fd, size)  # fica dono do fd
            buf.append_memory(mem)
            seen[ino] = total
            total += size
        offsets[i] = seen[ino] + reply.offsets[i]
    for fd in fds[reply.n_planes:]:
        os.close(fd)
    GstVideo.buffer_add_video_meta_full(buf, GstVideo.VideoFrameFlags.NONE, GstVideo.VideoFormat.DMA_DRM,
                                        reply.width, reply.height, reply.n_planes, offsets,
                                        reply.pitches)
    return buf


def caps_for(reply: Reply) -> Gst.Caps:
    return Gst.Caps.from_string(
        f"video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format={drm_format(reply.fourcc, reply.modifier)},"
        f"width={reply.width},height={reply.height},framerate=0/1,pixel-aspect-ratio=1/1")


class KmsSource(GstSource):
    """Pipeline do --dmabuf com um appsrc no lugar do pipewiresrc."""

    POLL_MS = 200  # espera por quadro novo em cada pedido ao auxiliar

    def __init__(self, width: int, height: int, fps: int, quality: int, scale: str = "bilinear",
                 keep_aspect: bool = True, codec: str = "jpeg", card=None, monitor: int = 0,
                 helper=HELPER, argv_prefix=()):
        self.helper = KmsHelper(helper, card, monitor, argv_prefix)
        hello = self.helper.hello
        log.info("KMS: %s", hello.msg)
        # Um quadro já na partida: sem o setcap, o erro aparece aqui e não com o
        # PSP conectado. Ele é o primeiro a ser enviado (com a tela parada, o
        # auxiliar só manda outro quando algo mudar).
        try:
            self._first = self.helper.next_frame(1000)
        except KmsError:
            self.helper.close()
            raise
        src = "appsrc name=kmssrc is-live=true do-timestamp=true format=time max-buffers=2 block=false"
        try:
            super().__init__(src, width, height, fps, quality, scale, keep_aspect, keepalive=self.helper,
                             codec=codec, gpu_from=(hello.width, hello.height))
        except Exception:
            self.helper.close()
            raise
        self.appsrc = self.pipeline.get_by_name("kmssrc")
        self.allocator = GstAllocators.DmaBufAllocator.new()
        self._caps = None
        self._size = (hello.width, hello.height)
        self._warned = set()

    def start(self) -> None:
        super().start()
        threading.Thread(target=self._capture, name="kms", daemon=True).start()

    def _capture(self) -> None:
        while not self._stop.is_set():
            try:
                if self._first is not None:
                    (reply, fds), self._first = self._first, None
                else:
                    reply, fds = self.helper.next_frame(self.POLL_MS)
            except (KmsError, OSError) as exc:
                if not self._stop.is_set():
                    self.failed = f"captura KMS: {exc}"
                    log.error("%s", self.failed)
                return
            if reply.status != ST_FRAME:
                continue
            if reply.modifier == MOD_INVALID and "mod" not in self._warned:
                self._warned.add("mod")
                log.warning("KMS: o framebuffer não informa o modificador; tratando como linear")
            if (reply.width, reply.height) != self._size and "size" not in self._warned:
                self._warned.add("size")
                log.warning("KMS: a tela mudou para %dx%d; reinicie o servidor para a proporção certa",
                            reply.width, reply.height)
            key = (reply.fourcc, reply.modifier, reply.width, reply.height)
            if key != self._caps:
                self._caps = key
                self.appsrc.set_property("caps", caps_for(reply))
                log.info("KMS: formato %s, %dx%d, %d plano(s)", drm_format(reply.fourcc, reply.modifier),
                         reply.width, reply.height, reply.n_planes)
            self.appsrc.emit("push-buffer", make_buffer(reply, fds, self.allocator))

    def stop(self) -> None:
        super().stop()
        self.helper.close()
