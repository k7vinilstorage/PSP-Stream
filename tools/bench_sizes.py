#!/usr/bin/env python3
"""Mede KB/frame do JPEG que o servidor geraria para um conjunto de imagens.

Usa o mesmo caminho do servidor (videoscale -> I420 -> jpegenc) e imprime
uma tabela Markdown por grupo de imagens, com a projeção de FPS limitada pela
rede para algumas vazões do Wi-Fi do PSP. A projeção NÃO inclui o decode no PSP
nem perdas no Wi-Fi: é o teto imposto só pela banda.

  python3 tools/bench_sizes.py --group jogos jogos/*.png --group desktop desk/*.png
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from gst_source import transcode_image  # noqa: E402

OVERHEAD = 16 + 40 * 1.0 / 1460  # cabeçalho do frame + TCP/IP por segmento (aprox.)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--group", nargs="+", action="append", metavar=("NAME", "IMAGE"), required=True,
                   help="group name followed by the images")
    p.add_argument("--qualities", default="30,40,50,60,70,80,90")
    p.add_argument("--size", default="480x272")
    p.add_argument("--scale", default="bilinear")
    p.add_argument("--kbps", default="300,400,500", help="throughputs (KB/s) for the FPS projection")
    args = p.parse_args(argv)

    w, h = (int(v) for v in args.size.split("x"))
    qualities = [int(q) for q in args.qualities.split(",")]
    rates = [float(r) for r in args.kbps.split(",")]

    for name, *images in args.group:
        print(f"\n### {name} ({len(images)} images, {w}x{h}, {args.scale})\n")
        header = "| quality | KB/frame (median) | min - max | " + " | ".join(
            f"FPS ceiling @ {r:.0f} KB/s" for r in rates) + " | encode (ms) |"
        print(header)
        print("|" + "---|" * (header.count("|") - 1))
        for q in qualities:
            sizes, times = [], []
            for img in images:
                t0 = time.perf_counter()
                jpeg = transcode_image(img, w, h, q, True, args.scale)
                times.append((time.perf_counter() - t0) * 1000)
                sizes.append(len(jpeg))
            med = statistics.median(sizes)
            per_frame = med + OVERHEAD * med / 1024 + 16
            fps = " | ".join(f"{r * 1024 / per_frame:5.1f}" for r in rates)
            print(f"| {q} | {med / 1024:5.1f} | {min(sizes) / 1024:.1f} - {max(sizes) / 1024:.1f} | {fps} | "
                  f"{statistics.median(times):.1f}* |")
        print("\n\\* includes opening and decoding the source image; on the live server the encode "
              "at 480x272 costs ~1 ms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
