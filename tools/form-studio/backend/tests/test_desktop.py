import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from formstudio import desktop, paths


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_mode_answers_health_and_stops_on_sigterm():
    port = _port()
    p = subprocess.Popen([sys.executable, "-m", "formstudio.desktop", "--serve", "--port", str(port)], env=os.environ.copy())
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
                assert b'"ok":true' in body
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("server never answered")
        p.send_signal(signal.SIGTERM)  # what the Mac app does on quit
        # uvicorn shuts down gracefully, then re-raises the signal
        assert p.wait(timeout=10) in (0, -signal.SIGTERM)
    finally:
        if p.poll() is None:
            p.kill()
    assert "server starting" in (paths.home() / "formstudio.log").read_text()


def test_startup_errors_are_logged_not_silent(monkeypatch):
    def boom(port):
        raise RuntimeError("simulated startup failure")

    monkeypatch.setattr(desktop, "_server", boom)
    assert desktop.main(["--serve", "--port", "1"]) == 1
    assert "simulated startup failure" in (paths.home() / "formstudio.log").read_text()
