"""Starting Form Studio.

  python -m formstudio.desktop --serve --port 8765   server only (the Mac app's native shell runs this)
  python -m formstudio.desktop --browser             server + opens your browser
  python -m formstudio.desktop                       server + a pywebview window (fallback, needs [window] extra)
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request

from . import paths

log = logging.getLogger("formstudio")


def _setup_logging() -> None:
    paths.home().mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(paths.home() / "formstudio.log")]
    if sys.stderr and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def _wait(url: str, timeout: float = 120) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"the Form Studio server didn't answer within {timeout:.0f} s")


def _server(port: int):
    import uvicorn

    config = uvicorn.Config("formstudio.api.app:app", host="127.0.0.1", port=port, log_level="warning")
    return uvicorn.Server(config)


def main(argv: list[str] | None = None) -> int:
    # let PyTorch fall back to the CPU for the few ops Apple's GPU backend lacks
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    ap = argparse.ArgumentParser(prog="formstudio")
    ap.add_argument("--serve", action="store_true", help="run only the server (used by the Mac app)")
    ap.add_argument("--browser", action="store_true", help="open in your browser instead of a window")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FORMSTUDIO_PORT", 8765)))
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    _setup_logging()
    try:
        if args.serve:
            log.info("Form Studio server starting on port %s", args.port)
            _server(args.port).run()  # blocks; the Mac app stops it with SIGTERM
            return 0

        port = _free_port(args.port)
        url = f"http://127.0.0.1:{port}"
        server = _server(port)
        threading.Thread(target=server.run, daemon=True).start()
        _wait(url)
        log.info("Form Studio running at %s", url)
        if args.browser:
            import webbrowser

            webbrowser.open(url)
            print(f"Form Studio is running at {url}. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        else:
            import webview  # pywebview, optional: pip install -e 'backend[window]'

            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.create_window("Form Studio", url, width=1320, height=880, min_size=(980, 660), background_color="#F6F5F1")
            webview.start()
        server.should_exit = True
        return 0
    except Exception:
        log.error("Form Studio failed to start:\n%s", traceback.format_exc())
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
