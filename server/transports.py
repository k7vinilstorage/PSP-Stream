"""Transportes do PSPStream: TCP e UDP, ambos no modelo pull.

O transporte só entrega pedidos à sessão (`session.on_request`) e envia frames
(`send_frame`). Pull, estatísticas, controles e benchmark ficam na sessão.

UDP: cada frame vai em pedaços de 1400 bytes. Se faltar algum, o PSP pede de
novo só os que faltam (NACK). Isso evita que um pacote perdido no fim do frame
trave o stream esperando a retransmissão do TCP, e não há ACK para cada
segmento ocupando o rádio do 802.11b.
"""
import logging
import socket
import threading
import time
from collections import OrderedDict

import protocol

log = logging.getLogger("pspstream.transport")

# Sem nenhuma mensagem do PSP por este tempo, a sessão é dada como morta.
IDLE_TIMEOUT_S = 10.0


def recv_exact(conn: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("PSP fechou a conexão")
        buf += chunk
    return bytes(buf)


class TcpTransport:
    name = "tcp"

    def __init__(self, conn: socket.socket, addr):
        self.conn = conn
        self.addr = addr
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(IDLE_TIMEOUT_S)
        self.resent_chunks = 0  # sempre 0 (o TCP retransmite sozinho)
        self.sent_chunks = 0
        self.session = None     # definido pela Session

    def start(self, session) -> None:
        threading.Thread(target=self._reader, args=(session,), name="tcp-reader", daemon=True).start()

    def _reader(self, session) -> None:
        try:
            while session.alive:
                session.on_request(protocol.Request.unpack(recv_exact(self.conn, protocol.REQ_STRUCT.size)))
        except socket.timeout:
            log.info("PSP ficou %.0f s sem responder", IDLE_TIMEOUT_S)
        except (OSError, ConnectionError, ValueError) as exc:
            if session.alive:
                log.info("leitura terminou: %s", exc)
        finally:
            session.close()

    def send_frame(self, frame_no: int, jpeg: bytes, send_ms: int) -> int:
        """Devolve os bytes de imagem enviados."""
        self.conn.sendall(protocol.pack_frame_header(frame_no, len(jpeg), send_ms) + jpeg)
        return len(jpeg)

    def close(self) -> None:
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()


class UdpTransport:
    name = "udp"

    def __init__(self, sock: socket.socket, addr, pace_kbps: float = 0, hdr_cache: bool = True):
        self.sock = sock  # socket UDP do servidor, compartilhado
        self.addr = addr
        self.pace = pace_kbps * 1024  # bytes/s; 0 = sem limite
        self.recent = OrderedDict()   # frame_no -> (payload, send_ms, hdr), para reenviar pedaços
        self.hdr_cache = hdr_cache
        self.psp_hdr = 0              # id do cabeçalho JPEG que o PSP diz ter guardado
        self.hdr_saved = 0            # bytes de cabeçalho que não precisaram ir
        self.lock = threading.Lock()  # sendto da thread de envio e da de NACK
        self.last_seen = time.monotonic()
        self.sent_chunks = 0
        self.resent_chunks = 0
        self.session = None           # definido pela Session, antes do primeiro pedido

    def start(self, session) -> None:
        threading.Thread(target=self._watchdog, name="udp-watchdog", daemon=True).start()

    def _watchdog(self) -> None:
        while self.session.alive:
            time.sleep(0.5)
            if time.monotonic() - self.last_seen > IDLE_TIMEOUT_S:
                log.info("PSP ficou %.0f s sem responder", IDLE_TIMEOUT_S)
                self.session.close()

    def feed(self, req: protocol.Request, nack=None) -> None:
        """Chamado pela thread que lê o socket UDP do servidor."""
        self.last_seen = time.monotonic()
        self.psp_hdr = req.hdr_have
        if nack is not None:
            self._resend(*nack)
        self.session.on_request(req)

    def _send(self, datagram: bytes) -> None:
        with self.lock:
            self.sock.sendto(datagram, self.addr)
        if self.pace:
            time.sleep(len(datagram) / self.pace)

    def send_frame(self, frame_no: int, jpeg: bytes, send_ms: int) -> int:
        """Envia em pedaços. Se o PSP já tem o cabeçalho deste JPEG (mesma
        qualidade), vai só o resto. Devolve os bytes de imagem enviados."""
        payload, hdr = jpeg, 0
        n = protocol.jpeg_header_len(jpeg) if self.hdr_cache else 0
        if n:
            hdr = protocol.jpeg_header_id(jpeg[:n])
            if hdr == self.psp_hdr:
                payload, hdr = jpeg[n:], hdr | protocol.HDR_STRIPPED
                self.hdr_saved += n
        self.recent[frame_no] = (payload, send_ms, hdr)
        while len(self.recent) > 4:
            self.recent.popitem(last=False)
        count = protocol.chunk_count(len(payload))
        for i in range(count):
            self._send(protocol.pack_chunk(frame_no, payload, send_ms, i, hdr))
        self.sent_chunks += count
        return len(payload)

    def _resend(self, frame_no: int, missing) -> None:
        entry = self.recent.get(frame_no)
        if entry is None:
            return  # frame antigo demais: o PSP vai desistir dele e pedir outro
        payload, send_ms, hdr = entry
        count = protocol.chunk_count(len(payload))
        for i in missing:
            if i < count:
                self._send(protocol.pack_chunk(frame_no, payload, send_ms, i, hdr))
                self.resent_chunks += 1

    def close(self) -> None:
        pass  # o socket é do servidor


def parse_datagram(data: bytes):
    """Pedido UDP do PSP -> (Request, (frame_no, faltando) ou None)."""
    req = protocol.Request.unpack(data[:protocol.REQ_STRUCT.size])
    nack = None
    if req.flags & protocol.REQ_NACK and len(data) >= protocol.REQ_STRUCT.size + protocol.NACK_STRUCT.size:
        nack = protocol.unpack_nack(data[protocol.REQ_STRUCT.size:])
    return req, nack
