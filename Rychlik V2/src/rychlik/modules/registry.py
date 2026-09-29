"""Registry and loader for user-added legacy site modules.

Flow: static inspection (AST, no import) -> explicit trust confirmation -> copy into the managed modules
directory -> SHA-256 recorded -> registered. A module's code is imported only while it is enabled and unchanged,
lazily, on first dispatch; a disabled, removed, changed or broken module is never executed. Importing is running
arbitrary Python: this is a trust decision, not a sandbox.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from rychlik.modules.legacy_adapter import (
    LegacyResolution,
    resolve_with_legacy_module,
)

_ID_RE = re.compile(r"[^a-z0-9_]+")
COMPAT_RESOLVER = "RESOLVER"
COMPAT_NEEDS_PROCESS_BRIDGE = "REQUIRES_MANAGED_PROCESS_BRIDGE"


class ModuleError(Exception):
    code = "MODULE_ERROR"


class TrustNotConfirmed(ModuleError):
    code = "TRUST_NOT_CONFIRMED"


class AmbiguousModuleMatch(ModuleError):
    code = "AMBIGUOUS_MODULE_MATCH"

    def __init__(self, module_ids: list[str]) -> None:
        super().__init__("more than one module claims this address: " + ", ".join(module_ids))
        self.module_ids = module_ids


class LoadStatus(Enum):
    NOT_LOADED = "NOT_LOADED"  # registered, enabled or not, never imported (yet)
    LOADED = "LOADED"
    CHANGED = "CHANGED"  # the file's SHA-256 no longer matches what was registered: revalidation required
    MISSING = "MISSING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Inspection:
    ok: bool
    compatibility: str
    problem: str = ""


@dataclass(frozen=True)
class ModuleInfo:
    module_id: str
    display_name: str
    source_path: str
    sha256: str
    enabled: bool
    compatibility: str
    load_status: LoadStatus
    last_error: str
    added_at_utc: str

    @property
    def dispatchable(self) -> bool:
        """What "Enabled" really means at runtime: chosen by the user AND safe to execute."""
        return self.enabled and self.compatibility == COMPAT_RESOLVER and self.load_status in (LoadStatus.NOT_LOADED, LoadStatus.LOADED)


@dataclass(frozen=True)
class HandlerMatch:
    module_id: str
    module: object


def default_modules_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "rychlik" / "modules"


def module_id_for(path: Path) -> str:
    stem = _ID_RE.sub("_", Path(path).stem.lower()).strip("_")
    if not stem:
        raise ModuleError("the module file needs a name made of letters or digits")
    return stem


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_module_source(path: Path) -> Inspection:
    """Static checks only: the file is parsed, never imported or executed."""
    try:
        source = Path(path).read_text("utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError) as exc:
        return Inspection(False, "UNKNOWN", f"the file cannot be read: {exc}")
    except SyntaxError as exc:
        return Inspection(False, "UNKNOWN", f"the file is not valid Python: {exc.msg} (line {exc.lineno})")
    top = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [name for name in ("can_handle", "download") if name not in top]
    if missing:
        return Inspection(False, "UNKNOWN", "a module must define " + " and ".join(f"{m}()" for m in missing) + " at the top level")
    compat = COMPAT_NEEDS_PROCESS_BRIDGE if "_set_process" in source else COMPAT_RESOLVER
    return Inspection(True, compat)


def _import(path: Path, module_id: str):
    spec = importlib.util.spec_from_file_location(f"rychlik_site_module_{module_id}", path)
    if spec is None or spec.loader is None:
        raise ModuleError("the file cannot be loaded as a Python module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - includes SystemExit from a badly written module
        raise ModuleError(f"the module failed to load: {exc.__class__.__name__}: {exc}") from exc
    if not callable(getattr(module, "can_handle", None)) or not callable(getattr(module, "download", None)):
        raise ModuleError("the module does not provide callable can_handle() and download()")
    return module


class ModuleRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else default_modules_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._loaded: dict[str, tuple[str, object]] = {}  # module_id -> (sha256, module)
        self._errors: dict[str, str] = {}

    @property
    def directory(self) -> Path:
        return self._dir

    # --- persistence -------------------------------------------------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self._dir / "registry.json"

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self._index_path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as exc:
            raise ModuleError(f"the module registry is unreadable: {exc}") from exc
        modules = data.get("modules") if isinstance(data, dict) else None
        return modules if isinstance(modules, dict) else {}

    def _write(self, modules: dict[str, dict]) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "modules": modules}, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, self._index_path)

    def _file(self, module_id: str) -> Path:
        return self._dir / f"{module_id}.py"

    # --- queries -------------------------------------------------------------------------------------------------------

    def list(self) -> list[ModuleInfo]:
        with self._lock:
            return [self._info(module_id, entry) for module_id, entry in sorted(self._read().items())]

    def get(self, module_id: str) -> ModuleInfo | None:
        return next((i for i in self.list() if i.module_id == module_id), None)

    def _info(self, module_id: str, entry: dict) -> ModuleInfo:
        path = self._file(module_id)
        error = self._errors.get(module_id, "")
        if not path.exists():
            status, error = LoadStatus.MISSING, "The module file is missing."
        elif _sha256(path) != entry.get("sha256"):
            status, error = LoadStatus.CHANGED, "The file changed after it was added. It is not run until it is added again."
        elif error:
            status = LoadStatus.ERROR
        else:
            loaded = self._loaded.get(module_id)
            status = LoadStatus.LOADED if loaded is not None and loaded[0] == entry.get("sha256") else LoadStatus.NOT_LOADED
        return ModuleInfo(
            module_id=module_id, display_name=entry.get("display_name", module_id), source_path=entry.get("source_path", ""),
            sha256=entry.get("sha256", ""), enabled=bool(entry.get("enabled")), compatibility=entry.get("compatibility", "UNKNOWN"),
            load_status=status, last_error=error, added_at_utc=entry.get("added_at_utc", ""),
        )

    # --- changes -------------------------------------------------------------------------------------------------------

    def add(self, source: Path, *, trust_confirmed: bool, enabled: bool = True) -> ModuleInfo:
        """Adds a local .py module. Nothing is imported here; the caller must have shown the trust warning."""
        source = Path(source)
        if not trust_confirmed:
            raise TrustNotConfirmed("modules are arbitrary Python code; adding one needs explicit confirmation")
        if source.suffix != ".py" or not source.is_file():
            raise ModuleError("choose a Python module file (.py)")
        inspection = inspect_module_source(source)
        if not inspection.ok:
            raise ModuleError(inspection.problem)
        module_id = module_id_for(source)
        with self._lock:
            index = self._read()
            if module_id in index:
                raise ModuleError(f"a module named “{module_id}” is already added; remove it first")
            shutil.copyfile(source, self._file(module_id))
            index[module_id] = {
                "display_name": module_id, "source_path": str(source), "sha256": _sha256(self._file(module_id)), "enabled": bool(enabled),
                "compatibility": inspection.compatibility, "added_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            self._write(index)
            self._errors.pop(module_id, None)
        return self.get(module_id)  # type: ignore[return-value]

    def remove(self, module_id: str) -> None:
        with self._lock:
            index = self._read()
            if module_id not in index:
                raise ModuleError("no such module")
            del index[module_id]
            self._write(index)
            self._loaded.pop(module_id, None)
            self._errors.pop(module_id, None)
            try:
                self._file(module_id).unlink()
            except FileNotFoundError:
                pass

    def set_enabled(self, module_id: str, enabled: bool) -> None:
        with self._lock:
            index = self._read()
            if module_id not in index:
                raise ModuleError("no such module")
            index[module_id]["enabled"] = bool(enabled)
            self._write(index)
            if not enabled:
                self._loaded.pop(module_id, None)  # a disabled module is unloaded and never called again
            self._errors.pop(module_id, None)  # a fresh enable is a fresh attempt

    # --- dispatch --------------------------------------------------------------------------------------------------------

    def _load(self, info: ModuleInfo):
        cached = self._loaded.get(info.module_id)
        if cached is not None and cached[0] == info.sha256:
            return cached[1]
        try:
            module = _import(self._file(info.module_id), info.module_id)
        except ModuleError as exc:
            self._errors[info.module_id] = str(exc)
            return None
        self._loaded[info.module_id] = (info.sha256, module)
        return module

    def find_handler(self, url: str) -> HandlerMatch | None:
        """0 matches -> None (normal download), 1 -> that module, 2+ -> AmbiguousModuleMatch (never filesystem order)."""
        matches: list[HandlerMatch] = []
        with self._lock:
            for info in self.list():
                if not info.dispatchable:
                    continue
                module = self._load(info)
                if module is None:
                    continue
                try:
                    if module.can_handle(url):
                        matches.append(HandlerMatch(info.module_id, module))
                except Exception as exc:  # noqa: BLE001 - one broken can_handle must not hide the others
                    self._errors[info.module_id] = f"can_handle failed: {exc.__class__.__name__}: {exc}"
                    self._loaded.pop(info.module_id, None)
        if len(matches) > 1:
            raise AmbiguousModuleMatch([m.module_id for m in matches])
        return matches[0] if matches else None

    def resolve(self, url: str, destination_dir: Path, *, cancel_event: threading.Event | None = None) -> LegacyResolution | None:
        match = self.find_handler(url)
        if match is None:
            return None
        return resolve_with_legacy_module(match.module, url, destination_dir, cancel_event=cancel_event)
