"""Deterministic local HTTP server for acquisition tests. No real internet dependency."""

from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NORMAL_BODY = b"fake video bytes " * 1000  # ~17 KB, enough to exercise chunking
_RANGE_RE = re.compile(r"bytes=(\d+)-")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence test output
        pass

    def do_GET(self):
        self.server.request_log.append(self.path)
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
        elif self.path == "/unknown-length":
            # Real unknown-total-size proof for Prompt A7: no Content-Length
            # header at all, connection closed after the body so the client
            # cannot infer total size upfront (matches DirectHttpAcquisition's
            # total_bytes=None path when Content-Length is absent).
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.close_connection = True
            self.end_headers()
            for offset in range(0, len(NORMAL_BODY), 1024):
                self.wfile.write(NORMAL_BODY[offset : offset + 1024])
                self.wfile.flush()
                time.sleep(0.01)
        elif self.path.startswith("/flaky/"):
            # Real transient-failure proof for Prompt A6: fails deterministically
            # for the first N requests under a given key, then succeeds -- no
            # mock network in the retry E2E tests that use this.
            key = self.path[len("/flaky/") :]
            server = self.server
            with server.flaky_lock:
                server.flaky_counts[key] = server.flaky_counts.get(key, 0) + 1
                count = server.flaky_counts[key]
                fail_until = server.flaky_fail_until.get(key, 1)
            if count <= fail_until:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_body(200, NORMAL_BODY, content_type="video/mp4")
        elif self.path.startswith("/resumable/"):
            self._handle_resumable(self.path[len("/resumable/") :])
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_resumable(self, key: str) -> None:
        # Prompt A9: a generic, per-key-configurable Range/If-Range/ETag/
        # Last-Modified fixture. Every request's Range/If-Range headers are
        # recorded so tests can assert exactly what the client sent, with no
        # mock network involved.
        server = self.server
        with server.resumable_lock:
            config = dict(server.resumable_configs.get(key, {}))
            server.resumable_last_request[key] = {
                "range": self.headers.get("Range"),
                "if_range": self.headers.get("If-Range"),
            }

        body = config.get("body", NORMAL_BODY)
        etag = config.get("etag")
        last_modified = config.get("last_modified")
        slow = config.get("slow", False)
        range_header = self.headers.get("Range")
        if_range = self.headers.get("If-Range")

        if config.get("force_status") == 416 and range_header:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(body)}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if config.get("force_bad_206") and range_header:
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            # Deliberately wrong start, ignoring the actual requested offset.
            self.send_header("Content-Range", f"bytes 999999-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", "1")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(b"X")
            return

        serve_partial = False
        start = 0
        if range_header:
            match = _RANGE_RE.match(range_header)
            if match:
                start = int(match.group(1))
                validator_matches = True
                if if_range:
                    validator_matches = if_range == etag or if_range == last_modified
                serve_partial = validator_matches and 0 <= start < len(body)

        self.send_response(206 if serve_partial else 200)
        self.send_header("Content-Type", "video/mp4")
        if etag:
            self.send_header("ETag", etag)
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        if config.get("content_encoding"):
            self.send_header("Content-Encoding", config["content_encoding"])

        payload = body[start:] if serve_partial else body
        if serve_partial:
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()

        drop_after = config.get("drop_after_bytes")
        drop_once_key = config.get("drop_once_key")
        if drop_after is not None and drop_once_key is not None:
            with server.flaky_lock:
                already_dropped = server.flaky_counts.get(drop_once_key, 0) > 0
                if not already_dropped:
                    server.flaky_counts[drop_once_key] = 1
            if not already_dropped:
                # Real deterministic mid-transfer connection loss (Prompt A9
                # retry+resume proof): write a truncated prefix then close
                # the socket without finishing -- the client sees a real
                # broken connection, not a mocked exception.
                self.wfile.write(payload[:drop_after])
                self.wfile.flush()
                self.close_connection = True
                return

        if slow:
            write_size = config.get("slow_chunk_bytes", 1024)
            delay = config.get("slow_delay", 0.03)
            for offset in range(0, len(payload), write_size):
                self.wfile.write(payload[offset : offset + write_size])
                self.wfile.flush()
                time.sleep(delay)
        else:
            self.wfile.write(payload)

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
        self._server.flaky_lock = threading.Lock()
        self._server.flaky_counts = {}
        self._server.flaky_fail_until = {}
        self._server.resumable_lock = threading.Lock()
        self._server.resumable_configs = {}
        self._server.resumable_last_request = {}
        self._server.request_log = []

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

    def request_log(self) -> list[str]:
        """Every request path in arrival order (list.append is atomic)."""
        return list(self._server.request_log)

    def configure_flaky(self, key: str, *, fail_until: int) -> None:
        """Requests 1..fail_until to /flaky/<key> return 503; request
        fail_until+1 onward return 200. Use a fresh, test-unique `key` (the
        server is session-scoped) to avoid cross-test contamination."""
        with self._server.flaky_lock:
            self._server.flaky_counts[key] = 0
            self._server.flaky_fail_until[key] = fail_until

    def flaky_call_count(self, key: str) -> int:
        with self._server.flaky_lock:
            return self._server.flaky_counts.get(key, 0)

    def configure_resumable(
        self,
        key: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        body: bytes | None = None,
        slow: bool = False,
        force_status: int | None = None,
        force_bad_206: bool = False,
        content_encoding: str | None = None,
        slow_chunk_bytes: int = 1024,
        slow_delay: float = 0.03,
        drop_after_bytes: int | None = None,
        drop_once_key: str | None = None,
    ) -> None:
        """Configures (or reconfigures, mid-test -- e.g. to simulate a
        representation change) one /resumable/<key> route. Use a fresh,
        test-unique `key` (the server is session-scoped)."""
        with self._server.resumable_lock:
            self._server.resumable_configs[key] = {
                "etag": etag,
                "last_modified": last_modified,
                "body": body if body is not None else NORMAL_BODY,
                "slow": slow,
                "force_status": force_status,
                "force_bad_206": force_bad_206,
                "content_encoding": content_encoding,
                "slow_chunk_bytes": slow_chunk_bytes,
                "slow_delay": slow_delay,
                "drop_after_bytes": drop_after_bytes,
                "drop_once_key": drop_once_key,
            }

    def resumable_last_request(self, key: str) -> dict | None:
        with self._server.resumable_lock:
            return self._server.resumable_last_request.get(key)

    def start(self) -> "HttpFixtureServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
