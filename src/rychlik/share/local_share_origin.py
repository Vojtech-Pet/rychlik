"""LocalShareOrigin: local-only HTTP media origin for active ShareLinks.

Bound to 127.0.0.1 by default. This server serves bytes locally only; no
public tunnel, TLS termination, web page, or Open Graph metadata exists here
(see docs/LOCAL_SHARE_ORIGIN_RESULT.md for the full policy).

Route: GET/HEAD /media/<share_id>  — nothing else. The share_id never
touches the filesystem directly; it is only ever used as a repository
lookup key, so there is no path-traversal surface by construction.
"""

from __future__ import annotations

import logging
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from rychlik.core.artifact_integrity import ArtifactChanged, resolve_artifact_file
from rychlik.core.artifact_repository import ArtifactRepository
from rychlik.share.contracts import ShareStatus
from rychlik.share.range_parser import RangeOutcome, parse_range
from rychlik.share.share_link_repository import ShareLinkRepository

_LOGGER = logging.getLogger("rychlik.share_origin")

_MEDIA_PATH_RE = re.compile(r"^/media/([A-Za-z0-9_-]+)$")
_CHUNK_SIZE = 64 * 1024

# Deterministic HTTP status mapping (documented in docs/LOCAL_SHARE_ORIGIN_RESULT.md).
_DENIED_STATUS_CODES: dict[ShareStatus, int] = {
    ShareStatus.CREATING: HTTPStatus.NOT_FOUND,  # not yet active; avoid state oracle
    ShareStatus.OFFLINE: HTTPStatus.SERVICE_UNAVAILABLE,
    ShareStatus.EXPIRED: HTTPStatus.GONE,
    ShareStatus.REVOKED: HTTPStatus.GONE,
    ShareStatus.FAILED: HTTPStatus.NOT_FOUND,
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "_OriginHTTPServer"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # replaced by structured logging in _respond()/_serve()

    def do_HEAD(self) -> None:
        self._handle(send_body=False)

    def do_GET(self) -> None:
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        path = urlsplit(self.path).path
        match = _MEDIA_PATH_RE.match(path)
        if not match:
            self._empty_response(HTTPStatus.NOT_FOUND)
            return

        share_id = match.group(1)
        origin = self.server.origin

        link = origin.share_link_repository.get(share_id)
        if link is None:
            self._empty_response(HTTPStatus.NOT_FOUND, share_id=share_id, result="unknown_share")
            return

        if link.status != ShareStatus.ACTIVE:
            code = _DENIED_STATUS_CODES.get(link.status, HTTPStatus.NOT_FOUND)
            self._empty_response(code, share_id=share_id, result=f"denied_{link.status.name.lower()}")
            return

        artifact = origin.artifact_repository.get(link.artifact_id)
        if artifact is None:
            self._empty_response(HTTPStatus.GONE, share_id=share_id, result="artifact_missing")
            return

        try:
            resolved_path = resolve_artifact_file(artifact)
        except (FileNotFoundError, ArtifactChanged) as exc:
            self._empty_response(HTTPStatus.GONE, share_id=share_id, result=f"artifact_changed:{exc}")
            return
        except OSError:
            self._empty_response(HTTPStatus.INTERNAL_SERVER_ERROR, share_id=share_id, result="os_error")
            return

        file_size = artifact.size
        content_type = artifact.mime_type or "application/octet-stream"
        etag = f'"{artifact.sha256}"'

        parsed = parse_range(self.headers.get("Range"), file_size)

        if parsed.outcome == RangeOutcome.UNSATISFIABLE:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            _LOGGER.info("share_id=%s result=416_unsatisfiable", share_id)
            return

        if parsed.outcome == RangeOutcome.SINGLE:
            start, end = parsed.start, parsed.end
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
        else:
            start, length = 0, file_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", str(file_size))

        self.send_header("Content-Type", content_type)
        self.send_header("ETag", etag)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        _LOGGER.info(
            "share_id=%s status=%s range_start=%s bytes=%s result=served",
            share_id,
            link.status.name,
            start,
            length,
        )

        if not send_body or length == 0:
            return

        self._stream_file(resolved_path, start, length)

    def _stream_file(self, path: Path, start: int, length: int) -> None:
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream; nothing more to do

    def _empty_response(self, status: HTTPStatus, *, share_id: str | None = None, result: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()
        _LOGGER.info("share_id=%s http_status=%s result=%s", share_id, int(status), result)


class _OriginHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], origin: "LocalShareOrigin") -> None:
        super().__init__(address, _Handler)
        self.origin = origin


class LocalShareOrigin:
    """Owns server lifecycle only. Does not create ShareLinks, does not
    manage acquisition, does not transcode, does not own GUI state."""

    def __init__(
        self,
        *,
        share_link_repository: ShareLinkRepository,
        artifact_repository: ArtifactRepository,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.share_link_repository = share_link_repository
        self.artifact_repository = artifact_repository
        self._host = host
        self._port = port
        self._server: _OriginHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def address(self) -> tuple[str, int] | None:
        if self._server is None:
            return None
        return self._server.server_address[:2]

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return  # idempotent
            self._server = _OriginHTTPServer((self._host, self._port), self)
            self._thread = threading.Thread(
                target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._server is None:
                return  # idempotent
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None
            self._thread = None

    def media_url(self, share_id: str) -> str:
        host, port = self.address
        return f"http://{host}:{port}/media/{share_id}"
