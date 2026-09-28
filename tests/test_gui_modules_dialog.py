"""M8: Settings › Modules, driven through the real dialog over the real ModuleRegistry (no mocks of the registry)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rychlik.gui.dialogs import SettingsDialog
from rychlik.gui.dialogs.modules import TRUST_WARNING, ModulesDialog, display_status
from rychlik.modules.catalog import bundled_module_path
from rychlik.modules.registry import LoadStatus, ModuleRegistry

BUNDLED = bundled_module_path("media_sites.py")
URL = "https://www.xvideos.com/video.abc123/some-title"


class Script:
    """Stands in for the native file chooser / message boxes and records what the user was shown."""

    def __init__(self):
        self.path = ""
        self.confirm_answer = True
        self.confirmations: list[tuple[str, str, str]] = []
        self.notices: list[tuple[str, str]] = []

    def chooser(self, parent):
        return self.path

    def confirm(self, parent, title, text, action):
        self.confirmations.append((title, text, action))
        return self.confirm_answer

    def notice(self, parent, title, text):
        self.notices.append((title, text))


@pytest.fixture
def rig(qapp, tmp_path):
    registry = ModuleRegistry(tmp_path / "modules")
    script = Script()
    dialog = ModulesDialog(registry, file_chooser=script.chooser, confirm=script.confirm, notice=script.notice)
    yield registry, script, dialog, tmp_path
    dialog.close()


def select(dialog, module_id):
    for i in range(dialog.list.count()):
        if dialog.list.item(i).data(0x0100) == module_id:
            dialog.list.setCurrentRow(i)
            return
    raise AssertionError(module_id)


def add_bundled(rig):
    registry, script, dialog, _ = rig
    script.path = str(BUNDLED)
    dialog.add_button.click()
    return registry.get("media_sites")


def test_empty_state_says_so(rig):
    _, _, dialog, _ = rig
    assert dialog.empty.isVisibleTo(dialog) and dialog.list.count() == 0
    assert not any(b.isEnabled() for b in (dialog.enable_button, dialog.disable_button, dialog.remove_button))


def test_settings_offers_modules_only_when_a_registry_exists(qapp, tmp_path):
    assert SettingsDialog(None).modules_button is None
    assert SettingsDialog(None, module_registry=ModuleRegistry(tmp_path / "m")).modules_button is not None


def test_full_lifecycle_add_disable_enable_remove_with_real_dispatch(rig):
    registry, script, dialog, tmp_path = rig
    assert registry.find_handler(URL) is None

    info = add_bundled(rig)
    assert info is not None and info.enabled
    assert script.confirmations and script.confirmations[0][1].startswith(TRUST_WARNING.split("\n")[0]) and "Install only modules you trust" in script.confirmations[0][1]
    assert "sandbox" in script.confirmations[0][1].lower() and "not sandbox" in script.confirmations[0][1].lower()
    assert dialog.list.count() == 1 and dialog.list.item(0).text() == "Media Sites    ·    Enabled"
    select(dialog, "media_sites")
    assert "Handles: XVideos, XNXX, EPorner, TGTube, ShemaleZ" in dialog.detail.text()
    resolved = registry.resolve(URL, tmp_path / "dl")  # registry dispatch -> adapter -> typed request
    assert resolved.request.url == URL and resolved.request.media is not None

    dialog.disable_button.click()
    assert dialog.list.item(0).text().endswith("Disabled") and registry.resolve(URL, tmp_path / "dl") is None  # no restart needed

    select(dialog, "media_sites")
    dialog.enable_button.click()
    assert dialog.list.item(0).text().endswith("Enabled") and registry.resolve(URL, tmp_path / "dl") is not None

    select(dialog, "media_sites")
    managed = registry.directory / "media_sites.py"
    assert managed.exists()
    dialog.remove_button.click()
    assert dialog.list.count() == 0 and not managed.exists() and registry.resolve(URL, tmp_path / "dl") is None
    assert resolved.request.url == URL  # a request that was already produced is untouched by the removal


def test_declining_the_trust_warning_adds_nothing(rig):
    registry, script, dialog, _ = rig
    script.confirm_answer = False
    script.path = str(BUNDLED)
    dialog.add_button.click()
    assert registry.list() == [] and dialog.list.count() == 0 and script.confirmations


def test_cancelling_the_file_chooser_does_nothing(rig):
    registry, script, dialog, _ = rig
    script.path = ""
    dialog.add_button.click()
    assert registry.list() == [] and not script.confirmations and not script.notices


def test_an_invalid_file_is_explained_and_never_reaches_the_trust_prompt(rig, tmp_path):
    registry, script, dialog, _ = rig
    bad = tmp_path / "bad.py"
    bad.write_text("def can_handle(:\n")
    script.path = str(bad)
    dialog.add_button.click()
    assert registry.list() == [] and not script.confirmations and "not valid Python" in script.notices[0][1]
    noapi = tmp_path / "noapi.py"
    noapi.write_text("x = 1\n")
    script.path = str(noapi)
    dialog.add_button.click()
    assert registry.list() == [] and "can_handle()" in script.notices[1][1]


def test_changed_module_is_shown_as_changed_and_cannot_be_enabled_or_dispatched(rig):
    registry, script, dialog, tmp_path = rig
    add_bundled(rig)
    managed = registry.directory / "media_sites.py"
    managed.write_text(managed.read_text("utf-8") + "\n# tampered\n", "utf-8")
    dialog.refresh()
    select(dialog, "media_sites")
    assert dialog.list.item(0).text() == "Media Sites    ·    Changed" and "Handles:" not in dialog.detail.text()  # no claims about an edited file
    assert "will not be loaded" in dialog.detail.text() and "Run anyway" not in dialog.detail.text()
    assert not dialog.enable_button.isEnabled() and not any("run anyway" in b.text().lower() for b in dialog.findChildren(type(dialog.add_button)))
    assert registry.resolve(URL, tmp_path / "dl") is None
    dialog.remove_button.click()  # the only way out is remove and add again
    assert registry.list() == []


def test_error_module_shows_a_short_reason_and_the_app_survives(rig, tmp_path):
    registry, script, dialog, _ = rig
    broken = tmp_path / "boom.py"
    broken.write_text("raise RuntimeError('bad import')\ndef can_handle(u):\n    return True\ndef download(w):\n    pass\n")
    script.path = str(broken)
    dialog.add_button.click()
    registry.find_handler("https://x.example/")  # first dispatch imports it and fails
    dialog.refresh()
    select(dialog, "boom")
    assert dialog.list.item(0).text() == "boom    ·    Error" and "bad import" in dialog.detail.text()


def test_process_bridge_module_is_added_but_labelled_unsupported(rig, tmp_path):
    registry, script, dialog, _ = rig
    proc = tmp_path / "shez.py"
    proc.write_text("def can_handle(u):\n    return True\ndef download(w):\n    w._set_process(None)\n")
    script.path = str(proc)
    dialog.add_button.click()
    assert "not used until Rýchlik supports that" in script.confirmations[0][1]
    select(dialog, "shez")
    assert dialog.list.item(0).text() == "shez    ·    Unsupported" and "process bridge" in dialog.detail.text()
    assert registry.find_handler("https://x.example/") is None


def test_cancelling_remove_keeps_the_module(rig):
    registry, script, dialog, _ = rig
    add_bundled(rig)
    select(dialog, "media_sites")
    script.confirm_answer = False
    dialog.remove_button.click()
    assert registry.get("media_sites") is not None and (registry.directory / "media_sites.py").exists()
    assert "already added keep working" in script.confirmations[-1][1]


def test_dialog_reflects_a_registry_persisted_by_an_earlier_session(qapp, tmp_path):
    first = ModuleRegistry(tmp_path / "modules")
    first.add(BUNDLED, trust_confirmed=True)
    dialog = ModulesDialog(ModuleRegistry(tmp_path / "modules"))
    assert dialog.list.count() == 1 and dialog.list.item(0).text() == "Media Sites    ·    Enabled"
    assert display_status(dialog._registry.get("media_sites")) == "Enabled" and dialog._registry.get("media_sites").load_status == LoadStatus.NOT_LOADED
