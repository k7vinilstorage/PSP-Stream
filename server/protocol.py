"""Protocolo v1 do PSPStream (TCP, little-endian). Ver docs/PROTOCOL.md.

Manter em sincronia com psp/src/protocol.h.
"""
import struct
from dataclasses import dataclass

DEFAULT_PORT = 5123

MAGIC_REQ = b"PSC1"
MAGIC_FRAME = b"PSF1"

MAX_JPEG = 256 * 1024

REQ_FRAME = 0x0001
REQ_HELLO = 0x0002

REQ_STRUCT = struct.Struct("<4sIBBHIIHHHH")
FRAME_HDR_STRUCT = struct.Struct("<4sIII")
assert REQ_STRUCT.size == 28
assert FRAME_HDR_STRUCT.size == 16


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

    def pack(self) -> bytes:
        return REQ_STRUCT.pack(
            MAGIC_REQ, self.buttons, self.lx, self.ly, self.flags,
            self.ack_frame, self.echo_ts,
            self.net_t, self.local_t, self.since_t, self.decode_t,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "Request":
        magic, *fields = REQ_STRUCT.unpack(data)
        if magic != MAGIC_REQ:
            raise ValueError(f"magic inválido no pedido: {magic!r}")
        return cls(*fields)


def pack_frame_header(frame_no: int, size: int, send_ts: int) -> bytes:
    return FRAME_HDR_STRUCT.pack(MAGIC_FRAME, frame_no, size, send_ts & 0xFFFFFFFF)


def unpack_frame_header(data: bytes) -> tuple[int, int, int]:
    magic, frame_no, size, send_ts = FRAME_HDR_STRUCT.unpack(data)
    if magic != MAGIC_FRAME:
        raise ValueError(f"magic inválido no frame: {magic!r}")
    return frame_no, size, send_ts


def clamp_u16(value: float) -> int:
    return max(0, min(0xFFFF, int(value)))
