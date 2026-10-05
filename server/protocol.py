"""Protocolo v5 do PSPStream (TCP ou UDP, little-endian). Ver docs/PROTOCOL.md.

Manter em sincronia com psp/src/protocol.h.
"""
import struct
import zlib
from dataclasses import dataclass

DEFAULT_PORT = 5123

MAGIC_REQ = b"PSC5"       # v5: pedido antecipado automático
MAGIC_REQ_OLD = (b"PSC1", b"PSC2", b"PSC3", b"PSC4")  # EBOOT antigo: recusado com mensagem clara
MAGIC_FRAME = b"PSF1"
MAGIC_CHUNK = b"PSU2"
MAGIC_PONG = b"PSO1"
MAGIC_AUDIO = b"PSA1"     # UDP, PC -> PSP: um bloco de som (empurrado, sem pedido)

MAX_JPEG = 256 * 1024

REQ_FRAME = 0x0001
REQ_HELLO = 0x0002
REQ_NACK = 0x0004  # UDP: seguido de NACK_STRUCT (pedaços que faltam de um frame)
REQ_BYE = 0x0008   # UDP: o PSP está saindo
REQ_PING = 0x0010  # UDP: responda já com PONG_STRUCT (mede a ida e volta pura)
REQ_IDR = 0x0020   # H.264 com frames P: o PSP perdeu a corrente de referências e precisa de um IDR

# UDP: cada frame vai em pedaços de até CHUNK_PAYLOAD bytes (cabe num pacote
# de 1500 bytes com IP/UDP e o cabeçalho do pedaço).
CHUNK_PAYLOAD = 1400
MAX_CHUNKS = 256

REQ_STRUCT = struct.Struct("<4sIBBHIIHHHHHHBBHIHHHHhH")
FRAME_HDR_STRUCT = struct.Struct("<4sIII")
CHUNK_HDR_STRUCT = struct.Struct("<4sIIIHHI")  # magic, frame_no, size, send_ts, chunk, count, hdr
NACK_STRUCT = struct.Struct("<I8I")            # frame_no, máscara de 256 bits
PONG_STRUCT = struct.Struct("<4sI")            # magic, token (o echo_ts do ping)
# magic, seq, pos (amostra do 1º do bloco), taxa (Hz), canais, codec, amostras por canal, reservado
AUDIO_HDR_STRUCT = struct.Struct("<4sIIHBBHH")
assert REQ_STRUCT.size == 52
assert FRAME_HDR_STRUCT.size == 16
assert CHUNK_HDR_STRUCT.size == 24
assert NACK_STRUCT.size == 36
assert AUDIO_HDR_STRUCT.size == 20
assert (MAX_JPEG + CHUNK_PAYLOAD - 1) // CHUNK_PAYLOAD <= MAX_CHUNKS


