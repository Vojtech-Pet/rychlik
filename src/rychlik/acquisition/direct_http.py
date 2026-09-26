"""DirectHttpAcquisition: conservative HTTP/HTTPS download backend.

Scope (Prompt 04.5): HTTP 200, redirects, stream to a `.part` temp file,
atomic finalization, cancel, basic progress, basic filename resolution.
Explicitly NOT in scope: resume, segmented/parallel ranges, cookies.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from rychlik.acquisition.contracts import (
    AcquisitionError,
    CompletedDownload,
    DownloadCancelled,
    DownloadRequest,
)

_CHUNK_SIZE = 64 * 1024
_TIMEOUT = (10, 30)  # (connect, read) seconds, matches legacy downloader style

ProgressCallback = Callable[[int, int | None], None]


class DirectHttpAcquisition:
    """Downloads a single URL to destination_dir via a `.part` temp file."""

    def acquire(
        self,
        request: DownloadRequest,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CompletedDownload:
        request.destination_dir.mkdir(parents=True, exist_ok=True)

        try:
            response = requests.get(request.url, stream=True, timeout=_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AcquisitionError(f"failed to fetch {request.url!r}: {exc}") from exc

        filename = self._resolve_filename(request, response)
        final_path = request.destination_dir / filename
        part_path = final_path.with_name(final_path.name + ".part")

        total_size = _content_length(response)
        bytes_written = 0

        try:
            with response, part_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled(f"download of {request.url!r} was cancelled")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    bytes_written += len(chunk)
                    if progress_callback is not None:
                        progress_callback(bytes_written, total_size)
                handle.flush()
                os.fsync(handle.fileno())
        except (DownloadCancelled, AcquisitionError):
            part_path.unlink(missing_ok=True)
            raise
        except requests.RequestException as exc:
            part_path.unlink(missing_ok=True)
            raise AcquisitionError(f"connection failed during download of {request.url!r}: {exc}") from exc

        if bytes_written == 0:
            part_path.unlink(missing_ok=True)
            raise AcquisitionError(f"empty response body for {request.url!r}")

        os.replace(part_path, final_path)  # atomic on same filesystem

        return CompletedDownload(
            final_path=final_path,
            display_name=filename,
            source_url=request.url,
            mime_type=response.headers.get("Content-Type"),
            size=final_path.stat().st_size,
        )

    @staticmethod
    def _resolve_filename(request: DownloadRequest, response: requests.Response) -> str:
        if request.filename_hint:
            return request.filename_hint

        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            filename = _filename_from_content_disposition(content_disposition)
            if filename:
                return filename

        url_path = urlparse(response.url).path
        candidate = Path(unquote(url_path)).name
        return candidate or "download"


_FILENAME_RE = re.compile(r'filename\*?="?([^";]+)"?', re.IGNORECASE)


def _filename_from_content_disposition(header_value: str) -> str | None:
    match = _FILENAME_RE.search(header_value)
    if not match:
        return None
    raw = match.group(1).strip()
    if raw.lower().startswith("utf-8''"):
        raw = unquote(raw[len("utf-8''"):])
    return Path(raw).name or None


def _content_length(response: requests.Response) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
