#!/usr/bin/env python3
"""Teste de ponta a ponta dos controles (Marco 4) no PPSSPPHeadless.

Sobe o servidor com --input-dry-run, roda o EBOOT com o depurador WebSocket
do PPSSPP, "aperta" X e move o analógico pelo depurador, e confere se as
teclas/movimentos certos chegam ao injetor do PC.

  PPSSPP_HEADLESS=/caminho/PPSSPPHeadless python3 tools/emu_input_test.py

Requer: pip install websocket-client. Servidor: PYTHON=python3 (com gi).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websocket  # websocket-client

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    headless = os.environ["PPSSPP_HEADLESS"]
    python = os.environ.get("PYTHON", "python3")
    port, dbg = free_port(), free_port()
    work = Path(tempfile.mkdtemp())
    game = work / "ms/PSP/GAME/PSPStream"
    game.mkdir(parents=True)
    shutil.copy(ROOT / "psp/EBOOT.PBP", game)
    (game / "server.txt").write_text(f"127.0.0.1:{port}\nexit_after=400\noverlay=0\n")

    log_path = work / "server.log"
    server = subprocess.Popen([python, str(ROOT / "server/pspstream.py"), "--source", "test", "--port", str(port),
                               "--input-dry-run", "-v"], stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    time.sleep(1.5)
    emu = subprocess.Popen([headless, f"--memstick={work / 'ms'}", "--graphics=software", "--timeout=60",
                            f"--debugger-run={dbg}", str(game / "EBOOT.PBP")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(50):
            try:
                ws = websocket.create_connection(f"ws://127.0.0.1:{dbg}/debugger", timeout=5)
                break
            except OSError:
                time.sleep(0.2)
        if ws is None:
            print("FALHOU: depurador do PPSSPP não respondeu")
            return 1
        # espera o stream começar
        for _ in range(100):
            if "PSP iniciou o stream" in log_path.read_text():
                break
            time.sleep(0.1)
        time.sleep(1.0)

        def send(event, **params):
            ws.send(json.dumps({"event": event, **params}))

        send("input.buttons.send", buttons={"cross": True})
        time.sleep(0.4)
        send("input.buttons.send", buttons={"cross": False})
        time.sleep(0.4)
        send("input.analog.send", x=1.0, y=0.0)
        time.sleep(0.6)
        send("input.analog.send", x=0.0, y=0.0)
        time.sleep(0.6)
        ws.close()
    finally:
        emu.terminate()
        emu.wait(timeout=10)
        server.terminate()
        server.wait(timeout=10)

    log = log_path.read_text()
    moves = [line.split("mouse ")[1].split() for line in log.splitlines() if "mouse +" in line or "mouse -" in line]
    right = sum(int(dx) for dx, dy in moves if int(dx) > 0)
    checks = {
        "X -> KEY_SPACE pressionada": "tecla KEY_SPACE pressionada" in log,
        "X -> KEY_SPACE solta": "tecla KEY_SPACE solta" in log,
        f"analógico p/ direita -> mouse andou {right} px p/ direita": right > 100,
        "analógico solto -> mouse parou": bool(moves) and log.rstrip().splitlines()[-1].find("mouse +") < 0,
    }
    print("\n".join(f"{'ok ' if v else 'FALHOU'} {k}" for k, v in checks.items()))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
