"""M4: module registry + loader (persistence, trust, hash-change detection, isolation, deterministic dispatch)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from rychlik.modules.legacy_migration import ALLOWED_LEGACY_MODULES, import_legacy_directory
from rychlik.modules.registry import (
    AmbiguousModuleMatch, LoadStatus, ModuleError, ModuleRegistry, TrustNotConfirmed, inspect_module_source,
    COMPAT_NEEDS_PROCESS_BRIDGE, COMPAT_RESOLVER,
)

LEGACY_DIR = Path("/home/vojtech/Stiahnuté/rychlik-downloader/download_modules")
GOOD = """
def can_handle(url):
    return url.startswith("https://{host}/")

def download(worker):
    worker.item.url = "https://cdn.{host}/v.mp4"
    worker._run_media()
"""


def make_module(tmp_path, name, host=None, source=None):
    path = tmp_path / "src" / f"{name}.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source if source is not None else GOOD.format(host=host or name + ".example"), "utf-8")
    return path


@pytest.fixture
def registry(tmp_path):
    return ModuleRegistry(tmp_path / "modules")


def test_add_requires_explicit_trust_and_never_imports_at_add_time(tmp_path, registry):
    marker = tmp_path / "imported.txt"
    path = make_module(tmp_path, "sneaky", source=f"open({str(marker)!r}, 'w').write('x')\n" + GOOD.format(host="a.example"))
    with pytest.raises(TrustNotConfirmed):
        registry.add(path, trust_confirmed=False)
    assert registry.list() == []
    info = registry.add(path, trust_confirmed=True)
    assert not marker.exists()  # registered from static inspection only: still not executed
    assert info.load_status == LoadStatus.NOT_LOADED and info.enabled and info.compatibility == COMPAT_RESOLVER
    assert registry.find_handler("https://a.example/x").module_id == "sneaky"  # first dispatch imports it
    assert marker.exists()


def test_install_persists_and_a_fresh_process_still_dispatches(tmp_path, registry):
    registry.add(make_module(tmp_path, "site1", "one.example"), trust_confirmed=True)
    code = textwrap.dedent(f"""
        from pathlib import Path
        from rychlik.modules.registry import ModuleRegistry
        r = ModuleRegistry(Path({str(registry.directory)!r}))
        assert [(i.module_id, i.enabled) for i in r.list()] == [("site1", True)]
        m = r.find_handler("https://one.example/v")
        assert m is not None and m.module_id == "site1"
        print("FRESH_OK")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": "src", "PATH": "/usr/bin"}, cwd=Path(__file__).parent.parent)
    assert "FRESH_OK" in out.stdout, out.stderr


def test_enable_disable_take_effect_immediately_and_disabled_code_is_never_imported(tmp_path, registry):
    marker = tmp_path / "imported.txt"
    path = make_module(tmp_path, "later", source=f"open({str(marker)!r}, 'a').write('x')\n" + GOOD.format(host="l.example"))
    registry.add(path, trust_confirmed=True, enabled=False)
    assert registry.find_handler("https://l.example/x") is None and not marker.exists()  # disabled: not imported at all
    registry.set_enabled("later", True)
    assert registry.find_handler("https://l.example/x").module_id == "later"
    registry.set_enabled("later", False)
    assert registry.find_handler("https://l.example/x") is None
    assert registry.get("later").load_status == LoadStatus.NOT_LOADED


def test_remove_deletes_registration_and_managed_file_and_stops_dispatch(tmp_path, registry):
    registry.add(make_module(tmp_path, "gone", "g.example"), trust_confirmed=True)
    assert registry.find_handler("https://g.example/x")
    registry.remove("gone")
    assert registry.list() == [] and not (registry.directory / "gone.py").exists()
    assert registry.find_handler("https://g.example/x") is None
    with pytest.raises(ModuleError):
        registry.remove("gone")


def test_the_original_file_is_copied_so_moving_it_does_not_break_the_registry(tmp_path, registry):
    src = make_module(tmp_path, "moved", "m.example")
    registry.add(src, trust_confirmed=True)
    src.unlink()
    assert registry.find_handler("https://m.example/x").module_id == "moved"


