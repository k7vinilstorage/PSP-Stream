#!/usr/bin/env python3
"""Cliente de teste que imita o PSP: mesmo protocolo e mesma estrutura de
threads (rede recebendo enquanto o "decode" acontece).

Serve para testar o servidor sem o PSP e para simular o gargalo do Wi-Fi
802.11b (--kbps) e o tempo de decode do PSP (--decode-ms). Os números que
ele produz são SIMULADOS. Os reais vêm do PSP (overlay e log do servidor).
"""
import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import protocol  # noqa: E402
from protocol import REQ_FRAME, REQ_HELLO, Request, clamp_u16  # noqa: E402


class Throttle:
    """Limita a vazão de recepção como um enlace de `kbps` KB/s."""

    def __init__(self, kbps: float):
        self.rate = kbps * 1024
        self.t = time.monotonic()

    def consume(self, n: int):
        if self.rate <= 0:
            return
        now = time.monotonic()
        self.t = max(self.t, now) + n / self.rate
        if self.t > now:
            time.sleep(self.t - now)


def recv_exact(sock, size, throttle):
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(min(size - len(buf), 1460))
        if not chunk:
            raise ConnectionError("servidor fechou a conexão")
        throttle.consume(len(chunk))
        buf += chunk
    return bytes(buf)


class FakePSP:
    def __init__(self, args):
        self.args = args
        self.sock = socket.create_connection((args.host, args.port), timeout=10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.throttle = Throttle(args.kbps)
        self.cond = threading.Condition()
        self.ready = None          # frame mais novo ainda não decodificado
        self.last_ack = None       # (frame_no, send_ts, shown_at, net_t, local_t, decode_t)
        self.send_lock = threading.Lock()
        self.want = threading.Event()
        self.dropped = 0
        self.error = None
        self.running = True

    def send_req(self, flags):
        r = Request(flags=flags)
        with self.cond:
            a = self.last_ack
        if a:
            r.ack_frame, r.echo_ts = a[0], a[1]
            r.since_t = clamp_u16((time.monotonic() - a[2]) * 10000)
            r.net_t, r.local_t, r.decode_t = a[3], a[4], a[5]
        with self.send_lock:
            self.sock.sendall(r.pack())

    def net_loop(self):
        try:
            t_req = time.monotonic()
            self.send_req(REQ_FRAME | REQ_HELLO)
            while self.running:
                frame_no, size, send_ts = protocol.unpack_frame_header(recv_exact(self.sock, 16, self.throttle))
                jpeg = recv_exact(self.sock, size, self.throttle)
                frame = (frame_no, send_ts, jpeg, t_req, time.monotonic())
                with self.cond:
                    if self.ready is not None:
                        self.dropped += 1
                    self.ready = frame
                    self.cond.notify_all()
                if self.args.no_prefetch:
                    self.want.wait()
                    self.want.clear()
                t_req = time.monotonic()
                self.send_req(REQ_FRAME)
        except (OSError, ConnectionError, ValueError) as exc:
            self.error = exc
            with self.cond:
                self.cond.notify_all()

    def run(self):
        threading.Thread(target=self.net_loop, daemon=True).start()
        sizes, nets, locals_ = [], [], []
        jpeg = b""
        start = time.monotonic()
        count = 0
        while True:
            elapsed = time.monotonic() - start
            if (self.args.seconds and elapsed >= self.args.seconds) or \
                    (not self.args.seconds and count >= self.args.frames):
                break
            with self.cond:
                self.cond.wait_for(lambda: self.ready is not None or self.error, timeout=1)
                if self.error:
                    break
                if self.ready is None:
                    continue
                frame_no, send_ts, jpeg, t_req, t_recv = self.ready
                self.ready = None
            if self.args.decode_ms:
                time.sleep(self.args.decode_ms / 1000)
            shown = time.monotonic()
            count += 1
            sizes.append(len(jpeg))
            nets.append((t_recv - t_req) * 1000)
            locals_.append((shown - t_recv) * 1000)
            with self.cond:
                self.last_ack = (frame_no, send_ts, shown, clamp_u16((t_recv - t_req) * 10000),
                                 clamp_u16((shown - t_recv) * 10000), clamp_u16(self.args.decode_ms * 10))
            self.want.set()
        elapsed = time.monotonic() - start
        self.running = False
        self.sock.close()
        return {
            "frames": count,
            "seconds": round(elapsed, 2),
            "fps": round(count / elapsed, 1) if elapsed else 0,
            "kb_per_frame": round(sum(sizes) / len(sizes) / 1024, 1) if sizes else 0,
            "kbps": round(sum(sizes) / elapsed / 1024, 1) if elapsed else 0,
            "net_ms": round(sum(nets) / len(nets), 1) if nets else 0,
            "local_ms": round(sum(locals_) / len(locals_), 1) if locals_ else 0,
            "dropped": self.dropped,
        }, jpeg


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("host", nargs="?", default="127.0.0.1")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    p.add_argument("--frames", type=int, default=100, help="quantos frames exibir")
    p.add_argument("--seconds", type=float, default=0, help="ou rodar por N segundos")
    p.add_argument("--kbps", type=float, default=0, help="limitar a vazão (KB/s), ex.: 400")
    p.add_argument("--decode-ms", type=float, default=0, help="simular o tempo de decode do PSP")
    p.add_argument("--no-prefetch", action="store_true",
                   help="só pedir o próximo frame depois de 'decodificar' o atual")
    p.add_argument("--save", help="salvar o último JPEG exibido neste arquivo")
    p.add_argument("--json", action="store_true", help="imprimir o resumo em JSON")
    args = p.parse_args(argv)

    summary, jpeg = FakePSP(args).run()
    if args.save and jpeg:
        Path(args.save).write_bytes(jpeg)
    if args.json:
        print(json.dumps(summary))
    else:
        print("{frames} frames em {seconds} s: {fps} fps, {kb_per_frame} KB/frame, {kbps} KB/s, "
              "rede {net_ms} ms, local {local_ms} ms, descartados {dropped}".format(**summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
