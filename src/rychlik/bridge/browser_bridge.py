"""Loopback HTTP bridge for the browser extension (`POST /download`, `POST /formats`).

Anything on the machine can reach 127.0.0.1, and so can any web page a browser opens, so a request is served only when
ALL of these hold: the Host header is this loopback endpoint (DNS-rebinding guard), the `X-Rychlik-Token` header equals
the secret token (which a web page cannot know), the body is small valid JSON with an http(s) URL, and the caller is
inside the rate limit. Nothing is downloaded silently: an accepted request only asks the app to open the Add download
dialog, pre-filled, for the user to confirm.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

DEFAULT_PORT = 17655
MAX_BODY_BYTES = 64 * 1024
TOKEN_HEADER = "X-Rychlik-Token"
_EXTENSION_ORIGIN_SCHEMES = ("moz-extension://", "chrome-extension://")


@dataclass(frozen=True)
class BrowserDownload:
    url: str
    media: bool
    browser: str
    referrer: str
    video_format: str


class TokenStore:
    """A random secret in a 0600 file; the extension is given a copy once by the user."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def get(self) -> str:
        try:
            token = self._path.read_text("utf-8").strip()
            if len(token) >= 32:
                return token
        except FileNotFoundError:
            pass
        return self.regenerate()

    def regenerate(self) -> str:
        token = secrets.token_urlsafe(32)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
        os.replace(tmp, self._path)
        return token


class _RateLimit:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max, self._window = max_events, window_seconds
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._events and now - self._events[0] > self._window:
                self._events.popleft()
            if len(self._events) >= self._max:
                return False
            self._events.append(now)
            return True


class BrowserBridge:
    def __init__(
        self,
        token_store: TokenStore,
        on_download: Callable[[BrowserDownload], None],
        list_formats: Callable[[str, str, str], list[dict[str, str]]] | None = None,
        *,
        port: int = DEFAULT_PORT,
        max_requests_per_minute: int = 30,
    ) -> None:
        self._tokens = token_store
        self._on_download = on_download
        self._list_formats = list_formats
        self._requested_port = port
        self._limit = _RateLimit(max_requests_per_minute, 60.0)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self._requested_port

    def start(self) -> bool:
        """False when the port is taken (another Rýchlik or program); the app keeps working without the bridge."""
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # one request per connection: no idle keep-alive threads

            def log_message(self, *_args) -> None:
                pass

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                origin = self.headers.get("Origin", "")
                if origin.startswith(_EXTENSION_ORIGIN_SCHEMES):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").strip().lower()
                return host in (f"127.0.0.1:{bridge.port}", f"localhost:{bridge.port}")

            def do_OPTIONS(self) -> None:  # CORS preflight: only extension origins get any permission
                if not self._host_ok():
                    return self._reply(403, {"ok": False, "error": "bad host"})
                origin = self.headers.get("Origin", "")
                self.send_response(204)
                if origin.startswith(_EXTENSION_ORIGIN_SCHEMES):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                self._reply(405, {"ok": False, "error": "POST only"})

            def do_POST(self) -> None:
                if not self._host_ok():
                    return self._reply(403, {"ok": False, "error": "bad host"})
                supplied = self.headers.get(TOKEN_HEADER, "")
                if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), bridge._tokens.get().encode("utf-8")):
                    return self._reply(401, {"ok": False, "error": "unauthorized"})
                if self.path not in ("/download", "/formats"):
                    return self._reply(404, {"ok": False, "error": "unknown endpoint"})
                if not bridge._limit.allow():
                    return self._reply(429, {"ok": False, "error": "too many requests"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "bad length"})
                if length <= 0 or length > MAX_BODY_BYTES:
                    return self._reply(413 if length > MAX_BODY_BYTES else 400, {"ok": False, "error": "bad body size"})
                try:
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError
                    url = str(data.get("url", "")).strip()
                    scheme = urlparse(url).scheme
                    if scheme not in ("http", "https") or not urlparse(url).netloc:
                        raise ValueError
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "invalid request"})
                browser = str(data.get("browser", ""))[:20]
                referrer = str(data.get("referrer", ""))[:2000]
                if self.path == "/formats":
                    if bridge._list_formats is None:
                        return self._reply(501, {"ok": False, "error": "formats unavailable"})
                    try:
                        return self._reply(200, {"ok": True, "formats": bridge._list_formats(url, browser, referrer)})
                    except Exception as exc:  # noqa: BLE001 - reported as a bounded message
                        return self._reply(502, {"ok": False, "error": str(exc)[:200] or "formats failed"})
                bridge._on_download(BrowserDownload(
                    url=url, media=bool(data.get("media")), browser=browser, referrer=referrer,
                    video_format=str(data.get("format", "") or "")[:300],
                ))
                self._reply(202, {"ok": True})

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self._requested_port), Handler)
        except OSError:
            self._server = None
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="browser-bridge", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()


def media_options_for(download: BrowserDownload):
    """The typed download options for a page/stream the extension sent as media; None for a plain file link."""
    if not download.media:
        return None
    from rychlik.acquisition.contracts import MediaOptions

    referer = download.referrer if urlparse(download.referrer).scheme in ("http", "https") else None
    return MediaOptions(video_format=download.video_format.strip() or "bestvideo+bestaudio/best", referer=referer)
