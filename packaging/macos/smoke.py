#!/usr/bin/env python3
"""Smoke test for the macOS server bundle or repository server.

Checks --version, --lang pt --help, --check, web /api/status, and live
h264p/jpeg streaming with audio through tools/fake_client.py.

Usage:
  python3 packaging/macos/smoke.py dist/PSPStream
  python3 packaging/macos/smoke.py --repo
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def free_port() -> int:
    """Allocate an ephemeral local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, proc: subprocess.Popen, timeout: float = 10.0) -> bytes:
    """Poll a local HTTP URL until it responds 200 or the server exits."""
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return resp.read()
        except (OSError, urllib.error.URLError) as e:
            last_err = e
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {url}: {last_err}")


def run_cli(base_cmd: list[str], extra: list[str], label: str) -> str:
    """Run a short CLI subcommand and assert exit 0."""
    res = subprocess.run(
        [*base_cmd, *extra],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"{label} failed (rc={res.returncode}):\n{res.stdout}\n{res.stderr}"
        )
    return res.stdout


def run_stream_check(base_cmd: list[str], source: str, audio: str, codec: str,
                     seconds: float = 3.0, verbose: bool = False) -> None:
    """Start server, stream with fake_client.py, and verify web status + frame stats."""
    port = free_port()
    web_port = free_port()
    work = Path(tempfile.mkdtemp(prefix="pspstream-smoke-"))
    log_path = work / "server.log"
    cfg_path = work / "server.json"

    cmd = [
        *base_cmd,
        "--source", source,
        "--codec", codec,
        "--audio-device", audio,
        "--no-input",
        "--port", str(port),
        "--web", f"127.0.0.1:{web_port}",
        "--config", str(cfg_path),
    ]
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)
    try:
        wait_http(f"http://127.0.0.1:{web_port}/api/status", proc)
        index_html = wait_http(f"http://127.0.0.1:{web_port}/", proc).decode("utf-8", errors="replace")
        if "PSPStream" not in index_html:
            raise RuntimeError("web UI / did not return PSPStream HTML")

        client_cmd = [
            sys.executable, str(ROOT / "tools" / "fake_client.py"),
            "--port", str(port),
            "--transport", "udp",
            "--seconds", str(seconds),
            "--audio",
            "--json",
        ]
        if codec == "h264p":
            client_cmd.append("--h264p")

        fake = subprocess.run(client_cmd, capture_output=True, text=True, timeout=seconds + 15)
        if fake.returncode != 0:
            raise RuntimeError(f"fake_client failed ({codec}/{source}): {fake.stderr}\n{fake.stdout}")
        lines = [line.strip() for line in fake.stdout.splitlines() if line.strip()]
        summary = json.loads(lines[-1])
        status = json.loads(wait_http(f"http://127.0.0.1:{web_port}/api/status", proc).decode("utf-8"))

        print(
            f"stream [{source}/{codec}]: frames={summary['frames']} "
            f"broken={summary['broken']} kb/f={summary['kb_per_frame']} "
            f"audio_packets={summary['audio_packets']} capture={status.get('capture')}"
        )
        assert summary["frames"] > 15, f"too few frames ({source}/{codec}): {summary}"
        assert summary["broken"] == 0, f"broken P-frames ({source}/{codec}): {summary}"
        assert summary["audio_packets"] > 0, f"no audio packets ({source}/{codec}): {summary}"
    except Exception:
        log_file.flush()
        server_log = log_path.read_text(encoding="utf-8", errors="replace")
        print(f"--- server log ({source}/{codec}) ---\n{server_log}", file=sys.stderr)
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
        if verbose:
            print(log_path.read_text(encoding="utf-8", errors="replace"))
        shutil.rmtree(work, ignore_errors=True)


def resolve_base_cmd(args) -> list[str]:
    """Return the server command prefix for either --repo or a packaged app dir."""
    if args.repo:
        return [sys.executable, str(ROOT / "server" / "pspstream.py")]
    if not args.app:
        raise SystemExit("pass a bundle path (e.g. dist/PSPStream) or --repo")
    target = Path(args.app).resolve()
    exe = target / "pspstream" if target.is_dir() else target
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise SystemExit(f"executable not found or not executable: {exe}")
    helper = exe.parent / "pspstream-sck"
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise SystemExit(f"ScreenCaptureKit helper missing next to {exe}: {helper}")
    return [str(exe)]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("app", nargs="?", help="path to dist/PSPStream directory or executable")
    p.add_argument("--repo", action="store_true", help="run against server/pspstream.py in this repository")
    p.add_argument("--screen", action="store_true", help="also smoke-test live ScreenCaptureKit screen + audio capture")
    p.add_argument("-v", "--verbose", action="store_true", help="print server logs even on success")
    args = p.parse_args(argv)

    base_cmd = resolve_base_cmd(args)

    ver = run_cli(base_cmd, ["--version"], "--version").strip()
    if not ver:
        raise RuntimeError("--version printed empty output")
    print(f"version: {ver}")

    pt_help = run_cli(base_cmd, ["--lang", "pt", "--help"], "--lang pt --help")
    assert "--check" in pt_help and "servidor" in pt_help.lower(), "unexpected Portuguese --help output"

    check_out = run_cli(base_cmd, ["--check"], "--check")
    if args.verbose:
        print(check_out)

    for codec in ("h264p", "jpeg"):
        run_stream_check(base_cmd, "test", "test", codec, seconds=3.0, verbose=args.verbose)

    if args.screen:
        run_stream_check(base_cmd, "screen", "monitor", "h264p", seconds=2.0, verbose=args.verbose)

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
