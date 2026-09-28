"""Presentation metadata for modules Rýchlik ships. Keyed by the file's SHA-256, so it can only ever describe the
exact bundled file: an edited copy has a different hash, gets no metadata, and is reported as changed by the registry.
The GUI never parses module source to find out what a module handles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_BUNDLED_DIR = Path(__file__).parent / "bundled"


@dataclass(frozen=True)
class ModuleDescription:
    name: str
    handles: tuple[str, ...]


_BUNDLED = {
    "media_sites.py": ModuleDescription("Media Sites", ("XVideos", "XNXX", "EPorner", "TGTube", "ShemaleZ")),
}


def bundled_module_path(file_name: str) -> Path:
    return _BUNDLED_DIR / file_name


def describe(sha256: str) -> ModuleDescription | None:
    for file_name, description in _BUNDLED.items():
        path = _BUNDLED_DIR / file_name
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
                return description
        except OSError:
            continue
    return None
