"""Compatibility layer for legacy download modules (`can_handle(url)` + `download(worker)`).

Not a core API. A legacy module gets a throw-away worker, fills in its old `item` fields and calls
`worker._run_media()`; the adapter *finalises that as a resolver result*: it snapshots and validates the item and
produces a typed `DownloadRequest` (with `MediaOptions`) for Rýchlik's own queue. Nothing is downloaded here and the
legacy item object never leaves this module. Modules that start their own process (`_set_process`) are recognised
and refused until a core-owned managed-process bridge exists.
"""

from __future__ import annotations

import inspect
import re
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

from rychlik.acquisition.contracts import DownloadRequest, MediaOptions

_PLACEHOLDER_NAMES = frozenset({"", "Video zo stránky"})
_UNSAFE_NAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


class ResolutionError(Exception):
    code = "RESOLUTION_FAILED"


class ResolutionCancelled(ResolutionError):
    code = "RESOLUTION_CANCELLED"


class UnsupportedLegacyBehavior(ResolutionError):
    code = "LEGACY_UNSUPPORTED"


class RequiresManagedProcessBridge(UnsupportedLegacyBehavior):
    code = "REQUIRES_MANAGED_PROCESS_BRIDGE"


class MultipleAcquisitions(ResolutionError):
    code = "LEGACY_MULTIPLE_ACQUISITIONS"


class NoRequestProduced(ResolutionError):
    code = "LEGACY_NO_REQUEST"


@dataclass
class LegacyItem:
    """The old mutable `Download` fields that the audited modules touch."""

    url: str
    destination: str
    status: str = ""
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    media: bool = False
    display_name: str = ""
    auth_browser: str = ""
    source_url: str = ""
    referrer: str = ""
    max_segments: int = 4
    speed_limit: int = 0
    category: str = ""
    video_format: str = "bestvideo+bestaudio/best"


_KNOWN_FIELDS = frozenset(f.name for f in fields(LegacyItem))


@dataclass(frozen=True)
class ResolverEvent:
    status: str
    detail: str = ""


@dataclass(frozen=True)
class LegacyResolution:
    request: DownloadRequest
    events: tuple[ResolverEvent, ...]
    diagnostics: tuple[str, ...]


def requires_process_bridge(module) -> bool:
    """A module that hands its own subprocess to the worker cannot be adapted (yet)."""
    try:
        return "_set_process" in inspect.getsource(module)
    except (OSError, TypeError):
        return False


def _sanitize_filename(name: str) -> str | None:
    name = name.strip()
    if name in _PLACEHOLDER_NAMES:
        return None
    base = Path(name.replace("\\", "/")).name  # no directory parts, no traversal
    base = _UNSAFE_NAME.sub("_", base).strip(" .")[:180]
    return base or None


def _require_http(url: str, what: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ResolutionError(f"{what} is not an http(s) address")
    return url


class LegacyWorkerAdapter:
    """The `worker` a legacy module sees. Only the surface the audited modules use exists."""

    def __init__(self, url: str, destination_dir: Path, cancel_event: threading.Event | None = None) -> None:
        self._destination_dir = Path(destination_dir)
        self._cancel = cancel_event or threading.Event()  # legacy name: modules call worker._cancel.wait()/is_set()
        self.item = LegacyItem(url=url, destination=str(self._destination_dir), media=True)
        self._events: list[ResolverEvent] = []
        self._diagnostics: list[str] = []
        self._request: DownloadRequest | None = None
        self._media_calls = 0

    # --- legacy surface -------------------------------------------------------------------------------------------

    def update(self, item: LegacyItem) -> None:
        self._events.append(ResolverEvent(status=str(getattr(item, "status", "") or ""), detail=str(getattr(item, "error", "") or "")))

    def _run_media(self) -> None:
        if self._cancel.is_set():
            raise ResolutionCancelled("cancelled while resolving")
        self._media_calls += 1
        if self._media_calls > 1:
            raise MultipleAcquisitions("the module asked for more than one download in a single resolve")
        self._request = self._translate(self.item)  # snapshot NOW: later mutation of the legacy item changes nothing

    def _set_process(self, process) -> None:
        if process is not None:
            _kill(process)
        raise RequiresManagedProcessBridge("this module runs its own download process, which needs the managed-process bridge (not available yet)")

    def _terminate_process(self) -> None:
        raise RequiresManagedProcessBridge("process control is not available to legacy modules yet")

    # --- translation --------------------------------------------------------------------------------------------------

    def _translate(self, item: LegacyItem) -> DownloadRequest:
        for name in vars(item):
            if name not in _KNOWN_FIELDS:
                self._diagnostics.append(f"ignored unknown legacy field: {name}")
        if item.auth_browser:
            self._diagnostics.append("ignored auth_browser: browser cookies are not handed to modules")
        if Path(item.destination) != self._destination_dir:
            self._diagnostics.append("ignored module-chosen destination; the download goes where the user chose")
        url = _require_http(item.url, "the resolved address")
        referer = item.referrer.strip() or None
        if referer is not None:
            referer = _require_http(referer, "the referer")
        video_format = (item.video_format or "").strip()
        if not video_format:
            raise ResolutionError("the module returned an empty video format")
        limit = int(item.speed_limit) if item.speed_limit and item.speed_limit > 0 else None
        try:
            media = MediaOptions(video_format=video_format, referer=referer, rate_limit_bytes_per_second=limit)
            return DownloadRequest(url=url, destination_dir=self._destination_dir, filename_hint=_sanitize_filename(item.display_name), media=media)
        except ValueError as exc:
            raise ResolutionError(str(exc)) from exc

    # --- outcome ---------------------------------------------------------------------------------------------------------

    def finish(self) -> LegacyResolution:
        if self._cancel.is_set():
            raise ResolutionCancelled("cancelled while resolving")
        if self._request is None:
            raise NoRequestProduced("the module finished without producing a download")
        return LegacyResolution(self._request, tuple(self._events), tuple(self._diagnostics))


def _kill(process) -> None:
    import os
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - best effort; a refused module's process must not survive
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def resolve_with_legacy_module(module, url: str, destination_dir: Path, *, cancel_event: threading.Event | None = None) -> LegacyResolution:
    if requires_process_bridge(module):
        raise RequiresManagedProcessBridge("recognised, but it runs its own process: needs the managed-process bridge")
    worker = LegacyWorkerAdapter(url, destination_dir, cancel_event)
    try:
        module.download(worker)
    except ResolutionError:
        raise
    except Exception as exc:  # noqa: BLE001 - a module bug or a site change is a resolution failure, not a crash
        raise ResolutionError(str(exc) or exc.__class__.__name__) from exc
    return worker.finish()