def test_changed_file_is_detected_and_never_silently_executed(tmp_path, registry):
    marker = tmp_path / "evil.txt"
    registry.add(make_module(tmp_path, "edited", "e.example"), trust_confirmed=True)
    assert registry.find_handler("https://e.example/x")  # loaded once with the original hash
    managed = registry.directory / "edited.py"
    managed.write_text(f"open({str(marker)!r}, 'w').write('pwned')\n" + managed.read_text("utf-8"), "utf-8")
    info = registry.get("edited")
    assert info.load_status == LoadStatus.CHANGED and not info.dispatchable
    assert registry.find_handler("https://e.example/x") is None and not marker.exists()
    fresh = ModuleRegistry(registry.directory)  # also after a restart
    assert fresh.get("edited").load_status == LoadStatus.CHANGED and fresh.find_handler("https://e.example/x") is None and not marker.exists()


def test_missing_file_is_reported(tmp_path, registry):
    registry.add(make_module(tmp_path, "vanish", "v.example"), trust_confirmed=True)
    (registry.directory / "vanish.py").unlink()
    assert registry.get("vanish").load_status == LoadStatus.MISSING and registry.find_handler("https://v.example/x") is None


@pytest.mark.parametrize("name,source,fragment", [
    ("syntaxerr", "def can_handle(:\n", "not valid Python"),
    ("nocan", "def download(worker):\n    pass\n", "can_handle()"),
    ("nodl", "def can_handle(url):\n    return True\n", "download()"),
])
def test_static_inspection_rejects_broken_modules_before_anything_runs(tmp_path, registry, name, source, fragment):
    path = make_module(tmp_path, name, source=source)
    assert not inspect_module_source(path).ok
    with pytest.raises(ModuleError, match=fragment.replace("(", r"\(").replace(")", r"\)")):
        registry.add(path, trust_confirmed=True)
    assert registry.list() == []


def test_exception_at_import_marks_error_and_others_keep_working(tmp_path, registry):
    registry.add(make_module(tmp_path, "boom", source="raise RuntimeError('bad import')\ndef can_handle(u):\n    return True\ndef download(w):\n    pass\n"), trust_confirmed=True)
    registry.add(make_module(tmp_path, "fine", "f.example"), trust_confirmed=True)
    assert registry.find_handler("https://f.example/x").module_id == "fine"
    info = registry.get("boom")
    assert info.load_status == LoadStatus.ERROR and "bad import" in info.last_error and not info.dispatchable


def test_system_exit_at_import_does_not_kill_the_app(tmp_path, registry):
    registry.add(make_module(tmp_path, "quits", source="import sys\nsys.exit(3)\ndef can_handle(u):\n    return True\ndef download(w):\n    pass\n"), trust_confirmed=True)
    assert registry.find_handler("https://x.example/") is None
    assert registry.get("quits").load_status == LoadStatus.ERROR


def test_can_handle_that_raises_isolates_that_module_only(tmp_path, registry):
    registry.add(make_module(tmp_path, "raiser", source="def can_handle(u):\n    raise ValueError('nope')\ndef download(w):\n    pass\n"), trust_confirmed=True)
    registry.add(make_module(tmp_path, "ok", "ok.example"), trust_confirmed=True)
    assert registry.find_handler("https://ok.example/x").module_id == "ok"
    assert registry.get("raiser").load_status == LoadStatus.ERROR and "nope" in registry.get("raiser").last_error


def test_download_that_raises_is_a_resolution_error_not_a_crash(tmp_path, registry):
    from rychlik.modules.legacy_adapter import ResolutionError

    registry.add(make_module(tmp_path, "dlraise", source='def can_handle(u):\n    return True\ndef download(w):\n    raise RuntimeError("site changed")\n'), trust_confirmed=True)
    with pytest.raises(ResolutionError, match="site changed"):
        registry.resolve("https://x.example/v", tmp_path)
    assert registry.get("dlraise").dispatchable  # a failed resolve is not a broken module


