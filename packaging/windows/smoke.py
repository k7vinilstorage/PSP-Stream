"""Teste do servidor de Windows já montado (dist/PSPStream/pspstream.exe), no CI:
--version, --check, a ajuda em português e um stream para o PSP falso
(tools/fake_client.py) com som e a interface web, em H.264 com frames P e
em JPEG. Com --screen, tenta a captura da tela de verdade (o runner pode não
ter uma área de trabalho que o Desktop Duplication enxergue).

  python packaging/windows/smoke.py dist/PSPStream [--screen]
"""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(exe, *args, timeout=120) -> subprocess.CompletedProcess:
    out = subprocess.run([str(exe), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout)
    print(f"$ pspstream {' '.join(args)}  -> {out.returncode}\n{out.stdout}{out.stderr}")
    return out


class Server:
    def __init__(self, exe, *args):
        self.port, self.web = free_port(), free_port()
        self.tmp = tempfile.mkdtemp()
        self.proc = subprocess.Popen([str(exe), *args, "--no-input", "--port", str(self.port),
                                      "--web", f"127.0.0.1:{self.web}", "--config", f"{self.tmp}/server.json"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace")
        self.log = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.log.append(line.rstrip())

    def wait_ready(self, timeout=60) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any("waiting for the PSP" in line for line in self.log):
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        self.stop()
        raise SystemExit("the server did not start:\n" + "\n".join(self.log))

    def status(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.web}/api/status", timeout=5) as r:
            return json.load(r)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        print("---- server log ----\n" + "\n".join(self.log[-40:]))


def fake_psp(port: int, seconds: float = 4) -> dict:
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "fake_client.py"), "127.0.0.1", "--port", str(port),
                          "--transport", "udp", "--h264p", "--audio", "--seconds", str(seconds), "--json"],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(f"fake_client failed: {out.stderr}")
    summary = json.loads(out.stdout.strip().splitlines()[-1])
    print("fake PSP:", {k: summary.get(k) for k in ("frames", "broken", "kb_per_frame", "audio_packets",
                                                     "audio_lost", "hitches", "gap_p99_ms")})
    return summary


def stream(exe, codec: str, min_frames: int) -> None:
    srv = Server(exe, "--source", "test", "--codec", codec, "--audio-device", "test")
    try:
        srv.wait_ready()
        summary = fake_psp(srv.port)
        status = srv.status()
        print("web status:", status.get("capture"))
    finally:
        srv.stop()
    assert summary["frames"] >= min_frames, summary
    assert summary["broken"] == 0, summary
    assert summary.get("audio_packets", 0) > 50, summary
    assert status["capture"]["source"] == "test", status
    assert any("capture: the source delivers" in line or "PSP connected" in line for line in srv.log), srv.log


def screen(exe) -> int:
    srv = Server(exe, "--source", "screen", "--audio-device", "test")
    try:
        srv.wait_ready()
        summary = fake_psp(srv.port, 3)
    finally:
        srv.stop()
    return 0 if summary["frames"] > 20 else 1


def main() -> int:
    # o console do runner é cp1252: o que o servidor escreveu não pode derrubar o teste ao ser mostrado
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app = Path(sys.argv[1]).resolve()
    exe = app / "pspstream.exe"
    if "--screen" in sys.argv:
        return screen(exe)
    assert "PSPStream" in run(exe, "--version").stdout
    assert "porta TCP e UDP (padrão" in run(exe, "--lang", "pt", "--help").stdout  # acentos em UTF-8 num pipe
    check = run(exe, "--check")
    assert check.returncode == 0, "--check found something essential missing"
    assert "libopenh264" in check.stdout
    stream(exe, "h264p", 100)
    stream(exe, "jpeg", 60)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
