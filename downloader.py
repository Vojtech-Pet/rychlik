from __future__ import annotations

import json
import importlib.util
import os
import re
import base64
import signal
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import requests


@dataclass
class Download:
    url: str
    destination: str
    status: str = "Čaká"
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    media: bool = False
    display_name: str = ""
    auth_browser: str = ""
    source_url: str = ""
    referrer: str = ""
    max_segments: int = 4
    speed_limit: int = 0
    category: str = ""
    video_format: str = "bestvideo+bestaudio/best"

    @property
    def filename(self) -> str:
        return self.display_name or Path(self.destination).name


class DownloadWorker:
    def __init__(self, item: Download, update: Callable[[Download], None]):
        self.item = item
        self.update = update
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self._pause.clear()
            return
        self._pause.clear()
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause.set()
        self.item.status = "Pozastavené"
        self.item.speed = 0
        self.update(self.item)

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()
        self._terminate_process()

    def _set_process(self, process: subprocess.Popen[str] | None) -> None:
        with self._process_lock:
            self._process = process

    def _terminate_process(self) -> None:
        with self._process_lock:
            process = self._process
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                process.kill()

    def _run(self) -> None:
        if self.item.url.startswith("data:"):
            self._run_data_url()
            return
        module = find_download_module(self.item.url)
        if module:
            self._run_download_module(module)
            return
        try:
            resolved = resolve_known_site(self.item.url)
        except PremiumContentError as exc:
            self.item.status = "Chyba"
            self.item.error = str(exc)
            self.update(self.item)
            return
        if resolved:
            direct_url, title = resolved
            current = Path(self.item.destination)
            is_media_stream = ".m3u8" in urllib.parse.urlparse(direct_url).path.lower()
            folder = current if self.item.media else current.parent
            safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")[:180] or "video"
            if not self.item.source_url:
                self.item.source_url = self.item.url
            self.item.url = direct_url
            self.item.media = is_media_stream
            self.item.destination = str(folder if is_media_stream else folder / f"{safe_name}.mp4")
            self.item.display_name = safe_name if is_media_stream else f"{safe_name}.mp4"
        if self.item.media:
            self._run_media()
            return
        destination = Path(self.item.destination)
        part = destination.with_name(destination.name + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "Rychlik/0.1"}
        if self.item.referrer:
            headers["Referer"] = self.item.referrer
        if existing:
            headers["Range"] = f"bytes={existing}-"

        try:
            self.item.status = "Sťahuje sa"
            self.item.error = ""
            self.update(self.item)
            segment_parts = list(destination.parent.glob(destination.name + ".part.*"))
            if not existing and self.item.max_segments > 1:
                probe_headers = {key: value for key, value in headers.items() if key != "Range"}
                try:
                    head = requests.head(self.item.url, headers=probe_headers, timeout=(10, 15), allow_redirects=True)
                    total = int(head.headers.get("content-length", "0") or 0)
                    supports_ranges = "bytes" in head.headers.get("accept-ranges", "").lower()
                except requests.RequestException:
                    total, supports_ranges = 0, False
                if total > 0 and ((supports_ranges and total >= 4 * 1024 * 1024) or segment_parts):
                    self._run_segmented(destination, total, probe_headers)
                    return
            with requests.get(self.item.url, headers=headers, stream=True, timeout=(15, 30), allow_redirects=True) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type:
                    page = response.content[:8 * 1024 * 1024].decode(response.encoding or "utf-8", errors="replace")
                    media_url = extract_media_from_page(page)
                    if not media_url:
                        raise ValueError("Stránka neobsahuje rozpoznateľnú adresu video streamu")
                    if not self.item.source_url:
                        self.item.source_url = self.item.url
                    self.item.url = media_url
                    self.item.media = True
                    self.item.display_name = extract_page_title(page) or "Video zo stránky"
                    self.item.destination = str(destination.parent)
                    self._run_media()
                    return
                resumed = existing > 0 and response.status_code == 206
                if existing and not resumed:
                    existing = 0
                length = int(response.headers.get("content-length", "0") or 0)
                self.item.total = existing + length if length else 0
                self.item.downloaded = existing
                mode = "ab" if resumed else "wb"
                last_time = time.monotonic()
                last_bytes = existing

                with part.open(mode) as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if self._cancel.is_set():
                            self.item.status = "Zrušené"
                            self.item.speed = 0
                            self.update(self.item)
                            return
                        while self._pause.is_set():
                            if self._cancel.wait(0.15):
                                self.item.status = "Zrušené"
                                self.update(self.item)
                                return
                        if not chunk:
                            continue
                        output.write(chunk)
                        self.item.downloaded += len(chunk)
                        if self.item.speed_limit > 0:
                            time.sleep(len(chunk) / self.item.speed_limit)
                        now = time.monotonic()
                        elapsed = now - last_time
                        if elapsed >= 0.4:
                            self.item.speed = (self.item.downloaded - last_bytes) / elapsed
                            last_time, last_bytes = now, self.item.downloaded
                            self.update(self.item)

            os.replace(part, destination)
            self.item.status = "Dokončené"
            self.item.speed = 0
            self.item.downloaded = self.item.total or destination.stat().st_size
            self.item.total = self.item.downloaded
            self.update(self.item)
        except Exception as exc:
            self.item.status = "Chyba"
            self.item.speed = 0
            self.item.error = str(exc)
            self.update(self.item)

    def _run_segmented(self, destination: Path, total: int, headers: dict[str, str]) -> None:
        if total <= 0:
            raise ValueError("Server neposkytol veľkosť súboru pre segmentované sťahovanie")
        count = max(1, min(int(self.item.max_segments), 16, total // (1024 * 1024)))
        ranges: list[tuple[int, int, Path]] = []
        base_size = total // count
        for index in range(count):
            start = index * base_size
            end = total - 1 if index == count - 1 else (index + 1) * base_size - 1
            ranges.append((start, end, destination.with_name(destination.name + f".part.{index}")))

        lock = threading.Lock()
        downloaded = 0
        for start, end, path in ranges:
            size = path.stat().st_size if path.exists() else 0
            expected = end - start + 1
            if size > expected:
                path.write_bytes(b"")
                size = 0
            downloaded += size
        self.item.total = total
        self.item.downloaded = downloaded
        self.update(self.item)
        started_at = time.monotonic()
        started_bytes = downloaded
        per_segment_limit = self.item.speed_limit / count if self.item.speed_limit > 0 else 0

        def fetch_range(spec: tuple[int, int, Path]) -> None:
            nonlocal downloaded
            start, end, path = spec
            existing_size = path.stat().st_size if path.exists() else 0
            expected = end - start + 1
            if existing_size == expected:
                return
            request_headers = dict(headers)
            request_headers["Range"] = f"bytes={start + existing_size}-{end}"
            with requests.get(self.item.url, headers=request_headers, stream=True,
                              timeout=(15, 30), allow_redirects=True) as response:
                if response.status_code != 206:
                    raise ValueError("Server odmietol segmentovanú Range požiadavku")
                with path.open("ab") as output:
                    for chunk in response.iter_content(256 * 1024):
                        if self._cancel.is_set():
                            raise RuntimeError("Sťahovanie bolo zrušené")
                        while self._pause.is_set():
                            if self._cancel.wait(0.15):
                                raise RuntimeError("Sťahovanie bolo zrušené")
                        if not chunk:
                            continue
                        output.write(chunk)
                        with lock:
                            downloaded += len(chunk)
                            self.item.downloaded = downloaded
                            elapsed = max(time.monotonic() - started_at, 0.001)
                            self.item.speed = (downloaded - started_bytes) / elapsed
                            self.update(self.item)
                        if per_segment_limit > 0:
                            time.sleep(len(chunk) / per_segment_limit)

        try:
            with ThreadPoolExecutor(max_workers=count, thread_name_prefix="rychlik-segment") as pool:
                futures = [pool.submit(fetch_range, spec) for spec in ranges]
                for future in futures:
                    future.result()
        except Exception:
            if self._cancel.is_set():
                self.item.status = "Zrušené"
                self.item.speed = 0
                self.update(self.item)
                return
            raise

        combined = destination.with_name(destination.name + ".part")
        with combined.open("wb") as output:
            for _start, _end, path in ranges:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        if combined.stat().st_size != total:
            raise ValueError("Veľkosť spojeného súboru nesúhlasí so serverom")
        os.replace(combined, destination)
        for _start, _end, path in ranges:
            path.unlink(missing_ok=True)
        self.item.downloaded = self.item.total = total
        self.item.speed = 0
        self.item.status = "Dokončené"
        self.update(self.item)

    def _run_data_url(self) -> None:
        try:
            match = re.fullmatch(r"data:([\w.+-]+/[\w.+-]+)(;base64)?,(.*)", self.item.url, re.S)
            if not match:
                raise ValueError("Neplatná dátová URL")
            mime, encoded, payload = match.groups()
            data = base64.b64decode(payload, validate=True) if encoded else urllib.parse.unquote_to_bytes(payload)
            if len(data) > 100 * 1024 * 1024:
                raise ValueError("Dátový súbor je väčší ako povolených 100 MB")
            destination = Path(self.item.destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".part")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
            self.item.downloaded = self.item.total = len(data)
            self.item.status = "Dokončené"
            self.item.speed = 0
            self.item.display_name = destination.name
            self.item.url = f"data:{mime};base64,[lokálny obsah]"
            self.item.source_url = self.item.url
            self.update(self.item)
        except Exception as exc:
            self.item.status = "Chyba"
            self.item.error = str(exc)
            self.update(self.item)

    def _run_download_module(self, module) -> None:
        try:
            module.download(self)
        except Exception as exc:
            self._set_process(None)
            self.item.status = "Zrušené" if self._cancel.is_set() else "Chyba"
            self.item.speed = 0
            self.item.error = "" if self._cancel.is_set() else str(exc)
            self.update(self.item)

    def _run_media(self) -> None:
        try:
            import yt_dlp

            folder = Path(self.item.destination)
            folder.mkdir(parents=True, exist_ok=True)
            self.item.status = "Analyzuje video"
            self.item.error = ""
            self.update(self.item)

            def progress(data):
                if self._cancel.is_set():
                    raise RuntimeError("Sťahovanie bolo zrušené")
                while self._pause.is_set():
                    if self._cancel.wait(0.15):
                        raise RuntimeError("Sťahovanie bolo zrušené")
                if data.get("status") == "downloading":
                    self.item.status = "Sťahuje video"
                    self.item.downloaded = int(data.get("downloaded_bytes") or 0)
                    self.item.total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                    self.item.speed = float(data.get("speed") or 0)
                    self.update(self.item)

            output_name = "%(title)s.%(ext)s"
            if self.item.display_name and self.item.display_name != "Video zo stránky":
                safe_name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", self.item.display_name).strip(" .")[:180]
                if safe_name:
                    output_name = safe_name + ".%(ext)s"
            def download_with_options(use_browser_cookies: bool):
                options = {
                    "ignoreconfig": True,
                    "format": self.item.video_format or "bestvideo+bestaudio/best",
                    "merge_output_format": "mp4",
                    "outtmpl": str(folder / output_name),
                    "noplaylist": True,
                    "progress_hooks": [progress],
                    "quiet": True,
                    "no_warnings": True,
                    "match_filter": self._reject_non_media,
                }
                if use_browser_cookies and self.item.auth_browser in ("firefox", "chromium", "chrome"):
                    options["cookiesfrombrowser"] = (self.item.auth_browser,)
                if self.item.referrer:
                    options["http_headers"] = {"Referer": self.item.referrer}
                if self.item.speed_limit > 0:
                    options["ratelimit"] = self.item.speed_limit
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(self.item.url, download=True)
                    requested = info.get("requested_downloads") or []
                    final_path = requested[0].get("filepath") if requested else ydl.prepare_filename(info)
                    self.item.destination = str(final_path)
                    self.item.display_name = Path(final_path).name

            try:
                download_with_options(False)
            except Exception:
                if self.item.auth_browser not in ("firefox", "chromium", "chrome"):
                    raise
                download_with_options(True)
            self.item.status = "Dokončené"
            self.item.speed = 0
            self.update(self.item)
        except Exception as exc:
            self.item.status = "Zrušené" if self._cancel.is_set() else "Chyba"
            self.item.speed = 0
            self.item.error = "" if self._cancel.is_set() else str(exc)
            self.update(self.item)

    @staticmethod
    def _reject_non_media(info, *, incomplete=False):
        if incomplete:
            return None
        extension = str(info.get("ext") or "").lower()
        protocol = str(info.get("protocol") or "").lower()
        if extension in {"html", "htm", "php", "asp", "aspx"}:
            return "Adresa smeruje na HTML stránku, nie na video stream"
        if extension not in {"mp4", "webm", "mkv", "mov", "m4a", "mp3", "ts"} and not any(
            name in protocol for name in ("m3u8", "dash", "http_dash_segments")
        ):
            return f"Nepodporovaný typ média: {extension or 'neznámy'}"
        return None


def filename_from_url(url: str) -> str:
    if url.startswith("data:"):
        mime = url[5:].split(";", 1)[0].split(",", 1)[0]
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                     "image/gif": "gif", "image/svg+xml": "svg"}.get(mime, "bin")
        return f"vlozeny-subor.{extension}"
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    return name or "download.bin"


_SITE_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
_DOWNLOAD_MODULES: list[object] | None = None


def load_download_modules() -> list[object]:
    global _DOWNLOAD_MODULES
    if _DOWNLOAD_MODULES is not None:
        return _DOWNLOAD_MODULES

    modules: list[object] = []
    modules_dir = Path(__file__).with_name("download_modules")
    if modules_dir.is_dir():
        for path in sorted(modules_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"rychlik_download_module_{path.stem}", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue
            if callable(getattr(module, "can_handle", None)) and callable(getattr(module, "download", None)):
                modules.append(module)

    _DOWNLOAD_MODULES = modules
    return modules


def reload_download_modules() -> list[object]:
    global _DOWNLOAD_MODULES
    _DOWNLOAD_MODULES = None
    return load_download_modules()


def find_download_module(url: str):
    for module in load_download_modules():
        try:
            if module.can_handle(url):
                return module
        except Exception:
            continue
    return None


def has_download_module_for(url: str) -> bool:
    try:
        return find_download_module(url) is not None
    except Exception:
        return False


class PremiumContentError(Exception):
    """Raised when a resolver recognizes the page but the only freely
    reachable file is a short promo trailer for paywalled content elsewhere
    — deliberately not swallowed by resolve_known_site's generic except."""


def resolve_known_site(url: str) -> tuple[str, str] | None:
    """Resolvers for pages whose real video URL isn't present in the static
    HTML (and that yt-dlp has no extractor for), checked before falling back
    to generic HTML sniffing / yt-dlp."""
    for resolver in (_resolve_abtranny, _resolve_txxx_network):
        try:
            result = resolver(url)
        except PremiumContentError:
            raise
        except Exception:
            continue
        if result:
            return result
    return None


def _resolve_abtranny(url: str) -> tuple[str, str] | None:
    # abtranny.com's player fetches the stream URL at runtime via a JSON API
    # instead of embedding it in the page; the direct file lives on one of a
    # few CDN hosts chosen by the video's "storage group" id (sg_id), listed
    # in a `window.EoCR4` map inlined on every video page.
    match = re.match(r"^https?://(?:www\.)?abtranny\.com/video/(\d+)/", url)
    if not match:
        return None
    video_id = int(match.group(1))
    bucket_major = 1_000_000 * (video_id // 1_000_000)
    bucket_minor = 1_000 * (video_id // 1_000)
    headers = {"User-Agent": _SITE_USER_AGENT}
    page = requests.get(url, headers=headers, timeout=(10, 20)).text
    host_map_match = re.search(r"window\.EoCR4\s*=\s*(\{.*?\});", page)
    if not host_map_match:
        return None
    host_map = json.loads(host_map_match.group(1).replace("\\/", "/"))
    api_url = (f"https://abtranny.com/api/json/video/86400/"
               f"{bucket_major}/{bucket_minor}/{video_id}.json")
    response = requests.get(api_url, headers=headers, timeout=(10, 20))
    response.raise_for_status()
    data = response.json().get("video") or {}
    host = host_map.get(str(data.get("sg_id") or ""))
    if not host:
        return None
    direct_url = f"https://{host}/{bucket_minor}/{video_id}/{video_id}.mp4"
    title = data.get("title") or f"video-{video_id}"
    return direct_url, title


def _resolve_txxx_network(url: str) -> tuple[str, str] | None:
    
    match = re.match(r"^(https?://[^/]+)/videos/(\d+)/", url)
    if not match:
        return None
    base_url, video_id_str = match.group(1), match.group(2)
    video_id = int(video_id_str)
    headers = {"User-Agent": _SITE_USER_AGENT}
    page = requests.get(url, headers=headers, timeout=(10, 20)).text
    constants_match = re.search(r"window\.constants\s*=\s*(\{.*?\})\s*(?:;|\n)", page)
    if not constants_match or '"video_id"' not in constants_match.group(1):
        return None
    constants = json.loads(constants_match.group(1))
    lifetime = str(constants.get("query", {}).get("lifetime") or "86400")
    bucket_major = 1_000_000 * (video_id // 1_000_000)
    bucket_minor = 1_000 * (video_id // 1_000)
    api_url = f"{base_url}/api/json/video/{lifetime}/{bucket_major}/{bucket_minor}/{video_id}.json"
    response = requests.get(api_url, headers=headers, timeout=(10, 20))
    response.raise_for_status()
    data = response.json().get("video") or {}

    title = data.get("title") or f"video-{video_id}"

    # Fallback to direct MP4 trailer (often short/promo)
    pv = data.get("pv")
    if not pv:
        return None
    
    direct_url = "https://" + pv.replace("\\/", "/").lstrip("/")
    return direct_url, title


def extract_media_from_page(page: str) -> str | None:
    """Extract common HTML5/playerConfig media URLs without executing page code."""
    # Embedded players often generate fresh signed manifests dynamically. Prefer
    # their page URL over stale fallback <source> elements left in the HTML.
    embed = re.search(
        r'<iframe[^>]+src=["\'](https?://[^"\']+/(?:embed|player)/?[^"\']*)["\']',
        page, re.I,
    )
    if embed:
        return embed.group(1).replace("&amp;", "&")

    # Several tube players expose a JSON object such as
    # `sources: {"hlsAuto": "..."}`. Prefer adaptive manifests.
    for match in re.finditer(r"sources\s*:\s*(\{[^\n;]+\})", page, re.I):
        try:
            sources = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(sources, dict):
            for key in ("hlsAuto", "hls", "dash", "mp4"):
                value = sources.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value.replace("\\/", "/")
                if isinstance(value, list) and value:
                    candidate = value[-1]
                    if isinstance(candidate, dict):
                        candidate = candidate.get("src")
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                        return candidate.replace("\\/", "/")

    patterns = (
        r'<(?:video|source)[^>]+src=["\'](https?://[^"\']+)',
        r'<meta[^>]+(?:property|name)=["\'](?:og:video|twitter:player:stream)["\'][^>]+content=["\'](https?://[^"\']+)',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return match.group(1).replace("&amp;", "&")
    return None


def extract_page_title(page: str) -> str | None:
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, page, re.I | re.S)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip().replace("&amp;", "&")
    return None


def _human_size(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def list_video_formats(url: str, browser: str = "", referrer: str = "") -> list[dict[str, str]]:
    if has_download_module_for(url) or resolve_known_site(url):
        return [{"label": "Najlepšia kvalita", "format": "bestvideo+bestaudio/best"}]

    import yt_dlp

    def extract(use_browser_cookies: bool):
        options = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        }
        if use_browser_cookies and browser in ("firefox", "chromium", "chrome"):
            options["cookiesfrombrowser"] = (browser,)
        if referrer:
            options["http_headers"] = {"Referer": referrer}
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = extract(False)
    except Exception:
        info = extract(True)

    heights: set[int] = set()
    for entry in info.get("formats") or []:
        height = entry.get("height")
        if not height:
            continue
        vcodec = str(entry.get("vcodec") or "")
        if vcodec == "none":
            continue
        try:
            heights.add(int(height))
        except (TypeError, ValueError):
            continue

    result = [{"label": "Najlepšia kvalita", "format": "bestvideo+bestaudio/best"}]
    for height in sorted(heights, reverse=True):
        result.append({
            "label": f"{height}p",
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
        })
    return result


def load_items(path: Path) -> list[Download]:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        items = [Download(**record) for record in records]
        for item in items:
            if item.status == "Sťahuje sa":
                item.status = "Pozastavené"
                item.speed = 0
        return items
    except (OSError, ValueError, TypeError):
        return []


def save_items(path: Path, items: list[Download]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
