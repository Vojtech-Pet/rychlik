"""DirectHttpAcquisition: conservative HTTP/HTTPS download backend.

Scope (Prompt 04.5): HTTP 200, redirects, stream to a `.part` temp file,
atomic finalization, cancel, basic progress, basic filename resolution.

Prompt A9 extension: cooperative pause (a `pause_event`, checked between
chunks, never thread suspension) and safe validated partial resume (a
`resume: ResumeRequest | None` -- see rychlik.acquisition.contracts).
Both are additive and optional; omitting them reproduces exact Prompt
04.5 behavior. The central safety rule: a `.part` file is NEVER appended
to on the strength of its mere existence or size -- only a caller-
provided, ALREADY locally-validated `resume.initial_partial` (path
ownership + durable_bytes + prefix SHA-256, see
rychlik.core.partial_transfer) is even considered, and even then only a
matching remote validator (`If-Range`) and a structurally valid `206`
response are accepted. Any inconsistency -- a `200` fallback, a malformed
`206`, a `416`, a validator mismatch, a wrong Content-Range start/total --
discards the resume attempt and performs exactly one clean full restart
from byte 0. No segmented/multipart downloading exists; one sequential
stream per attempt.
"""

from __future__ import annotations

import hashlib
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
    DownloadPaused,
    DownloadRequest,
    ResumeRequest,
)
from rychlik.core.partial_transfer import PartialTransferState, Validator, ValidatorKind, classify_validator

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
        pause_event: threading.Event | None = None,
        resume: ResumeRequest | None = None,
    ) -> CompletedDownload:
        request.destination_dir.mkdir(parents=True, exist_ok=True)

        offset = 0
        range_validator: Validator | None = None
        final_path: Path
        part_path: Path
        if resume is not None and resume.initial_partial is not None:
            offset = resume.initial_partial.durable_bytes
            range_validator = resume.initial_partial.validator
            final_path = resume.initial_partial.final_path
            part_path = resume.initial_partial.temp_path
        else:
            final_path = None  # resolved below once we have a response
            part_path = None

        headers: dict[str, str] = {}
        attempting_range = False
        if offset > 0 and range_validator is not None and range_validator.kind != ValidatorKind.NONE:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = range_validator.if_range_header
            headers["Accept-Encoding"] = "identity"
            attempting_range = True

        response = self._get(request.url, headers=headers)

        resuming = False
        if attempting_range:
            if response.status_code == 206:
                content_range_ok, total_from_range = _validate_content_range(
                    response.headers.get("Content-Range"), expected_start=offset
                )
                validator_ok = _validator_matches(response, range_validator)
                encoding_ok = response.headers.get("Content-Encoding", "identity") == "identity"
                expected_total = (
                    resume.initial_partial.expected_total_bytes if resume.initial_partial else None
                )
                total_consistent = (
                    expected_total is None or total_from_range is None or total_from_range == expected_total
                )
                if content_range_ok and validator_ok and encoding_ok and total_consistent:
                    resuming = True
                else:
                    response.close()
                    response = self._get(request.url, headers={"Accept-Encoding": "identity"})
                    self._raise_for_status(response, request.url)
                    offset = 0
            elif response.status_code == 416:
                # §64: never infer "already complete" from 416 -- one clean
                # full restart, exactly like a malformed/rejected 206.
                response.close()
                response = self._get(request.url, headers={"Accept-Encoding": "identity"})
                self._raise_for_status(response, request.url)
                offset = 0
            elif response.status_code == 200:
                # §61: server ignored Range/If-Range and is sending the full,
                # possibly-new representation -- use THIS response directly,
                # never append it to the old prefix.
                offset = 0
            else:
                self._raise_for_status(response, request.url)
        else:
            self._raise_for_status(response, request.url)

        if not resuming:
            offset = 0

        if final_path is None or not resuming:
            # Fresh download (no resume attempted, or resume fell back to a
            # full restart): resolve filename normally, exactly like
            # Prompt 04.5. A fallback-to-zero after a validator/Range
            # rejection deliberately does NOT reuse the old persisted
            # final_path/temp_path -- the representation may have changed
            # (§62), so filename resolution runs again from this response.
            filename = self._resolve_filename(request, response)
            final_path = request.destination_dir / filename
            part_path = final_path.with_name(final_path.name + ".part")

        range_total = None
        if resuming:
            _, range_total = _validate_content_range(response.headers.get("Content-Range"), expected_start=offset)
        total_size = (offset + _content_length(response)) if resuming else _content_length(response)
        if resuming and range_total is not None:
            total_size = range_total

        validator = classify_validator(
            etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified")
        )

        bytes_written = offset
        last_checkpoint_bytes = offset
        hasher = hashlib.sha256()
        if resuming and offset > 0:
            # §101: re-hash the already-locally-validated prefix once, so the
            # running digest covers durable_bytes + new bytes together. This
            # is the one-time O(partial size) cost the design accepts.
            with part_path.open("rb") as existing:
                while True:
                    chunk = existing.read(1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)

        mode = "r+b" if resuming else "wb"
        try:
            with response, part_path.open(mode) as handle:
                if resuming:
                    handle.seek(offset)
                else:
                    handle.truncate(0)

                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled(f"download of {request.url!r} was cancelled")

                    if pause_event is not None and pause_event.is_set():
                        handle.flush()
                        os.fsync(handle.fileno())
                        if resume is not None:
                            resume.save_partial(
                                _partial_state(
                                    resume, part_path, final_path, bytes_written, total_size,
                                    validator, hasher,
                                )
                            )
                        raise DownloadPaused(f"download of {request.url!r} was paused")

                    if not chunk:
                        continue
                    handle.write(chunk)
                    hasher.update(chunk)
                    bytes_written += len(chunk)
                    if progress_callback is not None:
                        progress_callback(bytes_written, total_size)

                    if (
                        resume is not None
                        and bytes_written - last_checkpoint_bytes >= resume.checkpoint_bytes_threshold
                    ):
                        handle.flush()
                        os.fsync(handle.fileno())
                        resume.save_partial(
                            _partial_state(
                                resume, part_path, final_path, bytes_written, total_size, validator, hasher
                            )
                        )
                        last_checkpoint_bytes = bytes_written

                handle.flush()
                os.fsync(handle.fileno())
        except DownloadPaused:
            raise  # §70/§71: the .part + durable checkpoint are deliberately preserved
        except DownloadCancelled:
            part_path.unlink(missing_ok=True)
            if resume is not None:
                resume.clear_partial()
            raise
        except AcquisitionError:
            part_path.unlink(missing_ok=True)
            if resume is not None:
                resume.clear_partial()
            raise
        except requests.RequestException as exc:
            raise AcquisitionError(f"connection failed during download of {request.url!r}: {exc}") from exc

        if bytes_written == 0:
            part_path.unlink(missing_ok=True)
            raise AcquisitionError(f"empty response body for {request.url!r}")

        os.replace(part_path, final_path)  # atomic on same filesystem
        if resume is not None:
            resume.clear_partial()

        return CompletedDownload(
            final_path=final_path,
            display_name=final_path.name,
            source_url=request.url,
            mime_type=response.headers.get("Content-Type"),
            size=final_path.stat().st_size,
        )

    @staticmethod
    def _get(url: str, *, headers: dict[str, str]) -> requests.Response:
        try:
            return requests.get(url, stream=True, timeout=_TIMEOUT, allow_redirects=True, headers=headers)
        except requests.RequestException as exc:
            raise AcquisitionError(f"failed to fetch {url!r}: {exc}") from exc

    @staticmethod
    def _raise_for_status(response: requests.Response, url: str) -> None:
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            response.close()
            raise AcquisitionError(f"failed to fetch {url!r}: {exc}") from exc

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


