#!/usr/bin/env python3
"""Teste do som no PPSSPPHeadless: liga e desliga pelo PSP.

Sobe o servidor com o tom de teste (--audio-device test), roda o EBOOT com o
depurador WebSocket do PPSSPP e confere no log do servidor: o PSP pede o
som ao conectar, SELECT + START + cima desliga (o servidor para de mandar) e
liga de novo. Os tempos do som não valem no emulador (o relógio dele pula o
tempo ocioso); isto confere a lógica.

  PPSSPP_HEADLESS=/caminho/PPSSPPHeadless python3 tools/emu_audio_test.py

Requer: pip install websocket-client. Servidor: PYTHON=python3 (com gi).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websocket  # websocket-client

from emu_input_test import free_port

ROOT = Path(__file__).resolve().parent.parent


def main():
    headless = os.environ["PPSSPP_HEADLESS"]
    python = os.environ.get("PYTHON", "python3")
    port, dbg = free_port(), free_port()
    work = Path(tempfile.mkdtemp())
    game = work / "ms/PSP/GAME/PSPStream"
    game.mkdir(parents=True)
    shutil.copy(ROOT / "psp/EBOOT.PBP", game)
    (game / "server.txt").write_text(f"127.0.0.1:{port}\nexit_after=100000\noverlay=1\ntransport=udp\n")

    log_path = work / "server.log"
    server = subprocess.Popen([python, str(ROOT / "server/pspstream.py"), "--source", "static", "--codec", "h264p",
                               "--audio-device", "test", "--no-input", "--port", str(port), "--stats-interval", "1"],
                              stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    time.sleep(1.5)
    emu = subprocess.Popen([headless, f"--memstick={work / 'ms'}", "--graphics=software", "--timeout=60",
                            f"--debugger-run={dbg}", str(game / "EBOOT.PBP")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_log(text, count=1, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if log_path.read_text().count(text) >= count:
                return True
            time.sleep(0.1)
        return False

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

        def combo_up():
            for buttons in ({"select": True, "start": True}, {"select": True, "start": True, "up": True},
                            {"select": False, "start": False, "up": False}):
                ws.send(json.dumps({"event": "input.buttons.send", "buttons": buttons}))
                time.sleep(0.3)

        checks = {"o PSP pede o som ao conectar": wait_log("som: ligado no PSP")}
        checks["os pacotes de som saem"] = wait_log("| som ")
        time.sleep(1.0)
        combo_up()
        checks["SELECT + START + cima desliga"] = wait_log("som: desligado no PSP")
        combo_up()
        checks["... e liga de novo"] = wait_log("som: ligado no PSP", count=2)
        ws.close()
    finally:
        emu.terminate()
        emu.wait(timeout=10)
        server.terminate()
        server.wait(timeout=10)

    print("\n".join(f"{'ok ' if v else 'FALHOU'} {k}" for k, v in checks.items()))
    if not all(checks.values()):
        print(log_path.read_text()[-3000:])
    shutil.rmtree(work, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
