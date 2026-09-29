"""Visual regression guard: the screenshot harness renders every approved screen (both themes) and none is blank."""

from PySide6.QtGui import QImage

from gui_screenshots import render_all


def test_every_approved_screen_renders_non_blank_in_both_themes(qapp, tmp_path):
    files = render_all(tmp_path / "out", tmp_path / "work", quick=True)
    names = {f.stem for f in files}
    for required in ("main_1366x768", "main_multiselect", "context_menu_transferring", "details_active", "share_selector", "send_transcoding", "send_identity_changed", "devices_page", "queues_view", "pair_device"):
        assert required in names, required
    assert {f.parent.name for f in files} == {"dark", "light"}
    for f in files:
        image = QImage(str(f))
        assert not image.isNull() and image.width() > 50 and image.height() > 20, f
        colors = {image.pixel(x, y) for x in range(0, image.width(), max(1, image.width() // 40)) for y in range(0, image.height(), max(1, image.height() // 40))}
        assert len(colors) > 3, f"{f.name} looks blank"
