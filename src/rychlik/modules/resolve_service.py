"""Add-download resolution: decides whether a URL is handled by an enabled module, off the GUI thread.

    no matching module      -> PLAIN      (the caller continues with the ordinary download path, unchanged)
    exactly one module      -> RESOLVED   (a typed DownloadRequest for the queue; nothing is downloaded here)
    two or more modules     -> AMBIGUOUS  (explicit error; no fallback)
    matched module fails    -> FAILED     (explicit error naming the module; NO fallback to the plain path)
    cancelled               -> CANCELLED  (nothing to enqueue)
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.modules.catalog import describe
from rychlik.modules.legacy_adapter import ResolutionCancelled, ResolutionError, resolve_with_legacy_module
from rychlik.modules.registry import AmbiguousModuleMatch, ModuleError, ModuleRegistry


class ResolveKind(Enum):
    PLAIN = "PLAIN"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ResolveOutcome:
    kind: ResolveKind
    request: DownloadRequest | None = None
    module_id: str | None = None
    module_name: str | None = None
    message: str = ""
    diagnostics: tuple[str, ...] = ()


class ResolveJob:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.finished = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


def _clean(url: str) -> str:
    return "".join(c for c in url.strip() if unicodedata.category(c) not in {"Cc", "Cf"})


class ModuleResolveService:
    def __init__(self, registry: ModuleRegistry) -> None:
        self._registry = registry

    def _name(self, module_id: str) -> str:
        info = self._registry.get(module_id)
        description = describe(info.sha256) if info is not None else None
        return description.name if description else module_id

    def resolve(self, url: str, destination_dir: Path, cancel_event: threading.Event | None = None) -> ResolveOutcome:
        """Synchronous core (runs on a worker thread in start())."""
        url = _clean(url)
        try:
            match = self._registry.find_handler(url)
        except AmbiguousModuleMatch as exc:
            names = ", ".join(self._name(m) for m in exc.module_ids)
            return ResolveOutcome(ResolveKind.AMBIGUOUS, message=f"More than one enabled module can handle this URL ({names}). Disable one of the conflicting modules and try again.")
        except ModuleError as exc:  # an unreadable registry must not stop ordinary downloads
            return ResolveOutcome(ResolveKind.PLAIN, diagnostics=(f"module registry unavailable: {exc}",))
        if match is None:
            return ResolveOutcome(ResolveKind.PLAIN)
        name = self._name(match.module_id)
        try:
            resolution = resolve_with_legacy_module(match.module, url, destination_dir, cancel_event=cancel_event)
        except ResolutionCancelled:
            return ResolveOutcome(ResolveKind.CANCELLED, module_id=match.module_id, module_name=name)
        except ResolutionError as exc:
            return ResolveOutcome(ResolveKind.FAILED, module_id=match.module_id, module_name=name, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - never let a module take the worker thread down silently
            return ResolveOutcome(ResolveKind.FAILED, module_id=match.module_id, module_name=name, message=f"{exc.__class__.__name__}: {exc}")
        return ResolveOutcome(ResolveKind.RESOLVED, request=resolution.request, module_id=match.module_id, module_name=name, diagnostics=resolution.diagnostics)

    def start(self, url: str, destination_dir: Path, on_done: Callable[[ResolveJob, ResolveOutcome], None]) -> ResolveJob:
        """Resolves on a background thread; on_done runs on that thread, unless the job was cancelled first."""
        job = ResolveJob()

        def run() -> None:
            try:
                outcome = self.resolve(url, destination_dir, job.cancel_event)
            except Exception as exc:  # noqa: BLE001
                outcome = ResolveOutcome(ResolveKind.FAILED, message=f"{exc.__class__.__name__}: {exc}")
            finally:
                job.finished.set()
            if not job.cancelled:
                on_done(job, outcome)

        threading.Thread(target=run, name="module-resolve", daemon=True).start()
        return job
