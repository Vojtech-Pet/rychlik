"""The bundled one-file module (XVideos, XNXX, EPorner, TGTube, ShemaleZ) behaves exactly like the five legacy modules."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from rychlik.modules.legacy_adapter import ResolutionError, resolve_with_legacy_module
from rychlik.modules.registry import LoadStatus, ModuleRegistry

BUNDLED = Path(__file__).parent.parent / "src" / "rychlik" / "modules" / "bundled" / "media_sites.py"
LEGACY_DIR = Path("/home/vojtech/Stiahnuté/rychlik-downloader/download_modules")
LEGACY_NAMES = ["xvideos", "xnxx", "eporner", "tgtube", "shemalez"]

URLS = [
    "https://www.xvideos.com/video.abc123/some-title", "https://xvideos.com/video.Ab_9/x", "https://www.xvideos.com/tags/foo", "https://xvideos.com/",
    "https://www.xnxx.com/video-abc123/title", "https://xnxx.com/video-zz/t", "https://www.xnxx.com/search/x",
    "https://www.eporner.com/video-abc/some-title/", "https://eporner.com/video-q1/x", "https://www.eporner.com/cat/x",
    "https://www.tgtube.com/videos/9/out", "https://tgtube.com/videos/1", "https://www.tgtube.com/",
    "https://shemalez.com/videos/123/some-title/", "https://www.shemalez.tube/videos/9/x/", "https://shemalez.com/videos/abc/", "https://shemalez.com/",
    "http://shemalez.com/videos/5/y/", "ftp://shemalez.com/videos/5/y/",
    "https://example.com/video.abc123/x", "https://evilxvideos.com/video.abc/x", "not a url", "",
    "https://www.xvideos.com/video.abc123/x​",  # invisible paste artefact
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundled():
    return _load(BUNDLED, "bundled_media_sites")


@pytest.fixture(scope="module")
def legacy():
    if not LEGACY_DIR.is_dir():
        pytest.skip("legacy modules folder not present")
    return {n: _load(LEGACY_DIR / f"{n}.py", f"legacy_{n}") for n in LEGACY_NAMES}


def test_can_handle_matches_the_union_of_the_five_legacy_modules(bundled, legacy):
    for url in URLS:
        expected = any(m.can_handle(url) for m in legacy.values())
        assert bundled.can_handle(url) == expected, url


class _Response:
    def __init__(self, url="", text="", payload=None):
        self.url, self.text, self._payload = url, text, payload

    def close(self):
        pass

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_requests(target_url, video_url_payload, page_html):
    calls = []

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, url, **kw):
            calls.append(url)
            return _Response(text=page_html, payload=[{"video_url": video_url_payload}]) if "videofile.php" in url or True else None

    def get(url, **kw):
        calls.append(url)
        return _Response(url=target_url)

    return types.SimpleNamespace(Session=Session, get=get), calls


@pytest.mark.parametrize("url,name", [("https://www.xvideos.com/video.abc123/some-title", "xvideos"), ("https://www.xnxx.com/video-abc123/title", "xnxx"),
                                      ("https://www.eporner.com/video-abc/some-title/", "eporner")])
def test_page_url_sites_produce_the_same_request_as_the_legacy_modules(bundled, legacy, tmp_path, url, name):
    new = resolve_with_legacy_module(bundled, url, tmp_path).request
    old = resolve_with_legacy_module(legacy[name], url, tmp_path).request
    assert new == old and new.url == url and new.media.referer is None


def test_tgtube_out_link_resolves_the_redirect_like_the_legacy_module(bundled, legacy, tmp_path, monkeypatch):
    fake, calls = _fake_requests("https://other-site.example/watch/9", None, "")
    monkeypatch.setattr(bundled, "requests", fake)
    monkeypatch.setattr(legacy["tgtube"], "requests", fake)
    stub = types.ModuleType("downloader")
    stub.find_download_module = lambda url: None
    monkeypatch.setitem(sys.modules, "downloader", stub)
    url = "https://www.tgtube.com/videos/9/out"
    new = resolve_with_legacy_module(bundled, url, tmp_path).request
    old = resolve_with_legacy_module(legacy["tgtube"], url, tmp_path).request
    assert new == old and new.url == "https://other-site.example/watch/9" and new.media.referer == url


def test_tgtube_redirect_into_a_bundled_site_is_resolved_by_that_site(bundled, tmp_path, monkeypatch):
    fake, _ = _fake_requests("https://www.xvideos.com/video.zz9/t", None, "")
    monkeypatch.setattr(bundled, "requests", fake)
    request = resolve_with_legacy_module(bundled, "https://www.tgtube.com/videos/9/out", tmp_path).request
    assert request.url == "https://www.xvideos.com/video.zz9/t" and request.media.referer is None


def test_tgtube_rejects_catalog_pages_and_redirects_back_to_tgtube(bundled, tmp_path, monkeypatch):
    with pytest.raises(ResolutionError, match="katalog"):
        resolve_with_legacy_module(bundled, "https://www.tgtube.com/videos/1", tmp_path)
    fake, _ = _fake_requests("https://www.tgtube.com/again", None, "")
    monkeypatch.setattr(bundled, "requests", fake)
    with pytest.raises(ResolutionError, match="presmerovanie"):
        resolve_with_legacy_module(bundled, "https://www.tgtube.com/videos/9/out", tmp_path)


def _encode_base164(bundled, text: str) -> str:
    alphabet, data = bundled.BASE164_ALPHABET, text.encode("utf-8")
    out = []
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        b = list(chunk) + [0] * (3 - len(chunk))
        idx = [b[0] >> 2, ((b[0] & 3) << 4) | (b[1] >> 4), ((b[1] & 15) << 2) | (b[2] >> 6), b[2] & 63]
        out.extend(alphabet[k] for k in idx[: len(chunk) + 1])
    return "".join(out)


def test_shemalez_decodes_the_hidden_url_and_matches_the_legacy_module(bundled, legacy, tmp_path, monkeypatch):
    payload = _encode_base164(bundled, "/media/v123.mp4")
    assert bundled.base164_decode(payload) == "/media/v123.mp4" == legacy["shemalez"].base164_decode(payload)
    fake, _ = _fake_requests("", payload, "<title>Some Title - ShemaleZ.com</title>")
    monkeypatch.setattr(bundled, "requests", fake)
    monkeypatch.setattr(legacy["shemalez"], "requests", fake)
    url = "https://shemalez.com/videos/123/some-title/"
    new = resolve_with_legacy_module(bundled, url, tmp_path).request
    old = resolve_with_legacy_module(legacy["shemalez"], url, tmp_path).request
    assert new == old
    assert new.url == "https://shemalez.com/media/v123.mp4" and new.filename_hint == "Some Title" and new.media.referer == url and new.media.video_format == "bestvideo+bestaudio/best"


def test_shemalez_without_a_video_url_is_a_resolution_error(bundled, tmp_path, monkeypatch):
    fake, _ = _fake_requests("", "", "<title>x</title>")
    monkeypatch.setattr(bundled, "requests", fake)
    with pytest.raises(ResolutionError, match="video URL"):
        resolve_with_legacy_module(bundled, "https://shemalez.com/videos/1/x/", tmp_path)


def test_the_file_installs_through_the_registry_and_dispatches_all_five_sites(tmp_path):
    registry = ModuleRegistry(tmp_path / "modules")
    info = registry.add(BUNDLED, trust_confirmed=True)
    assert info.dispatchable
    for url in ("https://www.xvideos.com/video.a1/x", "https://www.xnxx.com/video-a1/x", "https://www.eporner.com/video-a1/x/",
                "https://www.tgtube.com/videos/9/out", "https://shemalez.com/videos/1/x/"):
        assert registry.find_handler(url).module_id == "media_sites"
    assert registry.find_handler("https://example.com/x") is None
    assert registry.get("media_sites").load_status == LoadStatus.LOADED
    result = registry.resolve("https://www.xvideos.com/video.a1/x", tmp_path / "dl")
    assert result.request.url == "https://www.xvideos.com/video.a1/x" and result.request.media is not None
