"""Device Mode media preparation temp cache (Prompt A16 §53-57/§86-89/
§135-137).

A Python port of the same design already proven in
`friendsend/lib/receiver/temp_cache.dart` (A14): filenames derived only
from an internal, safely-sanitized id (never a declared/untrusted
filename, §57), an injectable clock for TTL tests, a startup sweep that
unconditionally clears anything physically present but not tracked in
this process's own manifest (crash recovery, §88), and cleanup that only
ever touches paths this cache itself allocated (§136/§137 -- never
arbitrary-path deletion from a corrupt/forged record).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]")
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # §89


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "rychlik" / "device-media"


def _safe_segment(raw: str) -> str:
    sanitized = _SAFE_SEGMENT_RE.sub("_", raw)
    return sanitized or "unknown"


@dataclass
class _Entry:
    directory: Path
    created_at: float


class DeviceMediaTempCache:
    def __init__(self, root: Path | None = None, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._root = root or _default_data_dir()
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    @property
    def root(self) -> Path:
        return self._root

    def allocate_directory(self, preparation_id: str) -> Path:
        """A fresh, private directory for exactly one preparation
        operation (§53) -- named only from `preparation_id`, never any
        source filename."""
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._root, stat.S_IRWXU)  # 0700, §54
        except OSError:
            pass
        directory = self._root / _safe_segment(preparation_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._entries[preparation_id] = _Entry(directory=directory, created_at=self._clock())
        return directory

    def discard(self, preparation_id: str) -> None:
        """§87 (failure)/§133 (rejected after preparation): removes the
        allocated directory immediately, regardless of TTL."""
        entry = self._entries.pop(preparation_id, None)
        directory = entry.directory if entry is not None else self._root / _safe_segment(preparation_id)
        self._remove_if_owned(directory)

    def mark_retained(self, preparation_id: str) -> None:
        """Refreshes the TTL clock for a successfully-prepared entry
        (§86: kept only until the terminal handoff outcome, or the TTL
        sweep, whichever comes first)."""
        if preparation_id in self._entries:
            self._entries[preparation_id].created_at = self._clock()

    def cleanup_expired(self) -> list[str]:
        now = self._clock()
        expired = [pid for pid, entry in self._entries.items() if now - entry.created_at >= self._ttl_seconds]
        for pid in expired:
            self.discard(pid)
        return expired

    def sweep_untracked_on_startup(self) -> None:
        """§88: on a fresh process, nothing is tracked yet in memory --
        every physical subdirectory here is a leftover from a prior
        process (crash, kill, or normal exit) with no way to know
        whether it was ever completed. Only removes directories directly
        under this cache's own root (§136/§137 -- never follows a
        record pointing outside its own tree)."""
        if not self._root.exists():
            return
        for child in self._root.iterdir():
            self._remove_if_owned(child)

    def _remove_if_owned(self, path: Path) -> None:
        try:
            resolved_root = self._root.resolve()
            resolved_path = path.resolve()
        except OSError:
            return
        # §136/§137: refuse to delete anything not structurally inside
        # this cache's own root, even if a caller passed a bogus id.
        if resolved_root not in resolved_path.parents and resolved_path != resolved_root:
            return
        if resolved_path == resolved_root:
            return
        if resolved_path.is_dir():
            shutil.rmtree(resolved_path, ignore_errors=True)
        elif resolved_path.exists():
            try:
                resolved_path.unlink()
            except OSError:
                pass
