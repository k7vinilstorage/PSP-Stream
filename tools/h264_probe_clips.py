#!/usr/bin/env python3
"""Gera psp/probe/clips.bin: clipes H.264 curtos para o teste de decode no PSP.

Cada frame leva o próprio número (0-255) desenhado em 8 blocos de 32x32 no
canto superior esquerdo (branco = bit 1). Depois do decode, o PSP lê esses
blocos e sabe qual frame saiu do decoder. Se, depois de entregar o frame N,
sai o N-2, o decoder segura 2 frames, e isso é latência somada ao stream.

Precisa de ffmpeg com libx264 (no Fedora, o ffmpeg do RPM Fusion; o
ffmpeg-free não tem x264) e do openh264enc do GStreamer (o mesmo encoder do
servidor). O clips.bin gerado fica no repositório, então só é preciso rodar
isto para mudar os clipes.

Formato do clips.bin (little-endian):
    "H264PRB1", u32 clipes
    por clipe: char nome[24], u32 AUs, u32 bytes, u32 tamanho[AUs], dados,
               zeros até o próximo múltiplo de 4
Os dados são Annex B (start codes). Nos clipes com frames P, cada AU começa
com um AUD; nos de todo frame IDR do openh264 (como o --codec h264), não.
"""
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "psp" / "probe" / "clips.bin"
sys.path.insert(0, str(ROOT / "server"))

W, H = 480, 272
FRAMES = 60

# Conteúdo com movimento (testsrc2) + o número do frame em binário.
MARKER = ",".join(
    ["drawbox=x=0:y=0:w=256:h=32:color=black:t=fill"]
    + [f"drawbox=x={k * 32}:y=0:w=32:h=32:color=white:t=fill:enable='eq(mod(floor(n/{1 << k}),2),1)'"
       for k in range(8)]
)

# O que o stream usaria: sem B-frames, 1 referência, sem lookahead, 1 slice.
COMMON = "ref=1:bframes=0:threads=1:sliced-threads=0:rc-lookahead=0:sync-lookahead=0:aud=1:" \
         "scenecut=0:weightp=0:repeat-headers=1:cabac=0"

# Teste v4: o --codec h264p (openh264, AUD + frame + 2 cópias) desligou o PSP.
# O teste v2 (x264, frame + 2 cópias) tinha funcionado. As diferenças entre os
# dois viram passos separados, do mais seguro para o mais arriscado:
#   - x264_dup3_l30: o clipe da v2 (controle);
#   - x264_dup3_l41: o mesmo com level_idc 41 (o openh264 escreve 4.1);
#   - oh_intra: openh264, todo frame IDR (o --codec h264, que funciona);
#   - oh_p1_l30: openh264 IPPP, uma chamada por frame, nível 3.0;
#   - oh_p3_l30: openh264 + 2 cópias, nível 3.0;
#   - oh_p3_l41: openh264 + 2 cópias, nível 4.1: o que o stream mandou.
# Os 6 passaram no PSP-3000 (60 frames cada). v4.1, passo 7:
#   - oh_long: openh264 + 2 cópias, 12000 frames sem IDR (36000 AUs). O
#     frame_num (15 bits) dá a volta em 32768 AUs e o POC (16 bits, +2 por
#     AU) também: no stream, ~3 min a 60 fps. Vídeo de PSP nunca chega lá,
#     porque cada IDR zera os dois. Conteúdo quase parado (fundo cinza, o
#     número e um quadrado andando) para caber no EBOOT.
#   - oh_idr30: openh264 + 2 cópias com IDR nos frames 0 e 30. A sonda pula os
#     frames 20-29, como o stream depois de uma perda (pula os P até o IDR
#     pedido chegar), e o IDR entra sem Stop.
# Na v4.1 o passo 7 passou (a volta dos contadores não é o problema) e o 8
# DESLIGOU o PSP: IDR no meio de uma sequência de P, sem Stop. v4.2:
#   - oh_idr10: openh264 + 2 cópias, 120 frames, IDR a cada 10. A sonda chama
#     Stop antes de cada IDR (é a correção do stream) e, num passo, pula os
#     frames 15-19 antes do IDR do 20.
# Além do nível, o openh264 usa frame_num de 15 bits e POC tipo 0 (16 bits);
# o x264 sem B-frames usa 4 bits e POC tipo 2. E as cópias do openh264 têm
# ~20 bytes, contra ~300 no clipe do x264.
X264_DUP3 = ["-profile:v", "baseline", "-x264-params", COMMON + ":keyint=600:min-keyint=600"]
QUALITY = 90  # o adaptativo ficava em q90 no PSP do usuário


