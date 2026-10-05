#!/usr/bin/env python3
"""Teste de ponta a ponta da tela de configuração no PPSSPPHeadless.

server.txt sem IP: a tela abre e espera. Pelo depurador WebSocket do PPSSPP,
"aperta" X em "Procurar o PC na rede" (ping UDP em broadcast, que o servidor
responde), depois START (grava o server.txt e conecta), e confere que o
stream começou e que o server.txt ganhou o IP.

  PPSSPP_HEADLESS=/caminho/PPSSPPHeadless python3 tools/emu_menu_test.py

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
    cfg = game / "server.txt"
    cfg.write_text(f"# sem IP: a tela de configuracao abre\nport={port}\nexit_after=200\n")

    log_path = work / "server.log"
    server = subprocess.Popen([python, str(ROOT / "server/pspstream.py"), "--config", str(work / "server.json"), "--no-web", "--source", "static", "--port", str(port),
                               "--no-input"], stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    time.sleep(1.5)
    emu = subprocess.Popen([headless, f"--memstick={work / 'ms'}", "--graphics=software", "--timeout=60",
                            f"--debugger-run={dbg}", str(game / "EBOOT.PBP")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
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

        def press(button, hold=0.15):
            ws.send(json.dumps({"event": "input.buttons.send", "buttons": {button: True}}))
            time.sleep(hold)
            ws.send(json.dumps({"event": "input.buttons.send", "buttons": {button: False}}))
            time.sleep(0.3)

        time.sleep(2.0)
        if "PSP iniciou o stream" in log_path.read_text():
            print("FALHOU: conectou sem IP, a tela não esperou")
            return 1
        press("cross")          # o item inicial sem IP é "Procurar o PC na rede"
        time.sleep(3.0)         # Wi-Fi emulado + broadcast
        press("start")          # salvar e conectar
        for _ in range(150):
            if "PSP iniciou o stream" in log_path.read_text():
                ok = True
                break
            time.sleep(0.1)
        saved = cfg.read_text()
        print("---- server.txt gravado pelo PSP ----")
        print(saved)
        if not ok:
            print("FALHOU: o stream não começou")
            return 1
        first = next(line for line in saved.splitlines() if line and not line.startswith("#"))
        if not first.split(":")[0].count(".") == 3:
            print(f"FALHOU: a primeira linha do server.txt não é um IP: {first!r}")
            return 1
        print(f"OK: achou o PC ({first}), gravou o server.txt e o stream começou")
        return 0
    finally:
        try:
            emu.wait(timeout=30)
        except subprocess.TimeoutExpired:
            emu.kill()
        server.terminate()
        server.wait()
        print("---- servidor ----")
        print(log_path.read_text()[-1500:])
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