def _partial_state(
    resume: ResumeRequest,
    part_path: Path,
    final_path: Path,
    durable_bytes: int,
    total_size: int | None,
    validator: Validator,
    hasher,
) -> PartialTransferState:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return PartialTransferState(
        queue_entry_id=resume.queue_entry_id,
        task_id=resume.task_id,
        attempt_count_snapshot=resume.attempt_count,
        temp_path=part_path,
        final_path=final_path,
        durable_bytes=durable_bytes,
        expected_total_bytes=total_size,
        validator_kind=validator.kind,
        validator_value=validator.value,
        prefix_sha256=hasher.hexdigest(),
        created_at=now,
        updated_at=now,
    )


def _validator_matches(response: requests.Response, expected: Validator | None) -> bool:
    if expected is None or expected.kind == ValidatorKind.NONE:
        return False
    if expected.kind == ValidatorKind.STRONG_ETAG:
        return response.headers.get("ETag") == expected.value
    if expected.kind == ValidatorKind.LAST_MODIFIED:
        return response.headers.get("Last-Modified") == expected.value
    return False


_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)")


def _validate_content_range(header_value: str | None, *, expected_start: int) -> tuple[bool, int | None]:
    """§57/§58: returns (is_structurally_valid_and_matches_expected_start,
    total_if_known). Never trusts a Content-Range whose start does not
    exactly equal the requested durable offset."""
    if not header_value:
        return False, None
    match = _CONTENT_RANGE_RE.fullmatch(header_value.strip())
    if not match:
        return False, None
    start, end, total_raw = match.groups()
    start_i, end_i = int(start), int(end)
    if start_i != expected_start or end_i < start_i:
        return False, None
    if total_raw == "*":
        return True, None
    total_i = int(total_raw)
    if end_i >= total_i:
        return False, None
    return True, total_i


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
