#!/usr/bin/env python3
"""Gera psp/probe/clips.bin: clipes H.264 curtos para o teste de decode no PSP.

Cada frame leva o próprio número (0-255) desenhado em 8 blocos de 32x32 no
canto superior esquerdo (branco = bit 1). Depois do decode, o PSP lê esses
blocos e sabe qual frame saiu do decoder. Se, depois de entregar o frame N,
sai o N-2, o decoder segura 2 frames, e isso é latência somada ao stream.

Precisa de ffmpeg com libx264 (no Fedora, o ffmpeg do RPM Fusion; o
ffmpeg-free não tem x264). O clips.bin gerado fica no repositório, então só é
preciso rodar isto para mudar os clipes.

Formato do clips.bin (little-endian):
    "H264PRB1", u32 clipes
    por clipe: char nome[24], u32 AUs, u32 bytes, u32 tamanho[AUs], dados,
               zeros até o próximo múltiplo de 4
Os dados são Annex B (start codes); cada AU começa com um AUD.
"""
import struct
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "psp" / "probe" / "clips.bin"

# Conteúdo com movimento (testsrc2) + o número do frame em binário.
MARKER = ",".join(
    ["drawbox=x=0:y=0:w=256:h=32:color=black:t=fill"]
    + [f"drawbox=x={k * 32}:y=0:w=32:h=32:color=white:t=fill:enable='eq(mod(floor(n/{1 << k}),2),1)'"
       for k in range(8)]
)

# O que o stream usaria: sem B-frames, 1 referência, sem lookahead, 1 slice.
COMMON = "ref=1:bframes=0:threads=1:sliced-threads=0:rc-lookahead=0:sync-lookahead=0:aud=1:" \
         "keyint=60:min-keyint=60:scenecut=0:weightp=0:repeat-headers=1"

BASELINE = ["-profile:v", "baseline", "-x264-params", COMMON + ":cabac=0"]

# (nome, frames, repetições de cada frame, argumentos do x264)
# - baseline_cavlc: referência (I + P), o decoder segura 2 frames.
# - *_intra: todo frame é IDR. No teste v2, sceMpegAvcDecodeStop soltou o
#   frame na hora (1,1 ms), mas zerou as referências e os P seguintes saíram
#   errados. Sem P, o Stop não teria o que quebrar.
# - baseline_dup3 (v2, fora do clips.bin atual): cada frame 3 vezes; atraso 0,
#   mas 3 chamadas de ~4 ms por frame.
INTRA = ":keyint=1:min-keyint=1"
CLIPS = [
    ("baseline_cavlc", 60, 1, BASELINE),
    ("baseline_intra", 30, 1, ["-profile:v", "baseline", "-x264-params", COMMON + ":cabac=0" + INTRA]),
    ("main_intra", 30, 1, ["-profile:v", "main", "-x264-params", COMMON + ":cabac=1" + INTRA]),
]


def encode(frames, repeat, args) -> bytes:
    # o número é desenhado antes de repetir: as 3 cópias levam o mesmo número
    vf = MARKER if repeat == 1 else f"{MARKER},fps={30 * repeat}"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=size=480x272:rate=30",
           "-vf", vf, "-frames:v", str(frames * repeat), "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-tune", "zerolatency", "-crf", "24", "-level", "3.0", *args, "-f", "h264", "-"]
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def split_aus(stream: bytes) -> list[bytes]:
    """Corta nos AUDs (00 00 00 01 09)."""
    aud = b"\x00\x00\x00\x01\x09"
    starts = []
    i = stream.find(aud)
    while i >= 0:
        starts.append(i)
        i = stream.find(aud, i + 1)
    if not starts or starts[0] != 0:
        raise ValueError("o stream não começa com AUD (x264 sem aud=1?)")
    return [stream[a:b] for a, b in zip(starts, starts[1:] + [len(stream)])]


def main() -> int:
    out = bytearray(b"H264PRB1" + struct.pack("<I", len(CLIPS)))
    for name, frames, repeat, args in CLIPS:
        aus = split_aus(encode(frames, repeat, args))
        if len(aus) != frames * repeat:
            raise ValueError(f"{name}: {len(aus)} AUs, esperava {frames * repeat}")
        if repeat > 1:
            dups = [len(a) for i, a in enumerate(aus) if i % repeat]
            print(f"  cópias: {sum(dups) / len(dups):.0f} bytes em média, máx. {max(dups)}")
        data = b"".join(aus)
        out += name.encode().ljust(24, b"\0")
        out += struct.pack(f"<II{len(aus)}I", len(aus), len(data), *(len(a) for a in aus))
        out += data
        out += bytes(-len(out) % 4)  # o próximo clipe começa alinhado
        print(f"{name}: {len(aus)} frames, {len(data) / len(aus) / 1024:.1f} KB/frame em média, "
              f"1º (IDR) {len(aus[0]) / 1024:.1f} KB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(out)
    print(f"{OUT}: {len(out) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
