"""The browser extension's bridge client, run by Node against the real Python bridge server."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rychlik.bridge.browser_bridge import BrowserBridge, BrowserDownload, TokenStore

EXT = Path(__file__).parent.parent / "browser-extension"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")

DRIVER = r"""
const {rychlikBridgeFetch} = require(process.argv[2] + "/bridge-client.js");
const port = Number(process.argv[3]);
const token = process.argv[4];
globalThis.fetch = ((orig) => (url, init) => orig(String(url).replace(/:17654/, ":" + port), init))(globalThis.fetch);
const store = {};
const api = {storage: {local: {get: async k => ({[k]: store[k]}), set: async o => Object.assign(store, o)}}};
(async () => {
  const out = {};
  for (const [name, t] of [["missing", ""], ["wrong", "nope"], ["right", token]]) {
    store.rychlikToken = t;
    try { out[name] = await rychlikBridgeFetch(api, "/download", {url: "https://example.com/v", media: true, browser: "firefox", referrer: "https://example.com/", format: "best"}); }
    catch (e) { out[name] = {error: e.message, code: e.code}; }
  }
  console.log(JSON.stringify(out));
})();
"""


def test_node_client_sends_the_token_and_reports_missing_or_wrong_tokens(tmp_path):
    received: list[BrowserDownload] = []
    tokens = TokenStore(tmp_path / "tok")
    bridge = BrowserBridge(tokens, received.append, port=0)
    assert bridge.start()
    try:
        driver = tmp_path / "driver.js"
        driver.write_text(DRIVER)
        out = subprocess.run([node, str(driver), str(EXT), str(bridge.port), tokens.get()], capture_output=True, text=True, timeout=30)
    finally:
        bridge.stop()
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["missing"]["code"] == "no-token"
    assert result["wrong"]["code"] == "bad-token"
    assert result["right"] == {"ok": True}
    assert received == [BrowserDownload("https://example.com/v", True, "firefox", "https://example.com/", "best")]


def test_web_page_side_script_never_talks_to_the_app_or_holds_the_token():
    content = (EXT / "content.js").read_text("utf-8")
    assert "17654" not in content and "fetch(" not in content and "rychlikToken" not in content
    assert 'runtime.sendMessage({type: "bridge"' in content  # goes through the background script instead


def test_only_the_background_script_and_client_know_the_port_and_token():
    for name in ("background.js", "popup.js"):
        text = (EXT / name).read_text("utf-8")
        assert "17654" not in text, name
    manifest = json.loads((EXT / "manifest.json").read_text("utf-8"))
    assert manifest["background"]["scripts"] == ["bridge-client.js", "background.js"]


def test_all_extension_scripts_parse():
    for name in ("background.js", "content.js", "bridge-client.js", "popup.js"):
        assert subprocess.run([node, "--check", str(EXT / name)], capture_output=True).returncode == 0, name


BG_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const dir = process.argv[2], port = Number(process.argv[3]), token = process.argv[4];
const store = {rychlikToken: token};
const listeners = [];
const notes = [];
const api = {
  storage: {local: {get: async k => ({[k]: store[k]}), set: async o => Object.assign(store, o)}},
  webRequest: {onHeadersReceived: {addListener() {}}},
  tabs: {onRemoved: {addListener() {}}, sendMessage: async () => {}, query: async () => []},
  contextMenus: {removeAll() {}, create() {}, update() {}, refresh() {}, onShown: {addListener() {}}, onClicked: {addListener() {}}},
  runtime: {onInstalled: {addListener() {}}, onMessage: {addListener: f => listeners.push(f)}},
  notifications: {create: n => notes.push(n.message)},
};
const realFetch = globalThis.fetch;
const ctx = vm.createContext({browser: api, globalThis: null, console, navigator: {userAgent: "Firefox"}, URL, Map, Promise, JSON, setTimeout,
  fetch: (u, i) => realFetch(String(u).replace(/:17654/, ":" + port), i)});
ctx.globalThis = ctx;
for (const f of ["bridge-client.js", "background.js"]) vm.runInContext(fs.readFileSync(dir + "/" + f, "utf8").replace("if (typeof module", "if (false && typeof module"), ctx);
const call = (msg) => new Promise(res => {
  const ret = listeners[0](msg, {tab: {id: 1, url: "https://example.com/"}}, res);
  if (ret !== true) setTimeout(() => res("no-async-response"), 50);
});
(async () => {
  const good = await call({type: "bridge", path: "/download", body: {url: "https://example.com/v", media: true, browser: "firefox", referrer: "https://example.com/"}});
  const unknown = await call({type: "bridge", path: "/admin", body: {}});
  store.rychlikToken = "wrong";
  const bad = await call({type: "bridge", path: "/download", body: {url: "https://example.com/v"}});
  console.log(JSON.stringify({good, unknown, bad}));
})();
"""


def test_background_relays_content_script_requests_with_the_stored_token(tmp_path):
    received: list[BrowserDownload] = []
    tokens = TokenStore(tmp_path / "tok")
    bridge = BrowserBridge(tokens, received.append, port=0)
    assert bridge.start()
    try:
        driver = tmp_path / "bg.js"
        driver.write_text(BG_DRIVER)
        out = subprocess.run([node, str(driver), str(EXT), str(bridge.port), tokens.get()], capture_output=True, text=True, timeout=30)
    finally:
        bridge.stop()
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["good"] == {"ok": True, "result": {"ok": True}}
    assert result["unknown"]["ok"] is False  # only /download and /formats can be relayed
    assert result["bad"]["ok"] is False and result["bad"]["code"] == "bad-token"
    assert [d.url for d in received] == ["https://example.com/v"]