class OldEbootError(ValueError):
    pass


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
    hdr_have: int = 0  # UDP: id do cabeçalho JPEG guardado no PSP (0 = nenhum)
    ping_select: int = 0  # 0,1 ms: ida e volta pura no início do stream, esperando com select()
    ping_poll: int = 0    # ... e consultando o socket a cada 0,5 ms (0 = não medido)
    ping_live: int = 0    # 0,1 ms: ping a cada 1 s durante o stream (média móvel; 0 = ainda não)
    ping_live_min: int = 0  # 0,1 ms: o menor dos últimos 8
    idle_t: int = -0x8000  # 0,1 ms: fim do frame anterior -> 1º pedaço deste no PSP (< 0 = em fila; IDLE_NONE)
    early_b: int = 0  # UDP: o PSP pede o próximo quando faltam estes bytes do atual (0 = só no fim)
    want_frame: int = 0  # não vai no pacote: frame pedido com FRAME + NACK (transports.UdpTransport.feed)

    def pack(self) -> bytes:
        return REQ_STRUCT.pack(
            MAGIC_REQ, self.buttons, self.lx, self.ly, self.flags,
            self.ack_frame, self.echo_ts,
            self.net_t, self.local_t, self.since_t, self.decode_t,
            self.first_t, self.burst_t, self.signal, self.wflags, self.lost,
            self.hdr_have, self.ping_select, self.ping_poll, self.ping_live, self.ping_live_min,
            self.idle_t, self.early_b,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "Request":
        if data[:4] in MAGIC_REQ_OLD:
            raise OldEbootError("o EBOOT do PSP é de uma versão antiga do protocolo "
                                f"(v{data[3] - 0x30}, este servidor fala v{MAGIC_REQ[3] - 0x30}): "
                                "compile e copie o EBOOT.PBP desta versão")
        if len(data) < REQ_STRUCT.size:
            raise ValueError(f"pedido curto: {len(data)} bytes (esperado {REQ_STRUCT.size})")
        magic, *fields = REQ_STRUCT.unpack(data[:REQ_STRUCT.size])
        if magic != MAGIC_REQ:
            raise ValueError(f"magic inválido no pedido: {magic!r}")
        return cls(*fields)


WIFI_POWER_SAVE = 0x01  # "Economia de energia WLAN" ligada no XMB
WIFI_RX_POLL = 0x02     # o PSP espera pacotes consultando o socket (não select())
CAP_H264 = 0x04         # o PSP decodifica H.264 (todo frame IDR) pelo hardware
CAP_H264P = 0x08        # ... e também frames P (pacote AUD + frame + 2 cópias, ver h264.H264PEncoder)
CAP_AUDIO = 0x10        # UDP: o PSP toca o som (pacotes MAGIC_AUDIO, ver audio.py)

CODEC_IMA_ADPCM = 1     # bloco IMA ADPCM do WAV (DVI, 4 bits por amostra; o adpcmenc do GStreamer)

IDLE_NONE = -0x8000     # idle_t: não medido (TCP, ou o primeiro frame)

# Campo hdr do pedaço UDP: bits 0-30 = id do cabeçalho deste JPEG, bit 31 = o
# cabeçalho foi tirado (o PSP põe de volta o que guardou com esse id).
HDR_STRIPPED = 0x80000000


def pack_frame_header(frame_no: int, size: int, send_ts: int) -> bytes:
    return FRAME_HDR_STRUCT.pack(MAGIC_FRAME, frame_no, size, send_ts & 0xFFFFFFFF)


def unpack_frame_header(data: bytes) -> tuple[int, int, int]:
    magic, frame_no, size, send_ts = FRAME_HDR_STRUCT.unpack(data)
    if magic != MAGIC_FRAME:
        raise ValueError(f"magic inválido no frame: {magic!r}")
    return frame_no, size, send_ts


def chunk_count(size: int) -> int:
    return (size + CHUNK_PAYLOAD - 1) // CHUNK_PAYLOAD


def pack_chunk(frame_no: int, payload: bytes, send_ts: int, index: int, hdr: int = 0) -> bytes:
    """Datagrama UDP com o pedaço `index` do frame (payload = JPEG, ou JPEG sem o cabeçalho)."""
    start = index * CHUNK_PAYLOAD
    return CHUNK_HDR_STRUCT.pack(MAGIC_CHUNK, frame_no, len(payload), send_ts & 0xFFFFFFFF,
                                 index, chunk_count(len(payload)), hdr) + payload[start:start + CHUNK_PAYLOAD]


def unpack_chunk(data: bytes):
    """Devolve (frame_no, size, send_ts, index, count, hdr, payload)."""
    magic, frame_no, size, send_ts, index, count, hdr = CHUNK_HDR_STRUCT.unpack_from(data)
    if magic != MAGIC_CHUNK:
        raise ValueError(f"magic inválido no pedaço: {magic!r}")
    return frame_no, size, send_ts, index, count, hdr, data[CHUNK_HDR_STRUCT.size:]


def pack_audio(seq: int, pos: int, rate: int, channels: int, samples: int, block: bytes,
               codec: int = CODEC_IMA_ADPCM) -> bytes:
    return AUDIO_HDR_STRUCT.pack(MAGIC_AUDIO, seq & 0xFFFFFFFF, pos & 0xFFFFFFFF, rate, channels, codec,
                                 samples, 0) + block


def unpack_audio(data: bytes):
    """Devolve (seq, pos, taxa, canais, codec, amostras, bloco)."""
    magic, seq, pos, rate, channels, codec, samples, _ = AUDIO_HDR_STRUCT.unpack_from(data)
    if magic != MAGIC_AUDIO:
        raise ValueError(f"magic inválido no som: {magic!r}")
    return seq, pos, rate, channels, codec, samples, data[AUDIO_HDR_STRUCT.size:]


def pack_pong(token: int) -> bytes:
    return PONG_STRUCT.pack(MAGIC_PONG, token & 0xFFFFFFFF)


def jpeg_header_len(jpeg: bytes) -> int:
    """Bytes do início do JPEG até o fim do segmento SOS (onde começam os dados
    comprimidos). Com o mesmo encoder, tamanho e qualidade, essa parte (~620
    bytes: tabelas de quantização e Huffman) é igual em todo frame. 0 se não achar."""
    if jpeg[:2] != b"\xff\xd8":
        return 0
    i, n = 2, len(jpeg)
    while i + 4 <= n:
        if jpeg[i] != 0xFF:
            return 0
        marker = jpeg[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        end = i + 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
        if marker == 0xDA:
            return end if end <= MAX_JPEG_HEADER and end < n else 0
        i = end
    return 0


MAX_JPEG_HEADER = 2048  # o PSP guarda até isso


def jpeg_header_id(header: bytes) -> int:
    """Id de 31 bits do cabeçalho (CRC32). Igual entre execuções do servidor, então
    o PSP nunca usa um cabeçalho guardado de outra qualidade com o mesmo id."""
    return (zlib.crc32(header) & 0x7FFFFFFF) or 1


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
