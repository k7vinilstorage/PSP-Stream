"""Protocolo v1 do PSPStream (TCP ou UDP, little-endian). Ver docs/PROTOCOL.md.

Manter em sincronia com psp/src/protocol.h.
"""
import struct
from dataclasses import dataclass

DEFAULT_PORT = 5123

MAGIC_REQ = b"PSC2"       # v2: campos de diagnóstico no fim do pedido
MAGIC_REQ_V1 = b"PSC1"    # EBOOT antigo: recusado com mensagem clara
MAGIC_FRAME = b"PSF1"
MAGIC_CHUNK = b"PSU1"

MAX_JPEG = 256 * 1024

REQ_FRAME = 0x0001
REQ_HELLO = 0x0002
REQ_NACK = 0x0004  # UDP: seguido de NACK_STRUCT (pedaços que faltam de um frame)
REQ_BYE = 0x0008   # UDP: o PSP está saindo

# UDP: cada frame vai em pedaços de até CHUNK_PAYLOAD bytes (cabe num pacote
# de 1500 bytes com IP/UDP e o cabeçalho do pedaço).
CHUNK_PAYLOAD = 1400
MAX_CHUNKS = 256

REQ_STRUCT = struct.Struct("<4sIBBHIIHHHHHHBBH")
FRAME_HDR_STRUCT = struct.Struct("<4sIII")
CHUNK_HDR_STRUCT = struct.Struct("<4sIIIHH")  # magic, frame_no, size, send_ts, chunk, count
NACK_STRUCT = struct.Struct("<I8I")            # frame_no, máscara de 256 bits
assert REQ_STRUCT.size == 36
assert FRAME_HDR_STRUCT.size == 16
assert CHUNK_HDR_STRUCT.size == 20
assert NACK_STRUCT.size == 36
assert (MAX_JPEG + CHUNK_PAYLOAD - 1) // CHUNK_PAYLOAD <= MAX_CHUNKS


@dataclass
class Request:
    buttons: int = 0
    lx: int = 128
    ly: int = 128
    flags: int = 0
    ack_frame: int = 0
    echo_ts: int = 0
    net_t: int = 0  # unidades de 0,1 ms
    local_t: int = 0
    since_t: int = 0
    decode_t: int = 0
    first_t: int = 0   # 0,1 ms: pedido -> primeiro pedaço (ida e volta)
    burst_t: int = 0   # 0,1 ms: primeiro -> último pedaço
    signal: int = 0    # sinal do Wi-Fi do PSP, %
    wflags: int = 0    # WIFI_*
    lost: int = 0      # UDP: frames abandonados desde o início do stream

    def pack(self) -> bytes:
        return REQ_STRUCT.pack(
            MAGIC_REQ, self.buttons, self.lx, self.ly, self.flags,
            self.ack_frame, self.echo_ts,
            self.net_t, self.local_t, self.since_t, self.decode_t,
            self.first_t, self.burst_t, self.signal, self.wflags, self.lost,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "Request":
        if data[:4] == MAGIC_REQ_V1:
            raise ValueError("o EBOOT do PSP é de uma versão antiga do protocolo: atualize-o")
        magic, *fields = REQ_STRUCT.unpack(data)
        if magic != MAGIC_REQ:
            raise ValueError(f"magic inválido no pedido: {magic!r}")
        return cls(*fields)


WIFI_POWER_SAVE = 0x01


def pack_frame_header(frame_no: int, size: int, send_ts: int) -> bytes:
    return FRAME_HDR_STRUCT.pack(MAGIC_FRAME, frame_no, size, send_ts & 0xFFFFFFFF)


def unpack_frame_header(data: bytes) -> tuple[int, int, int]:
    magic, frame_no, size, send_ts = FRAME_HDR_STRUCT.unpack(data)
    if magic != MAGIC_FRAME:
        raise ValueError(f"magic inválido no frame: {magic!r}")
    return frame_no, size, send_ts


def chunk_count(size: int) -> int:
    return (size + CHUNK_PAYLOAD - 1) // CHUNK_PAYLOAD


def pack_chunk(frame_no: int, jpeg: bytes, send_ts: int, index: int) -> bytes:
    """Datagrama UDP com o pedaço `index` do frame."""
    start = index * CHUNK_PAYLOAD
    return CHUNK_HDR_STRUCT.pack(MAGIC_CHUNK, frame_no, len(jpeg), send_ts & 0xFFFFFFFF,
                                 index, chunk_count(len(jpeg))) + jpeg[start:start + CHUNK_PAYLOAD]


def unpack_chunk(data: bytes):
    """Devolve (frame_no, size, send_ts, index, count, payload)."""
    magic, frame_no, size, send_ts, index, count = CHUNK_HDR_STRUCT.unpack_from(data)
    if magic != MAGIC_CHUNK:
        raise ValueError(f"magic inválido no pedaço: {magic!r}")
    return frame_no, size, send_ts, index, count, data[CHUNK_HDR_STRUCT.size:]


def pack_nack(frame_no: int, missing) -> bytes:
    words = [0] * 8
    for i in missing:
        if 0 <= i < MAX_CHUNKS:
            words[i // 32] |= 1 << (i % 32)
    return NACK_STRUCT.pack(frame_no, *words)


def unpack_nack(data: bytes):
    """Devolve (frame_no, [índices que faltam])."""
    frame_no, *words = NACK_STRUCT.unpack_from(data)
    return frame_no, [w * 32 + b for w, word in enumerate(words) for b in range(32) if word >> b & 1]


def clamp_u16(value: float) -> int:
    return max(0, min(0xFFFF, int(value)))
