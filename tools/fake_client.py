#!/usr/bin/env python3
"""Cliente de teste que imita o PSP: mesmo protocolo e mesma estrutura de
threads (rede recebendo enquanto o "decode" acontece).

Serve para testar o servidor sem o PSP e para simular o gargalo do Wi-Fi
802.11b (--kbps) e o tempo de decode do PSP (--decode-ms). Os números que
ele produz são SIMULADOS. Os reais vêm do PSP (overlay e log do servidor).
"""
import argparse
import json
import random
import select
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


# Mesmos tempos do cliente PSP (stream.c)
GAP_MIN_S, GAP_MAX_S = 0.020, 0.050  # fim do frame perdido: silêncio > média + 4 desvios entre pedaços
RTO_MIN_S, RTO_MAX_S = 0.030, 0.200  # depois de um NACK: média + 4 desvios da ida e volta
RTT_SAMPLE_MAX_S = 0.150             # acima disso o servidor esperou frame novo
MAX_NACKS = 3          # depois disso desiste do frame e pede outro
# pedido sem resposta: reenvia depois de uma ida e volta medida (rtt.timeout(RTO_MIN_S, RTO_MAX_S))
STALL_S = 3.0          # nada completo por 3 s: recomeça (HELLO)
EARLY_AUTO_MAX = 8 * 1024  # pedido antecipado automático: ping / intervalo entre pedaços x pedaço (teto)
EARLY_PING_DEFAULT_S = 0.006


class Estimator:
    """Média móvel + desvio médio, como o RTO do TCP (est_update no stream.c)."""

    def __init__(self, avg, dev):
        self.avg, self.dev = avg, dev

    def update(self, sample):
        err = sample - self.avg
        self.avg += err / 8
        self.dev += (abs(err) - self.dev) / 4

    def timeout(self, lo, hi):
        return min(hi, max(lo, self.avg + 4 * self.dev))


