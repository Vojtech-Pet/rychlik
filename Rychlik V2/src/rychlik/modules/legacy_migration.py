"""One-off import policy for the historical `download_modules/` folder (audit: docs/LEGACY_MODULES_AUDIT.md).

Selecting that whole folder registers only the audited allowlist. Everything else in it is reported and skipped;
arbitrary user modules are added one by one through ModuleRegistry.add with explicit confirmation instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rychlik.modules.registry import ModuleError, ModuleInfo, ModuleRegistry

ALLOWED_LEGACY_MODULES = (
    "xvideos", "xnxx", "eporner", "tgtube", "shemalez",
    "pornhub", "ashemaletube", "shemaletubevideos", "ladyboygold", "trannyvideosx",
    "shez_tube",
)


@dataclass
class ImportReport:
    added: list[ModuleInfo] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # file name -> reason


def import_legacy_directory(directory: Path, registry: ModuleRegistry, *, trust_confirmed: bool) -> ImportReport:
    report = ImportReport()
    for path in sorted(Path(directory).glob("*.py")):
        stem = path.stem
        if stem not in ALLOWED_LEGACY_MODULES:
            report.skipped[path.name] = "not part of the audited migration allowlist"
            continue
        try:
            report.added.append(registry.add(path, trust_confirmed=trust_confirmed))
        except ModuleError as exc:
            report.skipped[path.name] = str(exc)
    return report
