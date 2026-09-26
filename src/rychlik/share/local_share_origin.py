"""LocalShareOrigin: local-only HTTP media origin for active ShareLinks.

Bound to 127.0.0.1 by default. This server serves bytes locally only; no
public tunnel, TLS termination, or public internet exposure exists here
(see docs/LOCAL_SHARE_ORIGIN_RESULT.md and docs/SHARE_PAGE_RESULT.md for
the full policy).

Routes:
    GET/HEAD /media/<share_id>    binary media, ACTIVE-only (Prompt 07)
    GET/HEAD /s/<share_id>        server-rendered HTML share page (Prompt 09)
    GET/HEAD /preview/<share_id>  thumbnail image (Prompt 09)

share_id never touches the filesystem directly; it is only ever used as a
repository lookup key, so there is no path-traversal surface by construction.

/s/ and /preview/ are read-only, side-effect-free lookups: a crawler
fetching either one never changes ShareLink/session state, unlike an
eventual real media viewer session (see docs/SHARE_PAGE_RESULT.md "Crawler
safety").
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from rychlik.core.artifact_integrity import ArtifactChanged, resolve_artifact_file
from rychlik.core.artifact_repository import ArtifactRepository
from rychlik.share.contracts import ShareStatus
from rychlik.share.public_url_builder import PublicUrlBuilder
from rychlik.share.range_parser import RangeOutcome, parse_range
from rychlik.share.share_link_repository import ShareLinkRepository
from rychlik.share.share_page_renderer import render_not_found_page, render_share_page
from rychlik.share.share_preview_service import SharePreviewService

_LOGGER = logging.getLogger("rychlik.share_origin")

_MEDIA_PATH_RE = re.compile(r"^/media/([A-Za-z0-9_-]+)$")
_SHARE_PAGE_RE = re.compile(r"^/s/([A-Za-z0-9_-]+)$")
_PREVIEW_RE = re.compile(r"^/preview/([A-Za-z0-9_-]+)$")
_CHUNK_SIZE = 64 * 1024

# Deterministic HTTP status mapping for /media (docs/LOCAL_SHARE_ORIGIN_RESULT.md).
_MEDIA_DENIED_STATUS_CODES: dict[ShareStatus, int] = {
    ShareStatus.CREATING: HTTPStatus.NOT_FOUND,  # not yet active; avoid state oracle
    ShareStatus.OFFLINE: HTTPStatus.SERVICE_UNAVAILABLE,
    ShareStatus.EXPIRED: HTTPStatus.GONE,
    ShareStatus.REVOKED: HTTPStatus.GONE,
    ShareStatus.FAILED: HTTPStatus.NOT_FOUND,
}

# Deterministic HTTP status mapping shared by /s (page) and /preview (image),
# documented in docs/SHARE_PAGE_RESULT.md. CREATING/ACTIVE are the only
# statuses that render content; every other status returns an empty (for
# /preview) or state-specific (for /s) body with no thumbnail/media exposed.
_PAGE_STATUS_CODES: dict[ShareStatus, HTTPStatus] = {
    ShareStatus.CREATING: HTTPStatus.OK,
    ShareStatus.ACTIVE: HTTPStatus.OK,
    ShareStatus.OFFLINE: HTTPStatus.SERVICE_UNAVAILABLE,
    ShareStatus.EXPIRED: HTTPStatus.GONE,
    ShareStatus.REVOKED: HTTPStatus.GONE,
    ShareStatus.FAILED: HTTPStatus.NOT_FOUND,
}

_REQUEST_LOG_MAXLEN = 500


@dataclass(frozen=True)
class RequestLogEntry:
    """Safe, sanitized record of one request (Prompt 13 §6).

    Never includes: secret, cookies, source_url, local filesystem paths, or
    authorization material — those simply aren't in scope here; this only
    ever sees what the HTTP layer itself sees (route, method, headers,
    status, byte count).
    """

    timestamp: datetime
    route: str  # "page" | "preview" | "media" | "unknown"
    method: str  # "GET" | "HEAD"
    share_id: str | None
    http_status: int
    user_agent: str | None
    range_header: str | None
    bytes_served: int
    result: str


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; "
        "script-src 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Robots-Tag": "noindex, nofollow",
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "_OriginHTTPServer"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # replaced by structured logging in the _handle_* methods

    def _record(
        self,
        *,
        route: str,
        share_id: str | None,
        status: int,
        bytes_served: int = 0,
        result: str = "",
    ) -> None:
        entry = RequestLogEntry(
            timestamp=datetime.now(timezone.utc),
            route=route,
            method=self.command,
            share_id=share_id,
            http_status=status,
            user_agent=self.headers.get("User-Agent"),
            range_header=self.headers.get("Range"),
            bytes_served=bytes_served,
            result=result,
        )
        self.server.origin._append_request_log(entry)

    def do_HEAD(self) -> None:
        self._handle(send_body=False)

    def do_GET(self) -> None:
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        path = urlsplit(self.path).path

        match = _SHARE_PAGE_RE.match(path)
        if match:
            self._handle_share_page(match.group(1), send_body=send_body)
            return

        match = _PREVIEW_RE.match(path)
        if match:
            self._handle_preview(match.group(1), send_body=send_body)
            return

        match = _MEDIA_PATH_RE.match(path)
        if match:
            self._handle_media(match.group(1), send_body=send_body)
            return

        self._empty_response(HTTPStatus.NOT_FOUND)

    # --- /s/<share_id> ----------------------------------------------------

    def _handle_share_page(self, share_id: str, *, send_body: bool) -> None:
        origin = self.server.origin
        link = origin.share_link_repository.get(share_id)

        if link is None:
            self._send_html(HTTPStatus.NOT_FOUND, render_not_found_page(), send_body=send_body)
            _LOGGER.info("share_id=%s route=page result=unknown_share", share_id)
            self._record(route="page", share_id=share_id, status=404, result="unknown_share")
            return

        status_code = _PAGE_STATUS_CODES.get(link.status, HTTPStatus.NOT_FOUND)
        artifact = origin.artifact_repository.get(link.artifact_id)
        preview = self._resolve_preview(origin, artifact, link.artifact_id)
        media_mime_type = artifact.mime_type if artifact is not None else None

        body = render_share_page(
            status=link.status,
            share_id=share_id,
            preview=preview,
            urls=origin.base_url_builder,
            media_mime_type=media_mime_type,
        )
        self._send_html(status_code, body, send_body=send_body)
        _LOGGER.info("share_id=%s route=page status=%s result=served", share_id, link.status.name)
        self._record(
            route="page", share_id=share_id, status=int(status_code), result=f"served_{link.status.name.lower()}"
        )

    def _send_html(self, status: HTTPStatus, body: str, *, send_body: bool) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # --- /preview/<share_id> ----------------------------------------------

    def _handle_preview(self, share_id: str, *, send_body: bool) -> None:
        origin = self.server.origin
        link = origin.share_link_repository.get(share_id)

        if link is None:
            self._empty_response(HTTPStatus.NOT_FOUND, share_id=share_id, result="unknown_share", route="preview")
            return

        status_code = _PAGE_STATUS_CODES.get(link.status, HTTPStatus.NOT_FOUND)
        if status_code != HTTPStatus.OK:
            self._empty_response(
                status_code,
                share_id=share_id,
                result=f"denied_{link.status.name.lower()}",
                route="preview",
            )
            return

        artifact = origin.artifact_repository.get(link.artifact_id)
        preview = self._resolve_preview(origin, artifact, link.artifact_id)
        if preview is None or preview.thumbnail_path is None:
            self._empty_response(HTTPStatus.NOT_FOUND, share_id=share_id, result="no_thumbnail", route="preview")
            return

        try:
            if not preview.thumbnail_path.is_file():
                self._empty_response(
                    HTTPStatus.NOT_FOUND, share_id=share_id, result="thumbnail_missing", route="preview"
                )
                return
            size = preview.thumbnail_path.stat().st_size
        except OSError:
            self._empty_response(
                HTTPStatus.INTERNAL_SERVER_ERROR, share_id=share_id, result="os_error", route="preview"
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", preview.thumbnail_mime or "image/jpeg")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        _LOGGER.info("share_id=%s route=preview result=served bytes=%s", share_id, size)
        self._record(route="preview", share_id=share_id, status=200, bytes_served=size, result="served")

        if send_body and size > 0:
            self._stream_file(preview.thumbnail_path, 0, size)

    @staticmethod
    def _resolve_preview(origin: "LocalShareOrigin", artifact, artifact_id: str):
        if artifact is None:
            return None
        try:
            resolve_artifact_file(artifact)
        except (FileNotFoundError, ArtifactChanged):
            return None
        return origin.share_preview_service.get_or_create_preview(artifact_id)

    # --- /media/<share_id> (Prompt 07) -------------------------------------

    def _handle_media(self, share_id: str, *, send_body: bool) -> None:
        origin = self.server.origin

        link = origin.share_link_repository.get(share_id)
        if link is None:
            self._empty_response(HTTPStatus.NOT_FOUND, share_id=share_id, result="unknown_share", route="media")
            return

        if link.status != ShareStatus.ACTIVE:
            code = _MEDIA_DENIED_STATUS_CODES.get(link.status, HTTPStatus.NOT_FOUND)
            self._empty_response(
                code, share_id=share_id, result=f"denied_{link.status.name.lower()}", route="media"
            )
            return

        artifact = origin.artifact_repository.get(link.artifact_id)
        if artifact is None:
            self._empty_response(HTTPStatus.GONE, share_id=share_id, result="artifact_missing", route="media")
            return

        try:
            resolved_path = resolve_artifact_file(artifact)
        except (FileNotFoundError, ArtifactChanged) as exc:
            self._empty_response(
                HTTPStatus.GONE, share_id=share_id, result=f"artifact_changed:{exc}", route="media"
            )
            return
        except OSError:
            self._empty_response(
                HTTPStatus.INTERNAL_SERVER_ERROR, share_id=share_id, result="os_error", route="media"
            )
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
            self._record(route="media", share_id=share_id, status=416, result="416_unsatisfiable")
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
        served_status = 206 if parsed.outcome == RangeOutcome.SINGLE else 200
        self._record(route="media", share_id=share_id, status=served_status, bytes_served=length, result="served")

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

    def _empty_response(
        self,
        status: HTTPStatus,
        *,
        share_id: str | None = None,
        result: str = "",
        route: str = "unknown",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()
        _LOGGER.info("share_id=%s http_status=%s result=%s", share_id, int(status), result)
        self._record(route=route, share_id=share_id, status=int(status), result=result or "denied")


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
        share_preview_service: SharePreviewService,
        host: str = "127.0.0.1",
        port: int = 0,
        base_url: str | None = None,
    ) -> None:
        self.share_link_repository = share_link_repository
        self.artifact_repository = artifact_repository
        self.share_preview_service = share_preview_service
        self._host = host
        self._port = port
        self._base_url_override = base_url
        self._server: _OriginHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._request_log: deque[RequestLogEntry] = deque(maxlen=_REQUEST_LOG_MAXLEN)
        self._request_log_lock = threading.Lock()

    def _append_request_log(self, entry: RequestLogEntry) -> None:
        with self._request_log_lock:
            self._request_log.append(entry)

    @property
    def request_log(self) -> list[RequestLogEntry]:
        """Snapshot of the most recent requests (Prompt 13 experiment support).

        Bounded to the last _REQUEST_LOG_MAXLEN entries, in-memory only, not
        persisted. Safe by construction: RequestLogEntry never carries
        secret/cookie/source_url/local-path fields.
        """
        with self._request_log_lock:
            return list(self._request_log)

    @property
    def address(self) -> tuple[str, int] | None:
        if self._server is None:
            return None
        return self._server.server_address[:2]

    @property
    def base_url_builder(self) -> PublicUrlBuilder:
        if self._base_url_override is not None:
            return PublicUrlBuilder(self._base_url_override)
        host, port = self.address
        return PublicUrlBuilder(f"http://{host}:{port}")

    def set_base_url(self, base_url: str | None) -> None:
        """Point og:url/og:image/media links at a public tunnel URL discovered
        after start() (the public URL from DevelopmentTunnelProvider is only
        known after the local origin is already bound and the tunnel has
        connected). Pass None to revert to the local 127.0.0.1:<port> default.

        Found via Prompt 13's live WhatsApp experiment: without this, a
        share page correctly reachable at a public tunnel URL still emits
        og:image/og:url pointing at 127.0.0.1, which is unreachable from
        outside this machine -- the crawler fetches the HTML successfully
        but silently fails to fetch the (unreachable) image and falls back
        to a bare-domain card. See docs/WHATSAPP_LINK_PREVIEW_RESULT.md.
        """
        self._base_url_override = base_url

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
        return self.base_url_builder.media_url(share_id)

    def share_page_url(self, share_id: str) -> str:
        return self.base_url_builder.share_page_url(share_id)

    def preview_url(self, share_id: str) -> str:
        return self.base_url_builder.preview_url(share_id)
