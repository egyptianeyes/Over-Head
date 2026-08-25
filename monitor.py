#!/usr/bin/env python3
"""Serve the Over-Head RGB framebuffer as a full-screen monitor display."""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from overhead import load_settings, produce_frame


PAGE = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Over-Head</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: #020409; }
    body { display: grid; place-items: center; font-family: system-ui, sans-serif; cursor: none; }
    .wall { width: 100vw; height: 100vh; display: grid; place-items: center; background:
      radial-gradient(ellipse at center, #07131b 0%, #020409 66%); }
    img { width: min(100vw, 200vh); height: auto; max-height: 100vh; object-fit: contain;
      image-rendering: pixelated; image-rendering: crisp-edges; filter: saturate(1.1) contrast(1.04);
      box-shadow: 0 0 12vh rgba(20,236,255,.09); }
    .status { position: fixed; right: 12px; bottom: 8px; color: #416573; font: 11px monospace;
      opacity: .65; }
  </style>
</head>
<body>
  <main class="wall"><img id="frame" src="/frame.png" alt="Live nearby aircraft"></main>
  <div class="status" id="status">STARTING</div>
  <script>
    const frame = document.getElementById('frame');
    const status = document.getElementById('status');
    async function update() {
      frame.src = '/frame.png?t=' + Date.now();
      try {
        const response = await fetch('/status', {cache: 'no-store'});
        const data = await response.json();
        status.textContent = data.mode.toUpperCase() + '  ' + data.updated;
      } catch (_) { status.textContent = 'RECONNECTING'; }
    }
    setInterval(update, 2000);
    update();
    document.addEventListener('dblclick', () => document.documentElement.requestFullscreen?.());
  </script>
</body>
</html>
"""


class FrameState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.png = b""
        self.mode = "starting"
        self.updated = "never"

    def update(self, image, mode: str) -> None:
        data = BytesIO()
        image.save(data, format="PNG")
        with self.lock:
            self.png = data.getvalue()
            self.mode = mode
            self.updated = time.strftime("%H:%M:%S")


def refresh_loop(state: FrameState, settings, demo: bool) -> None:
    while True:
        try:
            image, mode = produce_frame(settings, demo=demo)
            state.update(image, mode)
        except Exception as exc:  # keep the wall alive and expose the failure mode
            with state.lock:
                state.mode = f"error: {type(exc).__name__}"
                state.updated = time.strftime("%H:%M:%S")
        time.sleep(max(1.0, settings.refresh_seconds))


def handler_factory(state: FrameState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self.send(HTTPStatus.OK, "text/html; charset=utf-8", PAGE)
            elif path == "/frame.png":
                with state.lock:
                    body = state.png
                if body:
                    self.send(HTTPStatus.OK, "image/png", body, cache=False)
                else:
                    self.send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain", b"Starting")
            elif path == "/status":
                with state.lock:
                    body = json.dumps({"mode": state.mode, "updated": state.updated}).encode()
                self.send(HTTPStatus.OK, "application/json", body, cache=False)
            else:
                self.send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def send(self, status, content_type, body, cache=True) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=30" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()

    settings = load_settings(Path(args.config))
    state = FrameState()
    first_image, first_mode = produce_frame(settings, demo=args.demo)
    state.update(first_image, first_mode)
    thread = threading.Thread(target=refresh_loop, args=(state, settings, args.demo), daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), handler_factory(state))
    url = f"http://{args.host}:{args.port}/"
    print(f"Over-Head monitor running at {url}")
    print("Double-click the display to enter browser full-screen mode.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
