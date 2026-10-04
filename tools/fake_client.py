#!/usr/bin/env python3
"""Cliente de teste que imita o PSP: segue o mesmo protocolo e mede o stream.

Serve para testar o servidor sem o PSP e para simular o gargalo do Wi-Fi
802.11b (--kbps) e o tempo de decode do PSP (--decode-ms).
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import protocol  # noqa: E402
from protocol import REQ_FRAME, REQ_HELLO, Request, clamp_u16  # noqa: E402


def recv_exact(sock, size, kbps=0.0):
    """Lê `size` bytes. Com kbps > 0, limita a vazão para imitar o Wi-Fi."""
    buf = bytearray()
    start = time.monotonic()
    while len(buf) < size:
        chunk = sock.recv(min(size - len(buf), 1460))
        if not chunk:
            raise ConnectionError("servidor fechou a conexão")
        buf += chunk
        if kbps > 0:
            ahead = len(buf) / (kbps * 1024) - (time.monotonic() - start)
            if ahead > 0:
                time.sleep(ahead)
    return bytes(buf)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("host", nargs="?", default="127.0.0.1")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    p.add_argument("--frames", type=int, default=100, help="quantos frames receber")
    p.add_argument("--seconds", type=float, default=0, help="ou rodar por N segundos")
    p.add_argument("--kbps", type=float, default=0, help="limitar a vazão (KB/s), ex.: 400")
    p.add_argument("--decode-ms", type=float, default=0, help="simular o tempo de decode do PSP")
    p.add_argument("--no-prefetch", action="store_true",
                   help="só pedir o próximo frame depois de 'decodificar' o atual")
    p.add_argument("--save", help="salvar o último JPEG recebido neste arquivo")
    p.add_argument("--json", action="store_true", help="imprimir o resumo em JSON")
    args = p.parse_args(argv)

    sock = socket.create_connection((args.host, args.port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    req = Request(flags=REQ_FRAME | REQ_HELLO)
    sock.sendall(req.pack())
    req_sent = time.monotonic()

    sizes, nets, locals_ = [], [], []
    last = None  # (frame_no, send_ts, displayed_at, net_t, local_t, decode_t)
    jpeg = b""
    start = time.monotonic()
    count = 0
    while True:
        if args.seconds and time.monotonic() - start >= args.seconds:
            break
        if not args.seconds and count >= args.frames:
            break
        frame_no, size, send_ts = protocol.unpack_frame_header(recv_exact(sock, 16, args.kbps))
        jpeg = recv_exact(sock, size, args.kbps)
        received = time.monotonic()
        net_ms = (received - req_sent) * 1000
        count += 1
        sizes.append(size)
        nets.append(net_ms)

        def ack_request(flags):
            r = Request(flags=flags)
            if last:
                r.ack_frame, r.echo_ts = last[0], last[1]
                r.since_t = clamp_u16((time.monotonic() - last[2]) * 10000)
                r.net_t, r.local_t, r.decode_t = last[3], last[4], last[5]
            return r

        if not args.no_prefetch:
            sock.sendall(ack_request(REQ_FRAME).pack())
            req_sent = time.monotonic()
        if args.decode_ms:
            time.sleep(args.decode_ms / 1000)
        displayed = time.monotonic()
        local_ms = (displayed - received) * 1000
        locals_.append(local_ms)
        last = (frame_no, send_ts, displayed, clamp_u16(net_ms * 10),
                clamp_u16(local_ms * 10), clamp_u16(args.decode_ms * 10))
        if args.no_prefetch:
            sock.sendall(ack_request(REQ_FRAME).pack())
            req_sent = time.monotonic()

    elapsed = time.monotonic() - start
    sock.close()
    if args.save and jpeg:
        Path(args.save).write_bytes(jpeg)
    summary = {
        "frames": count,
        "seconds": round(elapsed, 2),
        "fps": round(count / elapsed, 1) if elapsed else 0,
        "kb_per_frame": round(sum(sizes) / len(sizes) / 1024, 1) if sizes else 0,
        "kbps": round(sum(sizes) / elapsed / 1024, 1) if elapsed else 0,
        "net_ms": round(sum(nets) / len(nets), 1) if nets else 0,
        "local_ms": round(sum(locals_) / len(locals_), 1) if locals_ else 0,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print("{frames} frames em {seconds} s: {fps} fps, {kb_per_frame} KB/frame, "
              "{kbps} KB/s, rede {net_ms} ms, local {local_ms} ms".format(**summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
