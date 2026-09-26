"""Deterministic local HTTP server for acquisition tests. No real internet dependency."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NORMAL_BODY = b"fake video bytes " * 1000  # ~17 KB, enough to exercise chunking


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence test output
        pass

    def do_GET(self):
        if self.path == "/normal.mp4":
            self._send_body(200, NORMAL_BODY, content_type="video/mp4")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/normal.mp4")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(NORMAL_BODY)))
            self.end_headers()
            for offset in range(0, len(NORMAL_BODY), 1024):
                self.wfile.write(NORMAL_BODY[offset : offset + 1024])
                self.wfile.flush()
                time.sleep(0.05)
        elif self.path == "/notfound":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/servererror":
            self.send_response(500)
            self.end_headers()
        elif self.path == "/empty":
            self._send_body(200, b"", content_type="video/mp4")
        elif self.path == "/with-content-disposition":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", 'attachment; filename="named-file.mp4"')
            self.send_header("Content-Length", str(len(NORMAL_BODY)))
            self.end_headers()
            self.wfile.write(NORMAL_BODY)
        elif self.path == "/track":
            # Real-concurrency proof for Prompt A5: increments a shared
            # active-request counter, holds briefly, decrements. The test
            # process reads HttpFixtureServer.max_observed_active directly
            # (same process, no extra HTTP round trip needed) instead of
            # guessing overlap from wall-clock timing.
            server = self.server
            with server.track_lock:
                server.active_count += 1
                server.max_observed_active = max(server.max_observed_active, server.active_count)
            time.sleep(0.3)
            with server.track_lock:
                server.active_count -= 1
            self._send_body(200, NORMAL_BODY, content_type="video/mp4")
        else:
            self.send_response(404)
            self.end_headers()

    def _send_body(self, status: int, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpFixtureServer:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server.track_lock = threading.Lock()
        self._server.active_count = 0
        self._server.max_observed_active = 0

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def max_observed_active(self) -> int:
        with self._server.track_lock:
            return self._server.max_observed_active

    def reset_track(self) -> None:
        """The fixture server is session-scoped; call this before each test
        that reads max_observed_active to avoid cross-test contamination."""
        with self._server.track_lock:
            self._server.active_count = 0
            self._server.max_observed_active = 0

    def start(self) -> "HttpFixtureServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