def x264(repeat, level, args) -> bytes:
    # o número é desenhado antes de repetir: as 3 cópias levam o mesmo número
    vf = MARKER if repeat == 1 else f"{MARKER},fps={30 * repeat}"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30",
           "-vf", vf, "-frames:v", str(FRAMES * repeat), "-pix_fmt", "yuv420p", "-c:v", "libx264",
           "-tune", "zerolatency", "-crf", "24", "-level", level, *args, "-f", "h264", "-"]
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def raw_frames(count=FRAMES) -> list[bytes]:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30",
           "-vf", MARKER, "-frames:v", str(count), "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"]
    data = subprocess.run(cmd, check=True, capture_output=True).stdout
    size = W * H * 3 // 2
    return [data[i:i + size] for i in range(0, len(data), size)]


LONG_FRAMES = 12000


def raw_long():
    """Frames crus do clipe longo, um por vez (12000 de uma vez não cabem na memória)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"color=c=0x404040:size={W}x{H}:rate=30",
           "-vf", f"{MARKER},drawbox=x='mod(t*120\\,400)':y=120:w=48:h=48:color=red:t=fill",
           "-frames:v", str(LONG_FRAMES), "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"]
    size = W * H * 3 // 2
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        while True:
            frame = proc.stdout.read(size)
            if len(frame) < size:
                break
            yield frame
    if proc.returncode:
        raise RuntimeError(f"ffmpeg saiu com {proc.returncode}")


def openh264(frames, copies=None, level=None, idr_every=0) -> bytes:
    """copies=None: todo frame IDR (H264Encoder); senão H264PEncoder com tantas cópias
    (por padrão sem o IDR periódico do stream, para os contadores darem a volta
    no clipe longo)."""
    import h264
    if copies is None:
        enc = h264.H264Encoder(W, H, QUALITY)
    else:
        enc = h264.H264PEncoder(W, H, QUALITY, copies=copies, idr_every=idr_every)
    try:
        out = b"".join(enc.encode(f) for f in frames)
    finally:
        enc.close()
    return h264.set_sps_level(out, level) if level else out


def split_aus(stream: bytes, marker: bytes) -> list[bytes]:
    """Corta em `marker` (o AUD, ou o start code do SPS no openh264 intra)."""
    starts = []
    i = stream.find(marker)
    while i >= 0:
        starts.append(i)
        i = stream.find(marker, i + 1)
    if not starts or starts[0] != 0:
        raise ValueError("o stream não começa com o separador esperado")
    return [stream[a:b] for a, b in zip(starts, starts[1:] + [len(stream)])]


AUD = b"\x00\x00\x00\x01\x09"
SPS = b"\x00\x00\x00\x01\x67"


def main() -> int:
    frames = raw_frames()
    if len(frames) != FRAMES:
        raise ValueError(f"{len(frames)} frames crus, esperava {FRAMES}")
    clips = [
        ("x264_dup3_l30", 3, split_aus(x264(3, "3.0", X264_DUP3), AUD)),
        ("x264_dup3_l41", 3, split_aus(x264(3, "4.1", X264_DUP3), AUD)),
        ("oh_intra", 1, split_aus(openh264(frames), SPS)),
        ("oh_p1_l30", 1, split_aus(openh264(frames, copies=0, level=30), AUD)),
        ("oh_p3_l30", 3, split_aus(openh264(frames, copies=2, level=30), AUD)),
        ("oh_p3_l41", 3, split_aus(openh264(frames, copies=2), AUD)),
    ]
    clips.append(("oh_long", 3, split_aus(openh264(raw_long(), copies=2), AUD)))
    clips.append(("oh_idr30", 3, split_aus(openh264(frames, copies=2, idr_every=30), AUD)))
    clips.append(("oh_idr10", 3, split_aus(openh264(raw_frames(2 * FRAMES), copies=2, idr_every=10), AUD)))
    out = bytearray(b"H264PRB1" + struct.pack("<I", len(clips)))
    for name, group, aus in clips:
        frames_in = {"oh_long": LONG_FRAMES, "oh_idr10": 2 * FRAMES}.get(name, FRAMES)
        if len(aus) != frames_in * group:
            raise ValueError(f"{name}: {len(aus)} AUs, esperava {frames_in * group}")
        if group > 1:
            dups = [len(a) for i, a in enumerate(aus) if i % group]
            print(f"  cópias: {sum(dups) / len(dups):.0f} bytes em média, mín. {min(dups)}, máx. {max(dups)}")
        data = b"".join(aus)
        out += name.encode().ljust(24, b"\0")
        out += struct.pack(f"<II{len(aus)}I", len(aus), len(data), *(len(a) for a in aus))
        out += data
        out += bytes(-len(out) % 4)  # o próximo clipe começa alinhado
        print(f"{name}: {len(aus)} AUs, {len(data) / frames_in / 1024:.1f} KB por frame mostrado, "
              f"1º (IDR) {len(aus[0]) / 1024:.1f} KB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(out)
    print(f"{OUT}: {len(out) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
