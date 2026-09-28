# NON-PRODUCTION DESIGN PROTOTYPE -- not wired into the application.
"""Builds every static mockup: HTML (design/prototype/html/) -> PNG (design/mockups/).
Usage: python3 build.py [desktop|friendsend|all] [name-substring]"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desktop as D  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "prototype" / "html"
OUT = ROOT / "mockups"
BROWSER = "/usr/bin/brave"


def render(html, rel_png, w, h):
    stem = rel_png.replace("/", "_").removesuffix(".png")
    hp = HTML / f"{stem}.html"
    HTML.mkdir(parents=True, exist_ok=True)
    hp.write_text(html, encoding="utf-8")
    png = OUT / rel_png
    png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [BROWSER, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
           f"--window-size={w},{h}", f"--screenshot={png}", f"file://{hp}"]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    print("rendered", rel_png, f"{w}x{h}")


def desktop_jobs():
    W, H = 1366, 768
    dlg = lambda d, view="All", theme="dark", **kw: D.main_window(theme, W, H, view, overlay=d, **kw)
    yield "desktop/01_main_dark_1366.png", W, H, D.main_window("dark", 1366, 768, hover=1)
    yield "desktop/02_main_dark_1920.png", 1920, 1080, D.main_window("dark", 1920, 1080, hover=1, rows=D.ROWS + D.MORE[:8], count="22 tasks")
    yield "desktop/03_main_dark_2560.png", 2560, 1440, D.main_window("dark", 2560, 1440, hover=1, rows=D.ROWS + D.MORE, count="30 tasks", counts={"All": 30, "Downloading": 3, "Waiting": 2, "Paused": 1, "Completed": 22, "Failed": 1})
    yield "desktop/04_main_light.png", 1920, 1080, D.main_window("light", 1920, 1080, hover=1, rows=D.ROWS + D.MORE[:8], count="22 tasks")
    yield "desktop/04b_main_light_1366.png", W, H, D.main_window("light", 1366, 768, hover=1)
    yield "desktop/04c_main_compact_1100.png", 1100, 700, D.main_window("dark", 1100, 700, hover=1)
    yield "desktop/05_add_download.png", W, H, dlg(D.dlg_add())
    yield "desktop/06_completed_context.png", W, H, D.main_window("dark", W, H, selected=(11,), overlay=D.ctx_menu(560, 468))
    yield "desktop/07_share_selector.png", W, H, dlg(D.dlg_share(), selected=(11,))
    yield "desktop/08_send_device.png", W, H, dlg(D.dlg_send(), selected=(11,))
    yield "desktop/09_preparing.png", W, H, dlg(D.dlg_progress("preparing"), selected=(11,))
    yield "desktop/10_transcoding.png", W, H, dlg(D.dlg_progress("transcoding"), selected=(11,))
    yield "desktop/11_devices.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}')
    yield "desktop/12_pair_device.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}', overlay=D.dlg_pair())
    yield "desktop/13_empty_state.png", W, H, D.empty_main("dark", W, H)
    yield "desktop/14_active_downloading.png", W, H, D.main_window("dark", W, H, "Downloading", title="Downloading", rows=D.ROWS[:3], hover=None, hint=True, counts=None)
    yield "desktop/15_multi_selection.png", W, H, D.main_window("dark", W, H, selected=(0, 1, 3, 5), checked=(0, 1, 3, 5), bulk=(4, False))
    yield "desktop/16_media_detected.png", W, H, dlg(D.dlg_media())
    yield "desktop/17_details_information.png", W, H, dlg(D.dlg_details("Information"), selected=(1,))
    yield "desktop/18_details_connections.png", W, H, dlg(D.dlg_details("Connections"), selected=(1,))
    yield "desktop/19_details_log.png", W, H, dlg(D.dlg_details("Log"), selected=(1,))
    yield "desktop/20_share_by_link.png", W, H, dlg(D.dlg_link(), selected=(11,))
    yield "desktop/21_device_offline.png", W, H, dlg(D.dlg_send(offline=True), selected=(11,))
    yield "desktop/22_sending.png", W, H, dlg(D.dlg_progress("sending"), selected=(11,))
    yield "desktop/23_received.png", W, H, dlg(D.dlg_result("received"), selected=(11,))
    yield "desktop/24_send_failure.png", W, H, dlg(D.dlg_result("failed"), selected=(11,))
    yield "desktop/25_trusted_device.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}', overlay=D.dlg_device("details"))
    yield "desktop/26_identity_changed.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}', overlay=D.dlg_device("changed"))
    yield "desktop/27_forget_confirmation.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}', overlay=D.dlg_device("forget"))
    yield "desktop/28_settings.png", W, H, dlg(D.dlg_settings())
    yield "desktop/29_pair_success.png", W, H, D.window("dark", W, H, f'<div class="body">{D.sidebar("Devices")}{D.devices_view(W)}</div>{D.statusbar()}', overlay=D.dlg_pair(True))
    yield "desktop/30_components_dark.png", 1366, 900, D.component_sheet("dark")
    yield "desktop/31_components_light.png", 1366, 900, D.component_sheet("light")
    yield "desktop/32_share_by_link_light.png", W, H, dlg(D.dlg_link(), theme="light", selected=(11,))
    yield "desktop/33_send_device_light.png", W, H, dlg(D.dlg_send(), theme="light", selected=(11,))


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    sub = sys.argv[2] if len(sys.argv) > 2 else ""
    jobs = []
    if which in ("desktop", "all"):
        jobs += list(desktop_jobs())
    if which in ("friendsend", "all"):
        import friendsend as F
        jobs += list(F.jobs())
    for rel, w, h, html in jobs:
        if sub in rel:
            render(html, rel, w, h)


if __name__ == "__main__":
    main()
