"""Form Studio as a Mac app: starts the local server and opens it in a native window."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request

import uvicorn

from . import paths


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def _wait(url: str, timeout: float = 30) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("the Form Studio server didn't start")


def main() -> None:
    # let PyTorch fall back to the CPU for the few ops Apple's GPU backend lacks
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    log_file = paths.home() / "formstudio.log"
    paths.home().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(log_file), logging.StreamHandler()])
    port = _free_port(int(os.environ.get("FORMSTUDIO_PORT", 8765)))
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config("formstudio.api.app:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    _wait(url)

    if "--browser" in sys.argv:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        import webview  # pywebview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("Form Studio", url, width=1320, height=880, min_size=(980, 660), background_color="#F6F5F1")
        webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
