"""Quality choices for the extension's panel: heights yt-dlp reports for a page, as ready-to-use format strings."""

from __future__ import annotations


def list_video_formats(url: str, browser: str = "", referrer: str = "") -> list[dict[str, str]]:
    import yt_dlp

    options = {"ignoreconfig": True, "quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    if referrer:
        options["http_headers"] = {"Referer": referrer}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    heights: set[int] = set()
    for entry in info.get("formats") or []:
        height = entry.get("height")
        if not height or str(entry.get("vcodec") or "") == "none":
            continue
        try:
            heights.add(int(height))
        except (TypeError, ValueError):
            continue
    result = [{"label": "Najlepšia kvalita", "format": "bestvideo+bestaudio/best"}]
    for height in sorted(heights, reverse=True):
        result.append({"label": f"{height}p", "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"})
    return result
