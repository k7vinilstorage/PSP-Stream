#!/usr/bin/env python3
"""Teste do som no PPSSPPHeadless: liga e desliga pelo PSP, e reconecta.

Sobe o servidor com o tom de teste (--audio-device test), roda o EBOOT com o
depurador WebSocket do PPSSPP e confere no log do servidor e no do PSP: o
PSP pede o som ao conectar e abre o canal; SELECT + START + cima desliga (o
servidor para de mandar) e liga de novo (o canal abre outra vez); e depois
da tela de configuração (SELECT + START + R, O para conectar sem salvar) o
som volta. O PPSSPP recusa soltar o canal com amostras na fila, como o PSP:
foi assim que o "som: erro depois de mexer na configuração" apareceu aqui.
Os tempos do som não valem no emulador (o relógio dele pula o tempo ocioso).

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
    (game / "server.txt").write_text(f"127.0.0.1:{port}\nexit_after=100000\noverlay=1\ntransport=udp\n"
                                     "menu_wait=0\n")

    log_path, psp_path = work / "server.log", work / "psp.log"
    server = subprocess.Popen([python, str(ROOT / "server/pspstream.py"), "--config", str(work / "server.json"), "--no-web", "--source", "static", "--codec", "h264p",
                               "--audio-device", "test", "--no-input", "--port", str(port), "--stats-interval", "1"],
                              stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    time.sleep(1.5)
    emu = subprocess.Popen([headless, f"--memstick={work / 'ms'}", "--graphics=software", "--timeout=90",
                            f"--debugger-run={dbg}", str(game / "EBOOT.PBP")],
                           stdout=open(psp_path, "w"), stderr=subprocess.STDOUT)

    def wait_log(text, count=1, timeout=10.0, path=log_path):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.read_text(errors="replace").count(text) >= count:
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

        def press(*seq):
            for buttons in seq:
                ws.send(json.dumps({"event": "input.buttons.send", "buttons": buttons}))
                time.sleep(0.3)

        def combo(button):
            press({"select": True, "start": True}, {"select": True, "start": True, button: True},
                  {"select": False, "start": False, button: False})

        opened = "som: canal aberto"
        checks = {"o PSP pede o som ao conectar": wait_log("som: ligado no PSP"),
                  "o PSP abre o canal de som": wait_log(opened, path=psp_path)}
        checks["os pacotes de som saem"] = wait_log("| som ")
        time.sleep(1.0)
        combo("up")
        checks["SELECT + START + cima desliga"] = wait_log("som: desligado no PSP")
        time.sleep(0.5)
        combo("up")
        checks["... e liga de novo"] = wait_log("som: ligado no PSP", count=2)
        checks["... com o canal aberto de novo"] = wait_log(opened, count=2, path=psp_path)
        time.sleep(1.0)
        combo("rtrigger")  # tela de configuração
        time.sleep(1.5)
        press({"circle": True}, {"circle": False})  # conectar sem salvar
        checks["depois da configuração, o som volta"] = wait_log("som: ligado no PSP", count=3, timeout=20)
        checks["... e o canal abre"] = wait_log(opened, count=3, timeout=10, path=psp_path)
        checks["o canal nunca falhou"] = "falhou" not in psp_path.read_text(errors="replace")
        ws.close()
    finally:
        emu.terminate()
        emu.wait(timeout=10)
        server.terminate()
        server.wait(timeout=10)

    print("\n".join(f"{'ok ' if v else 'FALHOU'} {k}" for k, v in checks.items()))
    if not all(checks.values()):
        print("---- PSP ----")
        print("\n".join(line for line in psp_path.read_text(errors="replace").splitlines() if "som" in line))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