class FakePSP:
    def __init__(self, args):
        self.args = args
        self.udp = args.transport == "udp"
        if self.udp:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.dest = (socket.gethostbyname(args.host), args.port)
        else:
            self.sock = socket.create_connection((args.host, args.port), timeout=10)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.throttle = Throttle(args.kbps)
        self.lost = 0      # frames incompletos abandonados (UDP)
        self.nacks = 0
        self.lost_chunks = 0  # descartados pela perda simulada (--loss)
        self.dup_chunks = 0   # chegaram repetidos ou atrasados: ar desperdiçado
        self.hdrs = []        # cabeçalhos JPEG guardados [(id, bytes)], o mais novo primeiro
        self.hdr_have = 0
        self.stripped = 0     # frames que vieram sem o cabeçalho
        self.ping_us = 0
        self.early_cur = 0    # bytes que faltavam no último pedido antecipado (0 = só no fim)
        self.cond = threading.Condition()
        self.ready = None          # frame mais novo ainda não decodificado
        self.last_ack = None       # (frame_no, send_ts, shown_at, net_t, local_t, decode_t)
        self.send_lock = threading.Lock()
        self.want = threading.Event()
        self.dropped = 0
        self.error = None
        self.running = True
        self.buttons, self.lx, self.ly = 0, 128, 128

    def send_req(self, flags, nack=b""):
        r = Request(flags=flags, buttons=self.buttons, lx=self.lx, ly=self.ly)
        with self.cond:
            a = self.last_ack
        if a:
            r.ack_frame, r.echo_ts = a[0], a[1]
            r.since_t = clamp_u16((time.monotonic() - a[2]) * 10000)
            r.net_t, r.local_t, r.decode_t = a[3], a[4], a[5]
            r.first_t, r.burst_t = a[6], a[7]
            r.idle_t = a[8]
        r.hdr_have = self.hdr_have
        r.early_b = clamp_u16(self.early_cur)
        r.ping_select = clamp_u16(self.ping_us / 100)
        data = r.pack() + nack
        if self.args.rtt_ms and self.udp:
            # simula o atraso fixo por pedido (subida no Wi-Fi + reação do servidor)
            threading.Timer(self.args.rtt_ms / 1000, self._raw_send, args=(data,)).start()
            return
        self._raw_send(data)

    def _raw_send(self, data):
        if self.args.loss_up and random.random() < self.args.loss_up:
            return  # pedido "perdido no Wi-Fi" na subida
        with self.send_lock:
            try:
                if self.udp:
                    self.sock.sendto(data, self.dest)
                else:
                    self.sock.sendall(data)
            except OSError:
                pass

    def publish(self, frame):
        with self.cond:
            if self.ready is not None:
                self.dropped += 1
            self.ready = frame
            self.cond.notify_all()
        if self.args.no_prefetch:
            self.want.wait()
            self.want.clear()

    def ping_phase(self, count=8):
        """Ida e volta pura antes de pedir frames, como o PSP (só com select)."""
        rtts = []
        for i in range(count):
            token = (int(time.monotonic() * 1e6) ^ i << 28) & 0xFFFFFFFF
            t0 = time.monotonic()
            data = Request(flags=protocol.REQ_PING, echo_ts=token).pack()
            if self.args.rtt_ms:  # mesmo atraso simulado dos pedidos
                threading.Timer(self.args.rtt_ms / 1000, self._raw_send, args=(data,)).start()
            else:
                self._raw_send(data)
            while True:
                left = 0.3 - (time.monotonic() - t0)
                if left <= 0 or not select.select([self.sock], [], [], left)[0]:
                    break
                data = self.sock.recv(2048)
                if data == protocol.pack_pong(token):
                    rtts.append(time.monotonic() - t0)
                    break
        if rtts:
            self.ping_us = sorted(rtts)[len(rtts) // 2] * 1e6

    def hdr_learn(self, hid, jpeg):
        if not hid or (self.hdrs and self.hdrs[0][0] == hid):
            return
        n = protocol.jpeg_header_len(jpeg)
        if n:
            self.hdrs = [(hid, jpeg[:n])] + [h for h in self.hdrs if h[0] != hid][:1]
            self.hdr_have = hid

    def net_loop_udp(self):
        """Mesma lógica do stream.c do PSP: até dois frames em remontagem,
        pedido antecipado, NACK, desistência e reenvio de pedido."""
        P = protocol.CHUNK_PAYLOAD
        auto = self.args.early_kb == "auto"
        fixed = 0 if auto else int(float(self.args.early_kb) * 1024)
        try:
            asm = []          # frames em remontagem, do mais velho ao mais novo
            done = 0
            req_q = []        # horários dos pedidos sem resposta (máx. 2)
            last_req = last_done = link_free = time.monotonic()

            def ask(flags):
                nonlocal last_req
                if len(req_q) < 2:
                    req_q.append(time.monotonic())
                last_req = time.monotonic()
                self.send_req(flags)

            gap = Estimator(0.003, 0.003)   # entre pedaços seguidos de um frame
            rtt = Estimator(0.030, 0.010)   # pedido -> primeiro pedaço

            def early_bytes():
                if not auto:
                    self.early_cur = fixed
                    return fixed
                ping = self.ping_us / 1e6 or EARLY_PING_DEFAULT_S
                self.early_cur = min(EARLY_AUTO_MAX, int(ping * P / max(gap.avg, 0.0002)))
                return self.early_cur

            def send_nack(a):
                missing = [i for i in range(a["count"]) if i not in a["have"]]
                a["nack_last"] = missing[-1]
                a["nacks"] += 1
                self.nacks += 1
                a["t_nack"] = time.monotonic()
                a["deadline"] = a["t_nack"] + rtt.timeout(RTO_MIN_S, RTO_MAX_S)
                self.send_req(protocol.REQ_NACK, protocol.pack_nack(a["no"], missing))

            self.ping_phase()
            last_req = last_done = link_free = time.monotonic()
            ask(REQ_FRAME | REQ_HELLO)
            while self.running:
                now = time.monotonic()
                timeout, acted = 0.1, False
                for a in list(asm):
                    left = a["deadline"] - now
                    if left > 0:
                        timeout = min(timeout, left)
                        continue
                    acted = True
                    # um mais novo já chegando: o reenvio viria atrás dele na fila
                    newer = a is asm[0] and len(asm) == 2
                    if a["nacks"] < MAX_NACKS and not newer:
                        send_nack(a)
                    else:
                        done = max(done, a["no"])
                        asm.remove(a)
                        self.lost += 1
                if not asm:
                    if not req_q:
                        ask(REQ_FRAME)
                        continue
                    left = rtt.timeout(RTO_MIN_S, RTO_MAX_S) - (time.monotonic() - last_req)
                    if left <= 0:
                        stalled = time.monotonic() - last_done > STALL_S
                        last_req = time.monotonic()
                        self.send_req(REQ_FRAME | (REQ_HELLO if stalled else 0))
                        continue
                    timeout = min(timeout, left)
                if acted:
                    continue

                r, _, _ = select.select([self.sock], [], [], timeout)
                if not r:
                    continue
                data, _ = self.sock.recvfrom(2048)
                if self.args.loss and random.random() < self.args.loss:
                    self.lost_chunks += 1
                    continue  # pacote "perdido no Wi-Fi"
                self.throttle.consume(len(data))
                try:
                    no, fsize, fts, idx, fcount, hdr, payload = protocol.unpack_chunk(data)
                except (ValueError, Exception):
                    continue
                if fsize > protocol.MAX_JPEG or fcount != protocol.chunk_count(fsize) or idx >= fcount:
                    continue
                if len(payload) != (fsize - idx * P if idx == fcount - 1 else P):
                    continue
                if no <= done and time.monotonic() - last_done > STALL_S:
                    asm.clear()
                    done = 0  # servidor reiniciou a numeração
                if no <= done:
                    self.dup_chunks += 1
                    continue
                a = next((x for x in asm if x["no"] == no), None)
                if a is None:
                    if len(asm) == 2:  # dois em andamento: o mais velho sai
                        done = max(done, asm[0]["no"])
                        asm.pop(0)
                        self.lost += 1
                    head = b""
                    if hdr & protocol.HDR_STRIPPED:
                        head = next((h for i, h in self.hdrs if i == hdr & ~protocol.HDR_STRIPPED), None)
                        if head is None:  # sem o cabeçalho: pula o frame (o próximo vem inteiro)
                            done = max(done, no)
                            if req_q:
                                req_q.pop(0)
                            continue
                        self.stripped += 1
                    t_first = time.monotonic()
                    a = {"no": no, "count": fcount, "have": set(), "nacks": 0, "asked": False, "hi": 0,
                         "last_rx": t_first, "t_first": t_first, "buf": bytearray(head) + bytearray(fsize),
                         "ts": fts, "t_req": t_first, "deadline": 0.0, "base": len(head), "hdr": hdr}
                    if req_q:
                        a["t_req"] = req_q.pop(0)
                        if t_first - a["t_req"] < RTT_SAMPLE_MAX_S:
                            rtt.update(t_first - a["t_req"])
                    asm.append(a)
                t_rx = time.monotonic()
                if idx not in a["have"]:
                    if a["have"] and not a["nacks"] and t_rx - a["last_rx"] < 0.1:
                        gap.update(t_rx - a["last_rx"])
                    a["have"].add(idx)
                    a["hi"] = max(a["hi"], idx + 1)
                    off = a["base"] + idx * P
                    a["buf"][off: off + len(payload)] = payload
                else:
                    self.dup_chunks += 1
                a["last_rx"] = t_rx
                if len(a["have"]) == a["count"]:
                    t = time.monotonic()
                    t_req = max(a["t_req"], link_free)
                    idle = max(-0x7FFF, min(0x7FFF, int((a["t_first"] - link_free) * 10000)))
                    link_free = last_done = t
                    done = max(done, no)
                    asm.remove(a)
                    if not a["hdr"] & protocol.HDR_STRIPPED:
                        self.hdr_learn(a["hdr"], bytes(a["buf"]))
                    for older in [x for x in asm if x["no"] < no]:
                        asm.remove(older)
                        self.lost += 1
                    self.publish((no, a["ts"], bytes(a["buf"]), t_req, t, a["t_first"], idle))
                    if self.args.no_prefetch or (not a["asked"] and not req_q):
                        ask(REQ_FRAME)
                    continue
                if (idx == a["count"] - 1 if not a["nacks"]
                        else idx >= a["nack_last"] and t_rx - a["t_nack"] > rtt.avg / 2):
                    a["deadline"] = t_rx  # último do envio/reenvio chegou e há buracos: age já
                else:
                    d = t_rx + gap.timeout(GAP_MIN_S, GAP_MAX_S)
                    if (len(a["have"]) == 1 and not a["nacks"]) or d > a["deadline"]:
                        a["deadline"] = d
                # com buraco, não antecipa: o próximo entraria na fila na frente do reenvio
                if (not self.args.no_prefetch and (auto or fixed) and not a["asked"] and not req_q
                      and len(a["have"]) == a["hi"]
                      and len(a["buf"]) - a["base"] - len(a["have"]) * P <= early_bytes()):
                    a["asked"] = True  # pedido antecipado
                    ask(REQ_FRAME)
        except (OSError, ValueError) as exc:
            self.error = exc
            with self.cond:
                self.cond.notify_all()

    def net_loop(self):
        try:
            t_req = time.monotonic()
            self.send_req(REQ_FRAME | REQ_HELLO)
            while self.running:
                frame_no, size, send_ts = protocol.unpack_frame_header(recv_exact(self.sock, 16, self.throttle))
                t_first = time.monotonic()
                jpeg = recv_exact(self.sock, size, self.throttle)
                self.publish((frame_no, send_ts, jpeg, t_req, time.monotonic(), t_first, protocol.IDLE_NONE))
                t_req = time.monotonic()
                self.send_req(REQ_FRAME)
        except (OSError, ConnectionError, ValueError) as exc:
            self.error = exc
            with self.cond:
                self.cond.notify_all()

    def input_demo(self):
        """Sequência fixa de controles, enviada como no PSP (mensagens só de entrada)."""
        cross, up = 0x4000, 0x0010
        steps = [(0.5, cross, 128, 128), (0.7, 0, 128, 128), (0.9, up, 128, 128), (1.1, 0, 128, 128),
                 (1.3, 0, 255, 128), (1.8, 0, 128, 128), (2.0, 0, 128, 0), (2.3, 0, 128, 128)]
        t0 = time.monotonic()
        for at, buttons, lx, ly in steps:
            time.sleep(max(0.0, at - (time.monotonic() - t0)))
            self.buttons, self.lx, self.ly = buttons, lx, ly
            try:
                self.send_req(0)
            except OSError:
                return

    def run(self):
        threading.Thread(target=self.net_loop_udp if self.udp else self.net_loop, daemon=True).start()
        if self.args.input_demo:
            threading.Thread(target=self.input_demo, daemon=True).start()
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
                frame_no, send_ts, jpeg, t_req, t_recv, t_first, idle = self.ready
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
                                 clamp_u16((shown - t_recv) * 10000), clamp_u16(self.args.decode_ms * 10),
                                 clamp_u16(max(0.0, t_first - t_req) * 10000), clamp_u16((t_recv - t_first) * 10000),
                                 idle)
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
            "lost": self.lost,
            "nacks": self.nacks,
            "lost_chunks": self.lost_chunks,
            "dup_chunks": self.dup_chunks,
            "stripped": self.stripped,
            "ping_ms": round(self.ping_us / 1000, 2),
        }, jpeg


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("host", nargs="?", default="127.0.0.1")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    p.add_argument("--frames", type=int, default=100, help="quantos frames exibir")
    p.add_argument("--seconds", type=float, default=0, help="ou rodar por N segundos")
    p.add_argument("--transport", choices=["tcp", "udp"], default="tcp")
    p.add_argument("--loss", type=float, default=0, help="UDP: fração de pacotes perdidos (ex.: 0.02)")
    p.add_argument("--loss-up", type=float, default=0,
                   help="UDP: fração dos pedidos do PSP perdidos na subida (ex.: 0.05)")
    p.add_argument("--rtt-ms", type=float, default=0, help="UDP: atraso fixo por pedido (ex.: 21, medido no PSP)")
    p.add_argument("--early-kb", default="auto",
                   help="UDP: pedido antecipado, como no PSP: auto (padrão) = ida e volta x vazão, "
                        "0 = só no fim do frame, N = quando faltarem N KB")
    p.add_argument("--kbps", type=float, default=0, help="limitar a vazão (KB/s), ex.: 400")
    p.add_argument("--decode-ms", type=float, default=0, help="simular o tempo de decode do PSP")
    p.add_argument("--no-prefetch", action="store_true",
                   help="só pedir o próximo frame depois de 'decodificar' o atual")
    p.add_argument("--save", help="salvar o último JPEG exibido neste arquivo")
    p.add_argument("--input-demo", action="store_true",
                   help="enviar uma sequência de teste: X, cima, analógico p/ direita e p/ cima")
    p.add_argument("--json", action="store_true", help="imprimir o resumo em JSON")
    args = p.parse_args(argv)

    summary, jpeg = FakePSP(args).run()
    if args.save and jpeg:
        Path(args.save).write_bytes(jpeg)
    if args.json:
        print(json.dumps(summary))
    else:
        print("{frames} frames em {seconds} s: {fps} fps, {kb_per_frame} KB/frame, {kbps} KB/s, "
              "rede {net_ms} ms, local {local_ms} ms, descartados {dropped}, perdidos {lost}, "
              "NACKs {nacks}, pedaços perdidos/repetidos {lost_chunks}/{dup_chunks}".format(**summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
