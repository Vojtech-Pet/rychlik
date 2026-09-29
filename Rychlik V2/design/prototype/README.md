# NON-PRODUCTION DESIGN PROTOTYPE

This folder generates the static design mockups only. It is **not** part of Rýchlik or FriendSend, is not imported by
any application code, and must not be wired into the product. It exists so the approved visuals can be reproduced.

- `common.py` - token-driven CSS and the single icon set (reads `../final_design_tokens.json`)
- `desktop.py`, `friendsend.py` - HTML component builders for each app
- `build.py` - renders HTML to PNG with a headless Chromium-based browser (`BROWSER` in the file, default `/usr/bin/brave`)
- `contrast.py` - WCAG audit of the token pairs, writes `../CONTRAST_AUDIT.md`, fails on any regression
- `gen_inventory.py` - writes `../SCREEN_INVENTORY.md` and fails if a referenced mockup is missing
- `html/` - generated intermediate HTML (git-ignored, regenerate with `build.py`)

Regenerate everything: `python3 build.py all && python3 gen_inventory.py && python3 contrast.py`
Filters: `python3 build.py desktop 05_` renders only matching files.

Mockups render with Noto Sans (desktop) and Roboto (mobile) as stand-ins for the specified Inter/Roboto stack.
