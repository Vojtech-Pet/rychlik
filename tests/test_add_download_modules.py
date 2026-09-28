"""M9: Add download -> ModuleRegistry resolution, through the production DownloadManagerWidget and AddDownloadDialog."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from rychlik.acquisition.contracts import DownloadRequest
from rychlik.core.download_manager_service import DownloadManagerConfig, DownloadManagerService
from rychlik.gui.dialogs import AddDownloadDialog
from rychlik.gui.download_manager_widget import DownloadManagerWidget
from rychlik.modules.catalog import bundled_module_path
from rychlik.modules.registry import ModuleRegistry
from rychlik.modules.resolve_service import ModuleResolveService
from test_download_manager_widget import _FakeManager

SITE_MODULE = """
import threading
GATE = None
def can_handle(url):
    return url.startswith("https://site.example/")
def download(worker):
    if GATE is not None:
        GATE.wait(10)
    worker.item.url = "%(target)s"
    worker.item.display_name = "resolved.mp4"
    worker.item.referrer = "https://site.example/"
    worker._run_media()
"""


def pump(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return pred()


def write(tmp_path, name, source):
    path = tmp_path / "src" / f"{name}.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, "utf-8")
    return path


class Rig:
    def __init__(self, qapp, tmp_path, manager=None):
        self.tmp = tmp_path
        self.registry = ModuleRegistry(tmp_path / "modules")
        self.manager = manager or _FakeManager(items=[])
        self.widget = DownloadManagerWidget(self.manager, resolve_service=ModuleResolveService(self.registry))
        self.widget._destination_dir = tmp_path / "dl"
        (tmp_path / "dl").mkdir(exist_ok=True)

    def add_module(self, name, source, enabled=True):
        return self.registry.add(write(self.tmp, name, source), trust_confirmed=True, enabled=enabled)

    def dialog(self, url):
        dialog = AddDownloadDialog(self.widget)
        dialog.url_input.setText(url)
        return dialog

    def enqueued(self):
        return [c[1] for c in self.manager.calls if c[0] == "add_download"]


@pytest.fixture
def rig(qapp, tmp_path):
    r = Rig(qapp, tmp_path)
    yield r
    r.widget.shutdown()


def test_ordinary_url_takes_the_unchanged_original_path(rig):
    rig.add_module("site", SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    dialog = rig.dialog("https://example.test/file.mp4")
    dialog.download_button.click()
    assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted)
    assert rig.enqueued() == [DownloadRequest(url="https://example.test/file.mp4", destination_dir=rig.tmp / "dl")]  # no media options, no module


def test_a_module_url_enqueues_exactly_one_typed_request_and_shows_resolving(rig):
    import sys

    rig.add_module("site", SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    module = rig.registry.find_handler("https://site.example/x").module
    module.GATE = gate = threading.Event()
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    assert dialog.status_label.text() == "Resolving URL…" and not dialog.url_input.isEnabled()  # busy, GUI thread free
    assert rig.enqueued() == []
    gate.set()
    assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted)
    [request] = rig.enqueued()
    assert request.url == "https://cdn.example/v.mp4" and request.filename_hint == "resolved.mp4" and request.media.referer == "https://site.example/"


def test_the_gui_thread_is_not_blocked_while_a_module_resolves(rig):
    rig.add_module("site", SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    rig.registry.find_handler("https://site.example/x").module.GATE = gate = threading.Event()
    dialog = rig.dialog("https://site.example/page")
    start = time.monotonic()
    dialog.download_button.click()
    assert time.monotonic() - start < 1.0  # click returns immediately although the module is stuck
    ticks = []
    from PySide6.QtCore import QTimer

    QTimer.singleShot(50, lambda: ticks.append(1))
    assert pump(lambda: ticks, timeout=2)  # the event loop keeps running
    gate.set()
    pump(lambda: dialog.result() == dialog.DialogCode.Accepted)


def test_disabled_and_changed_modules_are_ignored_and_never_executed(rig, tmp_path):
    marker = tmp_path / "ran.txt"
    source = f"open({str(marker)!r}, 'a').write('x')\n" + SITE_MODULE % {"target": "https://cdn.example/v.mp4"}
    rig.add_module("site", source, enabled=False)
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted) and rig.enqueued()[0].media is None and not marker.exists()  # disabled -> plain path

    rig.registry.set_enabled("site", True)
    managed = rig.registry.directory / "site.py"
    managed.write_text(managed.read_text("utf-8") + "\n# edited\n", "utf-8")
    dialog2 = rig.dialog("https://site.example/again")
    dialog2.download_button.click()
    assert pump(lambda: dialog2.result() == dialog2.DialogCode.Accepted) and rig.enqueued()[1].media is None and not marker.exists()  # changed -> plain path


def test_a_matched_module_that_fails_is_an_explicit_error_with_no_fallback(rig):
    rig.add_module("site", 'def can_handle(u):\n    return u.startswith("https://site.example/")\ndef download(w):\n    raise RuntimeError("site changed")\n')
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    assert pump(lambda: "site changed" in dialog.status_label.text())
    assert "Could not resolve this URL" in dialog.status_label.text() and "site:" in dialog.status_label.text()
    assert "Traceback" not in dialog.status_label.text()
    assert dialog.result() != dialog.DialogCode.Accepted and rig.enqueued() == []  # NOT silently downloaded as a plain file
    assert dialog.url_input.isEnabled() and dialog.download_button.isEnabled()  # the user can fix and retry


def test_ambiguous_modules_are_an_explicit_error_and_enqueue_nothing(rig):
    for name in ("first", "second"):
        rig.add_module(name, SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    assert pump(lambda: "More than one enabled module" in dialog.status_label.text())
    assert "Disable one of the conflicting modules" in dialog.status_label.text() and rig.enqueued() == []
    rig.registry.set_enabled("second", False)  # the user resolves it
    dialog.download_button.click()
    assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted) and len(rig.enqueued()) == 1


def test_cancel_during_resolve_enqueues_nothing_even_when_the_module_later_finishes(rig):
    rig.add_module("site", SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    rig.registry.find_handler("https://site.example/x").module.GATE = gate = threading.Event()
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    dialog.cancel_button.click()
    gate.set()
    time.sleep(0.3)
    pump(lambda: False, timeout=0.3)
    assert rig.enqueued() == [] and dialog.result() != dialog.DialogCode.Accepted


def test_closing_the_window_during_resolve_leaves_no_stale_callback_and_no_crash(qapp, tmp_path):
    rig = Rig(qapp, tmp_path)
    rig.add_module("site", SITE_MODULE % {"target": "https://cdn.example/v.mp4"})
    rig.registry.find_handler("https://site.example/x").module.GATE = gate = threading.Event()
    dialog = rig.dialog("https://site.example/page")
    dialog.download_button.click()
    rig.widget.shutdown()
    rig.widget.deleteLater()
    dialog.deleteLater()
    gate.set()
    time.sleep(0.3)
    pump(lambda: False, timeout=0.3)
    assert rig.enqueued() == []


def test_bundled_media_sites_module_from_the_real_dialog(qapp, tmp_path):
    rig = Rig(qapp, tmp_path)
    try:
        rig.registry.add(bundled_module_path("media_sites.py"), trust_confirmed=True)
        url = "https://www.xvideos.com/video.abc123/some-title"
        dialog = rig.dialog(url)
        dialog.download_button.click()
        assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted)
        [request] = rig.enqueued()
        assert request.url == url and request.media is not None and request.destination_dir == tmp_path / "dl"
        rig.registry.set_enabled("media_sites", False)  # same URL, module disabled -> ordinary path
        dialog2 = rig.dialog(url)
        dialog2.download_button.click()
        assert pump(lambda: dialog2.result() == dialog2.DialogCode.Accepted) and rig.enqueued()[1].media is None
        rig.registry.set_enabled("media_sites", True)  # and enabled again -> module again
        dialog3 = rig.dialog(url)
        dialog3.download_button.click()
        assert pump(lambda: dialog3.result() == dialog3.DialogCode.Accepted) and rig.enqueued()[2].media is not None
    finally:
        rig.widget.shutdown()


def test_real_queue_e2e_through_the_widget_and_a_removed_module_does_not_affect_the_running_download(qapp, http_fixture_server, tmp_path):
    from tests.http_fixture_server import NORMAL_BODY
    from rychlik.gui.completed_artifact_bridge import build_artifact_for_completed

    manager = DownloadManagerService(config=DownloadManagerConfig(database_path=tmp_path / "s.db", max_active_transfers=1))
    manager.start()
    rig = Rig(qapp, tmp_path, manager=manager)
    try:
        rig.add_module("site", SITE_MODULE % {"target": f"{http_fixture_server.base_url}/slow"})
        dialog = rig.dialog("https://site.example/page")
        dialog.download_button.click()
        assert pump(lambda: dialog.result() == dialog.DialogCode.Accepted)
        assert pump(lambda: len(manager.snapshot().items) == 1)  # exactly one queue entry
        entry = manager.snapshot().items[0].queue_entry_id
        assert pump(lambda: any(i.bytes_downloaded for i in manager.snapshot().items))
        rig.registry.remove("site")  # the module is gone while its download is running
        assert pump(lambda: not manager.snapshot().items, timeout=30)
        artifact, _ = build_artifact_for_completed(manager, entry)
        assert artifact is not None and artifact.filename == "resolved.mp4" and artifact.local_path.read_bytes() == NORMAL_BODY
    finally:
        rig.widget.shutdown()
        manager.stop()


def test_a_late_result_for_a_cancelled_job_or_a_closing_window_is_dropped_by_the_widget_itself(rig):
    from rychlik.modules.resolve_service import ResolveJob, ResolveKind, ResolveOutcome

    request = DownloadRequest(url="https://cdn.example/v.mp4", destination_dir=rig.tmp / "dl")
    outcome = ResolveOutcome(ResolveKind.RESOLVED, request=request, module_id="site", module_name="Site")
    called = []
    cancelled = ResolveJob()
    cancelled.cancel()
    rig.widget._on_resolve_finished(cancelled, outcome, ("https://site.example/x", rig.tmp / "dl", lambda ok, msg: called.append(ok)))
    rig.widget._shutting_down = True
    rig.widget._on_resolve_finished(ResolveJob(), outcome, ("https://site.example/x", rig.tmp / "dl", lambda ok, msg: called.append(ok)))
    assert rig.enqueued() == [] and called == []
