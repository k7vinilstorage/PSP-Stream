"""Imita o tools/kms/pspstream-kms nos testes: mesmo protocolo, mas os
"framebuffers" são memfd (não há placa de vídeo no ambiente de teste).

Cada quadro tem 2 planos no mesmo buffer (como o plano de compressão da
Intel): o mesmo fd vai duas vezes."""
import os
import socket
import struct
import sys

REQUEST = struct.Struct("<4sII")
REPLY = struct.Struct("<4si4IQI4I4III160s")
XR24 = int.from_bytes(b"XR24", "little")
W, H = 64, 32


def reply(status, fb_id=0, n_planes=0, msg=b"", modifier=0x0100000000000002):
    return REPLY.pack(b"PSK1", status, fb_id, W, H, XR24, modifier, n_planes,
                      W * 4, 64, 0, 0, 0, W * 4 * H, 0, 0, 59998, 7, msg)


def main():
    sock = socket.socket(fileno=int(sys.argv[1]))
    sock.send(reply(2, msg=b"falso, monitor 0 de 1"))
    fb = 0
    while True:
        data = sock.recv(REQUEST.size)
        if len(data) != REQUEST.size:
            return
        magic, cmd, timeout_ms = REQUEST.unpack(data)
        if cmd == 2:
            return
        if os.environ.get("FAKE_KMS_NOPERM"):  # como sem o setcap: handles zerados
            sock.send(reply(-1, msg="no permission to read the screen (fake)".encode()))
            return
        fb += 1
        fd = os.memfd_create(f"fb{fb}")
        os.ftruncate(fd, W * 4 * H + 4096)
        socket.send_fds(sock, [reply(0, fb_id=100 + fb % 3, n_planes=2)], [fd, fd])
        os.close(fd)


if __name__ == "__main__":
    main()
