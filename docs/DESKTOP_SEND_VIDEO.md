# Desktop "Send video" — fetch a video and copy it to the clipboard

New GUI action (toolbar button + File menu, "Send video…"), separate from Add download / the download queue.

## Why

Rýchlik's desktop already auto-detects video pages in Add download (`known_video_page`, yt-dlp's own extractor list) and downloads them as a normal, permanent queue entry. That is right for building a library, but wrong for "I just want to hand this video to Facebook/Messenger right now": the user doesn't want a Downloads-manager entry, they want the video itself, ready to paste where they're posting.

## What it does

1. Toolbar "Send video" / File → "Send video…" opens `SendVideoDialog`.
2. The link is fetched with yt-dlp (`rychlik.sharing.video_fetch.VideoFetchService`) into a **private, throwaway cache** (`~/.local/share/rychlik/video_cache/`, files older than 1 hour purged on each use) — never `DownloadManagerService`, never the queue/history/state store. Runs on a background `QThread` with progress and Cancel.
3. On success the file is copied to the **clipboard** as a file URL (`QMimeData.setUrls([...])`, i.e. the desktop's standard "Copy" `text/uri-list`), so `Ctrl+V` in a browser/chat app that accepts pasted files (Facebook's post composer, Messenger/WhatsApp/Element desktop, a file manager, …) attaches the actual video — not a link back to the origin site. "Open folder" is offered as a manual fallback (drag-and-drop) since paste-file support varies by target app.
4. A link yt-dlp doesn't recognise is rejected up front with a plain message, before anything is fetched.

## Evidence

- `tests/test_video_fetch_service.py` (5): real yt-dlp against a local HTTP server — private cache dir (not the default Downloads dir), unrecognised link rejected before any fetch, cancel mid-fetch, stale-file purge, the real `known_video_page` on real URLs.
- `tests/test_gui_send_video.py` (5): the real dialog + real `QThread` + real Qt clipboard (offscreen platform) — fetch copies the file URL to the clipboard and shows "Copied to clipboard…", an unrecognised link shows a bounded error and copies nothing, cancel never copies anything, Paste fills the URL field, closing the dialog mid-fetch cancels the background thread.
- Full suite: 1247 passed.
- Manual: fetched a real YouTube video (`https://www.youtube.com/watch?v=jNQXAC9IVRw`, "Me at the zoo") through `VideoFetchService` — real 476 KB `video/mp4` file.

## Not done

- Not tested against a real paste target (Facebook's composer, Messenger desktop, …) — only that the clipboard holds a correct `text/uri-list` file URL, which is the standard desktop "copied file" format. Whether a given app's paste handler accepts a pasted *file* (vs. only pasted text/images) is up to that app and wasn't otherwise verifiable here.
- No xdg-desktop-portal "Share" integration (declined in favour of clipboard).