def test_two_matching_modules_are_ambiguous_never_first_by_filesystem_order(tmp_path, registry):
    registry.add(make_module(tmp_path, "aaa", "same.example"), trust_confirmed=True)
    registry.add(make_module(tmp_path, "bbb", "same.example"), trust_confirmed=True)
    with pytest.raises(AmbiguousModuleMatch) as caught:
        registry.find_handler("https://same.example/x")
    assert sorted(caught.value.module_ids) == ["aaa", "bbb"]
    registry.set_enabled("bbb", False)  # the user resolves it explicitly
    assert registry.find_handler("https://same.example/x").module_id == "aaa"


def test_no_match_falls_back_to_normal_download(tmp_path, registry):
    registry.add(make_module(tmp_path, "one", "one.example"), trust_confirmed=True)
    assert registry.find_handler("https://other.example/x") is None
    assert registry.resolve("https://other.example/x", tmp_path) is None


def test_resolve_goes_through_the_adapter_to_a_typed_request(tmp_path, registry):
    registry.add(make_module(tmp_path, "res", "r.example"), trust_confirmed=True)
    result = registry.resolve("https://r.example/page", tmp_path / "dl")
    assert result.request.url == "https://cdn.r.example/v.mp4" and result.request.media is not None


def test_process_bridge_module_is_registered_but_never_dispatched(tmp_path, registry):
    src = "def can_handle(u):\n    return True\ndef download(w):\n    w._set_process(None)\n"
    info = registry.add(make_module(tmp_path, "proc", source=src), trust_confirmed=True)
    assert info.enabled and info.compatibility == COMPAT_NEEDS_PROCESS_BRIDGE and not info.dispatchable
    assert registry.find_handler("https://x.example/") is None


def test_duplicate_and_non_python_files_are_refused(tmp_path, registry):
    path = make_module(tmp_path, "dup", "d.example")
    registry.add(path, trust_confirmed=True)
    with pytest.raises(ModuleError, match="already added"):
        registry.add(path, trust_confirmed=True)
    txt = tmp_path / "x.txt"
    txt.write_text("hi")
    with pytest.raises(ModuleError):
        registry.add(txt, trust_confirmed=True)


def test_unreadable_registry_file_is_an_error_not_a_silent_reset(tmp_path, registry):
    registry.add(make_module(tmp_path, "keep", "k.example"), trust_confirmed=True)
    (registry.directory / "registry.json").write_text("{not json", "utf-8")
    with pytest.raises(ModuleError, match="unreadable"):
        registry.list()


@pytest.mark.skipif(not LEGACY_DIR.is_dir(), reason="legacy modules folder not present")
def test_importing_the_historical_folder_registers_only_the_audited_allowlist(tmp_path, registry):
    report = import_legacy_directory(LEGACY_DIR, registry, trust_confirmed=True)
    ids = {i.module_id for i in report.added}
    assert ids == set(ALLOWED_LEGACY_MODULES) and len(ids) == 11
    for excluded in ("faphouse_paywall.py", "faphouse_cs_5min_check.py", "download_cs_faphouse_full_video.py", "investigate_ashemeta.py"):
        assert excluded in report.skipped and not (registry.directory / excluded).exists()
    by_id = {i.module_id: i for i in registry.list()}
    assert by_id["shez_tube"].compatibility == COMPAT_NEEDS_PROCESS_BRIDGE and not by_id["shez_tube"].dispatchable
    ten = [i for i in by_id.values() if i.module_id != "shez_tube"]
    assert len(ten) == 10 and all(i.compatibility == COMPAT_RESOLVER and i.dispatchable for i in ten)
    # the 10 real modules import (first dispatch) without error; shez_tube is never imported
    for i in ten:
        registry.find_handler("https://nothing.example/x")
    assert all(registry.get(i.module_id).load_status == LoadStatus.LOADED for i in ten)
    assert registry.get("shez_tube").load_status == LoadStatus.NOT_LOADED


def test_import_requires_trust_confirmation(tmp_path, registry):
    if not LEGACY_DIR.is_dir():
        pytest.skip("legacy modules folder not present")
    report = import_legacy_directory(LEGACY_DIR, registry, trust_confirmed=False)
    assert report.added == [] and registry.list() == []
