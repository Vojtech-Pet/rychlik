"""The loopback bridge: real HTTP against the real server, including the requests a hostile web page could make."""

from __future__ import annotations

import http.client
import json
import os
import stat

import pytest

from rychlik.bridge.browser_bridge import MAX_BODY_BYTES, BrowserBridge, BrowserDownload, TokenStore


@pytest.fixture
def rig(tmp_path):
    received: list[BrowserDownload] = []
    tokens = TokenStore(tmp_path / "bridge_token")
    bridge = BrowserBridge(tokens, received.append, lambda url, browser, referrer: [{"label": "720p", "format": "best[height<=720]"}], port=0, max_requests_per_minute=100)
    assert bridge.start()
    yield bridge, tokens, received
    bridge.stop()


def post(bridge, path, body=None, *, token=None, host=None, raw=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    data = raw if raw is not None else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{bridge.port}"}
    if token is not None:
        hdrs["X-Rychlik-Token"] = token
    hdrs.update(headers or {})
    conn.request("POST", path, body=data, headers=hdrs)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, (json.loads(payload) if payload else {}), response


VALID = {"url": "https://example.com/watch?v=1", "media": True, "browser": "firefox", "referrer": "https://example.com/", "format": "best"}


def test_authorised_request_asks_the_app_to_open_a_prefilled_dialog(rig):
    bridge, tokens, received = rig
    status, body, _ = post(bridge, "/download", VALID, token=tokens.get())
    assert (status, body) == (202, {"ok": True})
    assert received == [BrowserDownload("https://example.com/watch?v=1", True, "firefox", "https://example.com/", "best")]


def test_a_web_page_cannot_use_it_without_the_token(rig):
    bridge, tokens, received = rig
    for token in (None, "", "wrong", tokens.get()[:-1], tokens.get() + "x"):
        assert post(bridge, "/download", VALID, token=token)[0] == 401
    assert received == []


def test_dns_rebinding_host_is_refused_even_with_the_right_token(rig):
    bridge, tokens, received = rig
    for host in ("evil.example", f"evil.example:{bridge.port}", "127.0.0.1", "127.0.0.1:1", f"localhost.evil.com:{bridge.port}"):
        assert post(bridge, "/download", VALID, token=tokens.get(), host=host)[0] == 403
    assert received == []
    assert post(bridge, "/download", VALID, token=tokens.get(), host=f"localhost:{bridge.port}")[0] == 202


def test_bad_bodies_are_rejected_without_calling_the_app(rig):
    bridge, tokens, received = rig
    t = tokens.get()
    for body in ({"url": "file:///etc/passwd"}, {"url": "javascript:alert(1)"}, {"url": "ftp://x/y"}, {"url": ""}, {"url": "http://"}, {"nourl": 1}, ["list"], "str"):
        assert post(bridge, "/download", body, token=t)[0] == 400, body
    assert post(bridge, "/download", raw=b"{not json", token=t)[0] == 400
    assert post(bridge, "/download", raw=b"x" * (MAX_BODY_BYTES + 1), token=t)[0] == 413
    assert post(bridge, "/nope", VALID, token=t)[0] == 404
    assert received == []


def test_rate_limit_stops_a_flood_of_dialogs(tmp_path):
    received: list[BrowserDownload] = []
    tokens = TokenStore(tmp_path / "tok")
    bridge = BrowserBridge(tokens, received.append, port=0, max_requests_per_minute=8)
    assert bridge.start()
    try:
        statuses = [post(bridge, "/download", VALID, token=tokens.get())[0] for _ in range(12)]
    finally:
        bridge.stop()
    assert statuses[:8] == [202] * 8 and set(statuses[8:]) == {429}
    assert len(received) == 8


def test_formats_endpoint_needs_the_token_and_returns_the_list(rig):
    bridge, tokens, _ = rig
    assert post(bridge, "/formats", VALID, token="nope")[0] == 401
    status, body, _ = post(bridge, "/formats", VALID, token=tokens.get())
    assert status == 200 and body["formats"] == [{"label": "720p", "format": "best[height<=720]"}]


def test_cors_only_for_extension_origins_and_get_is_refused(rig):
    bridge, tokens, _ = rig
    _, _, resp = post(bridge, "/download", VALID, token=tokens.get(), headers={"Origin": "moz-extension://abc"})
    assert resp.getheader("Access-Control-Allow-Origin") == "moz-extension://abc"
    _, _, resp = post(bridge, "/download", VALID, token=tokens.get(), headers={"Origin": "https://evil.example"})
    assert resp.getheader("Access-Control-Allow-Origin") is None
    conn = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    conn.request("OPTIONS", "/download", headers={"Origin": "https://evil.example", "Host": f"127.0.0.1:{bridge.port}"})
    pre = conn.getresponse()
    assert pre.status == 204 and pre.getheader("Access-Control-Allow-Origin") is None
    conn.request("GET", "/download", headers={"Host": f"127.0.0.1:{bridge.port}"})
    assert conn.getresponse().status == 405


def test_it_listens_on_loopback_only(rig):
    bridge, _, _ = rig
    assert bridge._server.server_address[0] == "127.0.0.1"


def test_token_is_random_private_persistent_and_regenerable(tmp_path):
    store = TokenStore(tmp_path / "t" / "bridge_token")
    first = store.get()
    assert len(first) >= 32 and store.get() == first and TokenStore(tmp_path / "t" / "bridge_token").get() == first
    assert stat.S_IMODE(os.stat(tmp_path / "t" / "bridge_token").st_mode) == 0o600
    second = store.regenerate()
    assert second != first and store.get() == second


def test_old_token_stops_working_after_regeneration(rig):
    bridge, tokens, received = rig
    old = tokens.get()
    tokens.regenerate()
    assert post(bridge, "/download", VALID, token=old)[0] == 401 and received == []


def test_port_in_use_is_reported_not_fatal(rig, tmp_path):
    bridge, tokens, _ = rig
    second = BrowserBridge(tokens, lambda d: None, port=bridge.port)
    assert second.start() is False
    second.stop()


# --- from the bridge to the Add download dialog and the queue -------------------------------------------------------------


def test_media_options_for_maps_video_pages_and_leaves_plain_links_alone():
    from rychlik.bridge.browser_bridge import media_options_for

    assert media_options_for(BrowserDownload("https://a.example/f.zip", False, "firefox", "https://a.example/", "")) is None
    m = media_options_for(BrowserDownload("https://youtu.be/x", True, "firefox", "https://youtu.be/x", "best[height<=720]"))
    assert (m.video_format, m.referer) == ("best[height<=720]", "https://youtu.be/x")
    m = media_options_for(BrowserDownload("https://youtu.be/x", True, "firefox", "javascript:1", ""))
    assert (m.video_format, m.referer) == ("bestvideo+bestaudio/best", None)  # default format; a non-http referrer is dropped


def test_browser_request_reaches_a_prefilled_dialog_and_the_queue_with_media_options(qapp, tmp_path):
    from test_download_manager_widget import _FakeManager

    from rychlik.acquisition.contracts import DownloadRequest
    from rychlik.bridge.browser_bridge import media_options_for
    from rychlik.gui.dialogs import AddDownloadDialog
    from rychlik.gui.download_manager_widget import DownloadManagerWidget

    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    widget._destination_dir = tmp_path
    download = BrowserDownload("https://www.youtube.com/watch?v=abc", True, "firefox", "https://www.youtube.com/watch?v=abc", "best")
    dialog = AddDownloadDialog(widget, url=download.url, media=media_options_for(download))
    assert dialog.url_input.text() == download.url and dialog.download_button.isEnabled()
    assert manager.calls == []  # nothing is downloaded until the user confirms
    dialog.download_button.click()
    [request] = [c[1] for c in manager.calls if c[0] == "add_download"]
    assert request == DownloadRequest(url=download.url, destination_dir=tmp_path, media=media_options_for(download))
    widget.shutdown()


def test_plain_link_from_the_browser_stays_a_plain_download(qapp, tmp_path):
    from test_download_manager_widget import _FakeManager

    from rychlik.acquisition.contracts import DownloadRequest
    from rychlik.gui.dialogs import AddDownloadDialog
    from rychlik.gui.download_manager_widget import DownloadManagerWidget

    manager = _FakeManager(items=[])
    widget = DownloadManagerWidget(manager)
    widget._destination_dir = tmp_path
    dialog = AddDownloadDialog(widget, url="https://example.com/file.zip", media=None)
    dialog.download_button.click()
    assert [c[1] for c in manager.calls if c[0] == "add_download"] == [DownloadRequest(url="https://example.com/file.zip", destination_dir=tmp_path)]
    widget.shutdown()


def test_settings_shows_the_token_and_status_only_when_a_bridge_is_configured(qapp, tmp_path):
    from rychlik.gui.dialogs import SettingsDialog

    assert SettingsDialog(None).token_box is None
    tokens = TokenStore(tmp_path / "tok")
    bridge = BrowserBridge(tokens, lambda d: None, port=0)
    assert bridge.start()
    try:
        dialog = SettingsDialog(None, browser_bridge=bridge, bridge_tokens=tokens)
        assert dialog.token_box.text() == tokens.get() and f"127.0.0.1:{bridge.port}" in dialog.bridge_status.text()
        assert dialog.token_box.echoMode().name == "Password"  # not shown in clear text on screen
    finally:
        bridge.stop()
    dialog = SettingsDialog(None, browser_bridge=bridge, bridge_tokens=tokens)
    assert "in use" in dialog.bridge_status.text()  # stopped / port taken is reported honestly
