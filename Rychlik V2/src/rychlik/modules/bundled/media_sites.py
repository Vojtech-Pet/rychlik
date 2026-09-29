"""Rýchlik site module: XVideos, XNXX, EPorner, TGTube and ShemaleZ in one file.

Legacy contract (`can_handle(url)` + `download(worker)`); it only resolves an address and hands it to Rýchlik's
media download with `worker._run_media()`. Standalone: it imports nothing from Rýchlik.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from pathlib import Path

import requests

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DEFAULT_FORMAT = "bestvideo+bestaudio/best"

# --- XVideos / XNXX / EPorner: the page URL itself is what yt-dlp downloads --------------------------------------------

_SIMPLE_SITES = {
    "xvideos": ({"xvideos.com", "www.xvideos.com"}, re.compile(r"^/video\.([A-Za-z0-9_-]+)(?:/|$)"), "XVideos"),
    "xnxx": ({"xnxx.com", "www.xnxx.com"}, re.compile(r"^/video-([A-Za-z0-9_-]+)(?:/|$)"), "XNXX"),
    "eporner": ({"eporner.com", "www.eporner.com"}, re.compile(r"^/video-([A-Za-z0-9_-]+)(?:/|$)"), "EPorner"),
}

# --- TGTube: a catalog whose /out/ links redirect to the real video page -------------------------------------------------

TGTUBE_HOSTS = {"tgtube.com", "www.tgtube.com"}

# --- ShemaleZ: real file URL hidden behind a custom "base164" alphabet ---------------------------------------------------

SHEMALEZ_HOST_RE = re.compile(r"(?:^|\.)shemalez\.(?:com|tube)$", re.I)
SHEMALEZ_PATH_RE = re.compile(r"^/videos/(\d+)/")
BASE164_ALPHABET = "АВСDЕFGHIJKLМNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,~"
BASE164_KEEP_RE = re.compile(r"[^АВСЕМA-Za-z0-9.,~]")
TITLE_RE = re.compile(
    r'<title[^>]*>(.*?)</title>|'
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
    re.I | re.S,
)


def _clean_url(url: str) -> str:
    """Remove invisible paste artifacts before parsing or HTTP use."""
    return "".join(c for c in url.strip() if unicodedata.category(c) not in {"Cc", "Cf"})


def _simple_site(url: str) -> str | None:
    parsed = urllib.parse.urlparse(_clean_url(url))
    for key, (hosts, path_re, _label) in _SIMPLE_SITES.items():
        if parsed.netloc.lower() in hosts and path_re.match(parsed.path):
            return key
    return None


def _is_tgtube(url: str) -> bool:
    return urllib.parse.urlparse(_clean_url(url)).netloc.lower() in TGTUBE_HOSTS


def _is_shemalez(url: str) -> bool:
    parsed = urllib.parse.urlparse(_clean_url(url))
    return parsed.scheme in ("http", "https") and bool(SHEMALEZ_HOST_RE.search(parsed.netloc)) and SHEMALEZ_PATH_RE.match(parsed.path) is not None


def can_handle(url: str) -> bool:
    return _simple_site(url) is not None or _is_tgtube(url) or _is_shemalez(url)


def download(worker) -> None:
    item = worker.item
    source_url = _clean_url(item.url)
    site = _simple_site(source_url)
    if site is not None:
        _resolve_simple(worker, site, source_url)
    elif _is_tgtube(source_url):
        _resolve_tgtube(worker, source_url)
    elif _is_shemalez(source_url):
        _resolve_shemalez(worker, source_url)
    else:
        raise ValueError(f"Unsupported address: {source_url}")


def _folder(item) -> Path:
    destination = Path(item.destination)
    folder = destination if item.media else destination.parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _resolve_simple(worker, site: str, source_url: str) -> None:
    item = worker.item
    label = _SIMPLE_SITES[site][2]
    folder = _folder(item)
    item.status = f"Analyzuje {label}"
    item.error = ""
    item.speed = 0
    item.source_url = _clean_url(item.source_url) if item.source_url else source_url
    item.url = source_url
    item.media = True
    item.destination = str(folder)
    item.display_name = ""
    item.video_format = item.video_format or DEFAULT_FORMAT
    worker.update(item)
    worker._run_media()


def _resolve_tgtube(worker, source_url: str) -> None:
    item = worker.item
    parsed = urllib.parse.urlparse(source_url)
    if not parsed.path.rstrip("/").endswith("/out"):
        raise RuntimeError(
            "TGTube je katalog externych videi, nie video hosting. "
            "Otvor kartu videa a do Rychlika vloz jej konecnu URL alebo TGTube /out/ odkaz"
        )
    item.status = "Presmeruva TGTube odkaz"
    item.error = ""
    item.speed = 0
    worker.update(item)

    response = requests.get(
        source_url,
        headers={"User-Agent": USER_AGENT, "Referer": f"{parsed.scheme}://{parsed.netloc}/"},
        timeout=(10, 25),
        allow_redirects=True,
        stream=True,
    )
    try:
        target_url = _clean_url(response.url)
    finally:
        response.close()
    target = urllib.parse.urlparse(target_url)
    if target.scheme not in ("http", "https") or target.netloc.lower() in TGTUBE_HOSTS:
        raise RuntimeError("TGTube presmerovanie nevratilo platnu externu video stranku")

    item.source_url = source_url
    item.url = target_url
    # A target that one of this file's own sites handles is resolved by that site; anything else goes to yt-dlp as-is.
    site = _simple_site(target_url)
    if site is not None:
        _resolve_simple(worker, site, target_url)
        return
    if _is_shemalez(target_url):
        _resolve_shemalez(worker, target_url)
        return
    folder = _folder(item)
    item.referrer = source_url
    item.media = True
    item.destination = str(folder)
    item.display_name = ""
    item.video_format = item.video_format or DEFAULT_FORMAT
    worker.update(item)
    worker._run_media()


def _resolve_shemalez(worker, source_url: str) -> None:
    item = worker.item
    video_id = SHEMALEZ_PATH_RE.match(urllib.parse.urlparse(source_url).path).group(1)
    item.status = "Analyzuje ShemaleZ"
    item.error = ""
    item.speed = 0
    worker.update(item)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": source_url, "X-Requested-With": "XMLHttpRequest"})
    response = session.get(f"https://shemalez.com/api/videofile.php?video_id={video_id}", timeout=(10, 25))
    response.raise_for_status()
    entries = response.json()
    if not entries or not entries[0].get("video_url"):
        raise RuntimeError("ShemaleZ nevrátil video URL (pravdepodobne nedostupné video)")
    media_path = base164_decode(entries[0]["video_url"])
    media_url = "https://shemalez.com" + media_path if media_path.startswith("/") else media_path

    page = session.get(source_url, timeout=(10, 25))
    page.raise_for_status()

    folder = _folder(item)
    item.source_url = _clean_url(item.source_url) if item.source_url else source_url
    item.referrer = source_url
    item.url = media_url
    item.media = True
    item.destination = str(folder)
    item.display_name = _safe_name(_extract_title(page.text) or f"shemalez_{video_id}")
    item.video_format = item.video_format or "best"
    worker.update(item)
    worker._run_media()


def base164_decode(payload: str) -> str:
    """Port of the site's base164_decode(): custom alphabet + JS base64 math."""
    payload = BASE164_KEEP_RE.sub("", payload)
    out = bytearray()
    i = 0
    while i < len(payload):
        a = BASE164_ALPHABET.find(payload[i]); i += 1
        o = BASE164_ALPHABET.find(payload[i]) if i < len(payload) else -1; i += 1
        n = BASE164_ALPHABET.find(payload[i]) if i < len(payload) else -1; i += 1
        r = BASE164_ALPHABET.find(payload[i]) if i < len(payload) else -1; i += 1
        if a < 0 or o < 0:
            break
        out.append(((a << 2) | (o >> 4)) & 0xFF)
        if n >= 0:
            out.append((((o & 15) << 4) | (n >> 2)) & 0xFF)
            if r >= 0:
                out.append((((n & 3) << 6) | r) & 0xFF)
    return out.decode("utf-8", errors="replace")


def _extract_title(page: str) -> str | None:
    match = TITLE_RE.search(page)
    if not match:
        return None
    title = match.group(1) or match.group(2) or ""
    title = re.sub(r"\s*-\s*(ShemaleZ|SHEMALEZ)[^-]*$", "", title.strip())
    return re.sub(r"\s+", " ", title).strip()


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" .")[:180] or "video"
