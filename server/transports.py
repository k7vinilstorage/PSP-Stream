"""Transportes do PSPStream: TCP e UDP, ambos no modelo pull.

O transporte só entrega pedidos à sessão (`session.on_request`) e envia frames
(`send_frame`). Pull, estatísticas, controles e benchmark ficam na sessão.

UDP: cada frame vai em pedaços de 1400 bytes. Se faltar algum, o PSP pede de
novo só os que faltam (NACK). Isso evita que um pacote perdido no fim do frame
trave o stream esperando a retransmissão do TCP, e não há ACK para cada
segmento ocupando o rádio do 802.11b.
"""
import heapq
import logging
import socket
import threading
import time
from collections import OrderedDict

import protocol

log = logging.getLogger("pspstream.transport")

# Sem nenhuma mensagem do PSP por este tempo, a sessão é dada como morta.
IDLE_TIMEOUT_S = 10.0
# Frame enviado há menos que isso ainda pode estar no ar: não reenvia. Tem
# folga sobre a cópia do pedido de frame P (6 ms depois do original, ver
# REQ_DUP_US no stream.c), que não pode virar reenvio.
RETRY_GUARD_S = 0.015
# Frames P: o último pedaço de cada frame vai de novo depois disto. Perder o
# último pedaço é o caso lento do NACK: sem pedaço seguinte, o PSP só nota a
# falta pelo silêncio (>= 20 ms) e o reenvio leva mais uma ida e volta, com o
# stream parado (um P precisa do anterior). Um frame pequeno é um pedaço só,
# então a cópia cobre também o frame perdido inteiro. Um pedaço do meio
# perdido o PSP nota quando o último chega, e o NACK resolve em uma ida e
# volta. A espera tira a cópia da mesma rajada de interferência.
REDUNDANCY_S = 0.006


def recv_exact(conn: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("PSP fechou a conexão")
        buf += chunk
    return bytes(buf)


DSCP = {"ef": 0xB8, "cs5": 0xA0, "af41": 0x88, "0": 0}


def set_dscp(sock: socket.socket, name: str) -> None:
    """Marca os pacotes do servidor (WMM): com EF, a placa Wi-Fi do PC e o
    roteador usam a fila de voz, que disputa o ar com prioridade."""
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP[name])
    except OSError as exc:
        log.debug("DSCP não aplicado: %s", exc)


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

    def send_frame(self, frame_no: int, jpeg: bytes, send_ms: int, redundant: bool = False) -> int:
        """Devolve os bytes de imagem enviados (redundant: só no UDP)."""
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

    def __init__(self, sock: socket.socket, addr, pace_kbps: float = 0, hdr_cache: bool = True,
                 redundancy_s: float = REDUNDANCY_S):
        self.sock = sock  # socket UDP do servidor, compartilhado
        self.addr = addr
        self.pace = pace_kbps * 1024  # bytes/s; 0 = sem limite
        self.recent = OrderedDict()   # frame_no -> (payload, send_ms, hdr, enviado em), para reenviar pedaços
        self.hdr_cache = hdr_cache
        self.psp_hdr = 0              # id do cabeçalho JPEG que o PSP diz ter guardado
        self.hdr_saved = 0            # bytes de cabeçalho que não precisaram ir
        self.lock = threading.Lock()  # sendto da thread de envio e da de NACK
        self.last_seen = time.monotonic()
        self.sent_chunks = 0
        self.resent_chunks = 0
        self.retry_resends = 0        # frames reenviados inteiros por pedido repetido (frames P)
        self.redundancy_s = redundancy_s
        self.redundant_chunks = 0     # cópias do último pedaço (frames P)
        self._later = []              # (horário, n, datagrama): cópias esperando a vez
        self._later_n = 0
        self._later_cv = threading.Condition()
        self.session = None           # definido pela Session, antes do primeiro pedido

    def start(self, session) -> None:
        threading.Thread(target=self._watchdog, name="udp-watchdog", daemon=True).start()
        if self.redundancy_s > 0:
            threading.Thread(target=self._send_later_loop, name="udp-redundancy", daemon=True).start()

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
        if nack is not None and req.flags & protocol.REQ_FRAME:
            # Pedido repetido com o NACK do frame esperado (frames P): se esse
            # frame já saiu, ele se perdeu inteiro; reenvia o mesmo em vez de
            # um novo, que chegaria sem a referência e custaria um IDR.
            entry = self.recent.get(nack[0])
            if entry is not None:
                if time.monotonic() - entry[3] > RETRY_GUARD_S:  # senão ainda está a caminho
                    self._resend(*nack)
                    self.retry_resends += 1
                req.flags &= ~protocol.REQ_FRAME  # o pedido já foi atendido
            else:
                req.want_frame = nack[0]  # frames P: o PSP aceita até este frame (Session.on_request)
        elif nack is not None:
            self._resend(*nack)
        self.session.on_request(req)

    def _send(self, datagram: bytes) -> None:
        with self.lock:
            self.sock.sendto(datagram, self.addr)
        if self.pace:
            time.sleep(len(datagram) / self.pace)

    def send_frame(self, frame_no: int, jpeg: bytes, send_ms: int, redundant: bool = False) -> int:
        """Envia em pedaços. Se o PSP já tem o cabeçalho deste JPEG (mesma
        qualidade), vai só o resto. redundant (frames P): o último pedaço vai
        de novo depois de redundancy_s. Devolve os bytes de imagem enviados."""
        payload, hdr = jpeg, 0
        n = protocol.jpeg_header_len(jpeg) if self.hdr_cache else 0
        if n:
            hdr = protocol.jpeg_header_id(jpeg[:n])
            if hdr == self.psp_hdr:
                payload, hdr = jpeg[n:], hdr | protocol.HDR_STRIPPED
                self.hdr_saved += n
        self.recent[frame_no] = (payload, send_ms, hdr, time.monotonic())
        while len(self.recent) > 4:
            self.recent.popitem(last=False)
        count = protocol.chunk_count(len(payload))
        for i in range(count):
            self._send(protocol.pack_chunk(frame_no, payload, send_ms, i, hdr))
        self.sent_chunks += count
        if redundant and self.redundancy_s > 0:
            self._send_later(protocol.pack_chunk(frame_no, payload, send_ms, count - 1, hdr))
        return len(payload)

    def _send_later(self, datagram: bytes) -> None:
        with self._later_cv:
            self._later_n += 1
            heapq.heappush(self._later, (time.monotonic() + self.redundancy_s, self._later_n, datagram))
            self._later_cv.notify()

    def _send_later_loop(self) -> None:
        while self.session.alive:
            with self._later_cv:
                if not self._later:
                    self._later_cv.wait(0.5)
                    continue
                left = self._later[0][0] - time.monotonic()
                if left > 0:
                    self._later_cv.wait(left)
                    continue
                _, _, datagram = heapq.heappop(self._later)
            self._send(datagram)
            self.redundant_chunks += 1

    def send_audio(self, datagram: bytes) -> int:
        """Pacote de som (protocol.pack_audio), da thread da captura."""
        self._send(datagram)
        return len(datagram)

    def _resend(self, frame_no: int, missing) -> None:
        entry = self.recent.get(frame_no)
        if entry is None:
            return  # frame antigo demais: o PSP vai desistir dele e pedir outro
        payload, send_ms, hdr, _ = entry
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
