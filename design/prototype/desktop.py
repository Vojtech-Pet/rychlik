# NON-PRODUCTION DESIGN PROTOTYPE -- not wired into the application.
"""Rýchlik Desktop mockups: HTML components -> full-page HTML strings."""
from common import base_css, icon

CSS = """
.win{position:relative;display:flex;flex-direction:column;width:100vw;height:100vh;background:var(--bg);overflow:hidden}
.menubar{height:28px;flex:none;display:flex;align-items:center;padding:0 8px;background:var(--surface);border-bottom:1px solid var(--border)}
.menubar span{padding:0 10px;color:var(--text2);height:28px;line-height:28px}
.toolbar{height:54px;flex:none;display:flex;align-items:center;padding:0 10px;gap:2px;background:var(--surface);border-bottom:1px solid var(--border)}
.tb{display:flex;align-items:center;gap:8px;height:34px;padding:0 12px;border-radius:7px;color:var(--text);font-weight:500}
.tb .ic{color:var(--icon)}
.tb.primary{background:var(--accent);color:var(--on-accent)} .tb.primary .ic{color:var(--on-accent)}
.tb.off{color:var(--text3)} .tb.off .ic{color:var(--text3)}
.tsep{width:1px;height:22px;background:var(--border);margin:0 8px}
.body{flex:1;display:flex;min-height:0;overflow:hidden}
.sidebar{width:190px;flex:none;background:var(--surface);border-right:1px solid var(--border);padding:6px 8px;overflow:hidden}
.sb-title{font-size:10.5px;line-height:14px;letter-spacing:.07em;text-transform:uppercase;color:var(--text2);padding:14px 8px 5px;font-weight:600}
.sb-item{height:30px;display:flex;align-items:center;gap:10px;padding:0 8px;border-radius:7px;color:var(--text)}
.sb-item .ic{color:var(--icon)} .sb-item .n{margin-left:auto;color:var(--text2);font-size:11px}
.sb-item.active{background:var(--accent-tint);color:var(--accent-text);font-weight:600;box-shadow:inset 2px 0 0 var(--accent)}
.sb-item.active .ic,.sb-item.active .n{color:var(--accent-text)}
.content{flex:1;min-width:0;min-height:0;overflow:hidden;position:relative;display:flex;flex-direction:column;padding:10px 16px 0}
.content.scroll::after{content:'';position:absolute;right:6px;top:150px;bottom:14px;width:4px;border-radius:2px;background:color-mix(in srgb,var(--text) 8%,transparent)}
.content.scroll::before{content:'';position:absolute;right:6px;top:150px;height:52%;width:4px;border-radius:2px;background:color-mix(in srgb,var(--text) 28%,transparent);z-index:2}
.pagehead{display:flex;align-items:center;gap:10px;height:30px}
.pagehead h1{font-size:15px;line-height:22px;font-weight:600}
.pagehead .sp{margin-left:auto;display:flex;gap:8px}
.filterbar{display:flex;gap:8px;align-items:center;margin:8px 0 10px;height:30px;flex:none}
.field{height:30px;border:1px solid var(--border);background:var(--surface2);border-radius:7px;display:flex;align-items:center;gap:8px;padding:0 10px;color:var(--text2);white-space:nowrap}
.field.text{color:var(--text)} .field.grow{flex:1}
.field.focus,.btn.focus{outline:2px solid var(--focus);outline-offset:1px}
.count{margin-left:auto;color:var(--text2)}
.bulk{display:flex;align-items:center;gap:6px;height:36px;margin:8px 0 10px;padding:0 8px 0 12px;border-radius:7px;background:var(--accent-tint);border:1px solid color-mix(in srgb,var(--accent) 45%,transparent)}
.bulk b{color:var(--accent-text);margin-right:6px}
.bulk .btn{height:26px;padding:0 10px;background:transparent;border-color:transparent}
.bulk .btn.dis{color:var(--text3)} .bulk .clr{margin-left:auto}
.table{border:1px solid var(--border);border-radius:7px;background:var(--surface);overflow:hidden;flex:none}
.thead,.tr{display:grid;grid-template-columns:var(--cols);align-items:center;padding:0 8px;column-gap:12px}
.thead{height:30px;background:var(--surface2);color:var(--text2);font-weight:600;font-size:11px;border-bottom:1px solid var(--border)}
.tr{height:42px;border-bottom:1px solid color-mix(in srgb,var(--border) 55%,transparent);position:relative}
.tr:last-child{border-bottom:none}
.tr.sel{background:var(--accent-tint);box-shadow:inset 2px 0 0 var(--accent)}
.tr.hover{background:var(--surface2)}
.check{width:16px;height:16px;border:1.5px solid var(--border-strong);border-radius:4px;display:grid;place-items:center}
.check.on{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.name{display:flex;align-items:center;gap:10px;min-width:0}
.name .t{min-width:0} .name b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:16px}
.name small{display:block;color:var(--text2);font-size:11px;line-height:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.name .ic{color:var(--icon)}
.cell{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
.prog{display:flex;align-items:center;gap:8px}
.bar{flex:1;height:6px;border-radius:3px;background:color-mix(in srgb,var(--text) 11%,transparent);overflow:hidden}
.bar i{display:block;height:100%;border-radius:3px}
.pct{width:34px;text-align:right;color:var(--text2);font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:6px;font-weight:500;white-space:nowrap}
.badge .ic{width:14px;height:14px}
.chip{display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 8px;border-radius:10px;font-size:11px;font-weight:600;border:1px solid currentColor;background:color-mix(in srgb,currentColor 10%,transparent)}
.chip .ic{width:12px;height:12px}
.statusbar{height:28px;flex:none;display:flex;align-items:center;gap:18px;padding:0 12px;background:var(--surface);border-top:1px solid var(--border);color:var(--text2);font-size:11.5px}
.statusbar .r{margin-left:auto}
.hint{margin-top:auto;padding:8px 2px 10px;color:var(--text2);font-size:11px}
.scrim{position:absolute;inset:0;background:var(--scrim)}
.dlg{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:var(--elev);border-radius:10px;box-shadow:var(--sh-dialog);overflow:hidden}
.dlg-h{display:flex;align-items:center;gap:10px;padding:14px 16px 4px;font-size:14px;line-height:20px;font-weight:600}
.dlg-h .x{margin-left:auto;color:var(--text2)}
.dlg-b{padding:8px 16px 4px}
.dlg-f{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:14px 16px 16px}
.dlg-f .l{margin-right:auto}
.lbl{color:var(--text2);font-size:11px;font-weight:600;margin:12px 0 5px}
.btn{height:30px;padding:0 14px;border-radius:7px;border:1px solid var(--border);background:var(--surface2);display:inline-flex;align-items:center;justify-content:center;gap:8px;font-weight:500;white-space:nowrap}
.btn .ic{color:var(--icon)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)} .btn.primary .ic{color:var(--on-accent)}
.btn.tertiary{background:transparent;border-color:transparent;color:var(--accent-text)} .btn.tertiary .ic{color:var(--accent-text)}
.btn.danger{background:var(--error-solid);border-color:var(--error-solid);color:#fff} .btn.danger .ic{color:#fff}
.btn.dtert{background:transparent;border-color:transparent;color:var(--error-text)} .btn.dtert .ic{color:var(--error-text)}
.btn.icon{width:30px;padding:0}
.btn.dis{color:var(--text3);background:transparent} .btn.dis .ic{color:var(--text3)}
.menu{position:absolute;background:var(--elev);border-radius:8px;box-shadow:var(--sh-pop);padding:4px;min-width:230px}
.mi{height:28px;display:flex;align-items:center;gap:10px;padding:0 10px;border-radius:5px}
.mi .ic{color:var(--icon)} .mi .k{margin-left:auto;color:var(--text2);font-size:11px}
.mi.hl{background:var(--accent-tint)} .mi.dis{color:var(--text3)} .mi.dis .ic{color:var(--text3)}
.mi.dg,.mi.dg .ic{color:var(--error-text)}
.sep{height:1px;background:var(--border);margin:4px 6px}
.opt{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface2);margin-bottom:8px}
.opt.sel{border-color:var(--accent);background:var(--accent-tint)}
.opt .ico{width:36px;height:36px;border-radius:8px;display:grid;place-items:center;background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent-text);flex:none}
.opt b{display:block;font-weight:600;font-size:13px;line-height:18px} .opt small{display:block;color:var(--text2);font-size:11.5px;line-height:16px}
.opt .go{margin-left:auto;color:var(--text2)}
.radio{width:16px;height:16px;border-radius:50%;border:1.5px solid var(--border-strong);display:grid;place-items:center;flex:none}
.radio.on{border-color:var(--accent)} .radio.on::after{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent)}
.banner{display:flex;gap:10px;padding:10px 12px;border-radius:8px;border:1px solid currentColor;background:color-mix(in srgb,currentColor 9%,transparent);margin:8px 0}
.banner .tx{color:var(--text)} .banner b{display:block;font-weight:600}
.steps{display:flex;align-items:center;gap:8px;color:var(--text2);font-size:11.5px;margin:6px 0 12px}
.steps .s{display:flex;align-items:center;gap:6px}
.steps .n{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--border-strong);display:grid;place-items:center;font-size:10.5px;font-weight:700}
.steps .s.cur{color:var(--text);font-weight:600} .steps .s.cur .n{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.steps .s.done .n{background:var(--success);border-color:var(--success);color:#0b1a10}
.steps .ln{flex:1;height:1px;background:var(--border)}
.bigprog{height:8px;border-radius:4px;background:color-mix(in srgb,var(--text) 11%,transparent);overflow:hidden;margin:10px 0 6px}
.bigprog i{display:block;height:100%;background:var(--info);border-radius:4px}
.kv{display:grid;grid-template-columns:130px 1fr;row-gap:7px;column-gap:12px}
.kv .k{color:var(--text2)} .kv .v{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);padding:0 12px}
.tab{padding:8px 12px;color:var(--text2);font-weight:500}
.tab.on{color:var(--text);font-weight:600;box-shadow:inset 0 -2px 0 var(--accent)}
.code{background:var(--surface2);border:1px solid var(--border);border-radius:7px;padding:10px 12px;color:var(--text);word-break:break-all;line-height:17px;font-size:11px}
.log{background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:10px 12px;line-height:18px;font-size:11px;color:var(--text2)}
.log b{color:var(--text);font-weight:400}
.toggle{width:34px;height:20px;border-radius:10px;background:var(--border-strong);position:relative;flex:none}
.toggle.on{background:var(--accent)} .toggle::after{content:"";position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:#fff}
.toggle.on::after{left:17px}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:7px;overflow:hidden}
.seg span{padding:0 14px;height:28px;line-height:28px;color:var(--text2)} .seg span.on{background:var(--accent-tint);color:var(--accent-text);font-weight:600}
.thumb{width:176px;height:99px;border-radius:8px;background:linear-gradient(135deg,#3b3f8f,#6C5DF6 55%,#468BFF);position:relative;flex:none;display:grid;place-items:center;color:#fff}
.thumb small{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.65);padding:0 5px;border-radius:4px;font-size:10.5px}
.empty{margin:auto;text-align:center;color:var(--text2);padding-bottom:80px}
.empty .ring{width:56px;height:56px;border-radius:50%;background:var(--accent-tint);color:var(--accent-text);display:grid;place-items:center;margin:0 auto 12px}
.empty b{display:block;color:var(--text);font-size:14px;line-height:20px;margin-bottom:4px}
.acts{display:flex;justify-content:flex-end;gap:2px}
.ab{width:24px;height:24px;display:grid;place-items:center;border-radius:5px;color:var(--icon)}
.tr.hover .ab,.tr.sel .ab{background:color-mix(in srgb,var(--text) 9%,transparent)}
.held{display:inline-flex;align-items:center;gap:4px;height:18px;padding:0 6px;margin-left:8px;border-radius:9px;font-size:10.5px;font-weight:600;color:var(--text2);border:1px solid var(--border-strong)}
.held .ic{width:11px;height:11px}
.btn.xs{height:22px;padding:0 8px;font-size:11px;border-radius:5px}
.pri{display:inline-flex;vertical-align:-2px;margin-right:4px}
.tip{position:absolute;background:var(--text);color:var(--bg);border-radius:6px;padding:6px 9px;font-size:11px;line-height:15px;max-width:250px;box-shadow:var(--sh-pop)}
.band{display:flex;align-items:center;gap:8px;height:28px;padding:0 12px;background:var(--surface2);color:var(--text2);font-size:10.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;border-bottom:1px solid var(--border)}
.band .n{margin-left:auto;font-weight:500;letter-spacing:0;text-transform:none}
.grip{color:var(--text3)}
.kbd{display:inline-block;border:1px solid var(--border-strong);border-radius:4px;padding:0 5px;font-size:10.5px;line-height:16px;color:var(--text2)}
"""

STATE = {
    "downloading": ("arrow-down", "Downloading", "info", "var(--info)"),
    "retrying": ("refresh", "Retrying in 12 s", "info", "var(--info)"),
    "waiting": ("clock", "Waiting", "neutral", "var(--neutral)"),
    "paused": ("pause", "Paused", "warning", "var(--warning)"),
    "completed": ("check", "Completed", "success", "var(--success)"),
    "failed": ("x", "Failed", "error", "var(--error)"),
    "cancelled": ("x-circle", "Cancelled", "neutral", "var(--neutral)"),
}
# Queue-level attributes (independent of task state): priority band and hold.
PRIO = {"Fedora-Workstation-Live-x86_64-41.iso": "high", "video.mp4": "high", "backup-2026-09.tar.zst": "low", "movie.mkv": "low"}
HELD = {"archive.zip"}
EXT_ICON = {"ISO": "box", "MP4": "video", "MKV": "video", "WEBM": "video", "ZIP": "archive", "ZST": "archive", "XZ": "archive",
            "PDF": "doc", "PPTX": "doc", "FLAC": "music", "MP3": "music", "PNG": "image", "BIN": "box", "APPIMAGE": "box"}

# name, source, ext, size, pct, state, speed, eta, category, added
ROWS = [
    ("Fedora-Workstation-Live-x86_64-41.iso", "mirror.fedoraproject.org", "ISO", "2.1 GB", 38, "downloading", "31.2 MB/s", "00:41", "Other", "Today 09:12"),
    ("video.mp4", "x.com", "MP4", "84.2 MB", 63, "downloading", "12.4 MB/s", "00:03", "Video", "Today 09:14"),
    ("lecture-03-networking.webm", "youtube.com", "WEBM", "312 MB", 17, "downloading", "9.8 MB/s", "00:26", "Video", "Today 09:15"),
    ("archive.zip", "files.example.org", "ZIP", "620 MB", 0, "waiting", "—", "—", "Archives", "Today 09:15"),
    ("backup-2026-09.tar.zst", "nas.local", "ZST", "4.7 GB", 0, "waiting", "—", "—", "Archives", "Today 09:16"),
    ("movie.mkv", "example.org", "MKV", "1.8 GB", 40, "paused", "—", "—", "Video", "Yesterday"),
    ("setup.AppImage", "releases.example.dev", "APPIMAGE", "148 MB", 71, "retrying", "—", "—", "Other", "Yesterday"),
    ("broken.bin", "bad.example", "BIN", "220 MB", 22, "failed", "—", "—", "Other", "Yesterday"),
    ("old-installer.exe", "download.example.com", "BIN", "76 MB", 12, "cancelled", "—", "—", "Other", "Yesterday"),
    ("ubuntu-24.04.1-desktop-amd64.iso", "releases.ubuntu.com", "ISO", "5.9 GB", 100, "completed", "—", "—", "Other", "Mon"),
    ("document.pdf", "docs.example", "PDF", "12.4 MB", 100, "completed", "—", "—", "Documents", "Mon"),
    ("music.flac", "audio.example", "FLAC", "87.8 MB", 100, "completed", "—", "—", "Music", "Mon"),
    ("holiday.mp4", "cdn.example.net", "MP4", "198 MB", 100, "completed", "—", "—", "Video", "Sun"),
    ("album-cover.png", "images.example", "PNG", "3.2 MB", 100, "completed", "—", "—", "Images", "Sun"),
    ("slides.pptx", "docs.example", "PPTX", "18.6 MB", 100, "completed", "—", "—", "Documents", "Sat"),
]

MORE = [
    ("Kdenlive-24.08-x86_64.AppImage", "download.kde.example", "APPIMAGE", "121 MB", 100, "completed", "—", "—", "Other", "Sat"),
    ("podcast-ep42.mp3", "cdn.podcast.example", "MP3", "64.1 MB", 100, "completed", "—", "—", "Music", "Fri"),
    ("photos-2026-08.zip", "share.example.net", "ZIP", "1.3 GB", 100, "completed", "—", "—", "Archives", "Fri"),
    ("tutorial-rust-ownership.mp4", "youtube.com", "MP4", "226 MB", 100, "completed", "—", "—", "Video", "Fri"),
    ("thesis-final.pdf", "univ.example.edu", "PDF", "9.8 MB", 100, "completed", "—", "—", "Documents", "Thu"),
    ("wallpapers.zip", "images.example", "ZIP", "412 MB", 100, "completed", "—", "—", "Archives", "Thu"),
    ("concert-2025.flac", "audio.example", "FLAC", "512 MB", 100, "completed", "—", "—", "Music", "Wed"),
    ("nvidia-driver.run", "download.example.com", "BIN", "412 MB", 100, "completed", "—", "—", "Other", "Wed"),
    ("interview.mkv", "example.org", "MKV", "2.4 GB", 100, "completed", "—", "—", "Video", "Tue"),
    ("invoice-09.pdf", "docs.example", "PDF", "0.4 MB", 100, "completed", "—", "—", "Documents", "Tue"),
    ("linux-6.11.tar.xz", "cdn.kernel.example", "XZ", "138 MB", 100, "completed", "—", "—", "Archives", "Mon"),
    ("cat.png", "images.example", "PNG", "1.1 MB", 100, "completed", "—", "—", "Images", "Mon"),
    ("keynote.pptx", "docs.example", "PPTX", "44.0 MB", 100, "completed", "—", "—", "Documents", "Sun"),
    ("live-set.mp3", "audio.example", "MP3", "148 MB", 100, "completed", "—", "—", "Music", "Sun"),
    ("game-demo.zip", "files.example.org", "ZIP", "3.4 GB", 100, "completed", "—", "—", "Archives", "Sat"),
    ("screencast.webm", "cdn.example.net", "WEBM", "73.9 MB", 100, "completed", "—", "—", "Video", "Sat"),
]

COLSETS = {
    "wide": ("check|24px name|minmax(280px,1fr) cat|90px type|56px size|76px prog|190px stat|164px speed|84px eta|70px added|100px act|84px",),
    "normal": ("check|24px name|minmax(240px,1fr) type|56px size|76px prog|190px stat|164px speed|84px eta|70px act|84px",),
    "compact": ("check|24px name|minmax(200px,1fr) size|72px prog|150px stat|140px speed|96px act|84px",),
}
HEAD = {"check": "", "name": "Name", "cat": "Category", "type": "Type", "size": "Size", "prog": "Progress", "stat": "Status",
        "speed": "Speed", "eta": "Remaining", "added": "Added", "act": ""}


def colset_for(width):
    return "wide" if width >= 2200 else "normal" if width >= 1280 else "compact"


def cols_template(cs):
    parts = [p.split("|") for p in COLSETS[cs][0].split()]
    return [k for k, _ in parts], " ".join(v for _, v in parts)


def state_badge(state):
    g, label, cls, _ = STATE[state]
    return f'<span class="badge c-{cls}">{icon(g, 14)}{label}</span>'


def prio_mark(name):
    p = PRIO.get(name)
    if p == "high":
        return f'<span class="pri c-accent" title="High priority">{icon("arrow-up", 11, stroke=2.4)}</span>'
    if p == "low":
        return f'<span class="pri t2" title="Low priority">{icon("arrow-down", 11, stroke=2.4)}</span>'
    return ""


def row_actions(state, held, name):
    ab = lambda ic: f'<span class="ab">{icon(ic, 15)}</span>'
    more = ab("more")
    if state == "downloading":
        return ab("pause") + ab("x-circle") + more
    if state == "retrying":
        return ab("refresh") + ab("x-circle") + more
    if state == "paused":
        return ab("play") + ab("x-circle") + more
    if state == "waiting":
        return ab("unlock" if held else "lock") + ab("x-circle") + more
    if state == "completed":
        return ab("folder") + ab("send") + more
    return more


def row(r, keys, sel=False, hover=False, checked=False):
    name, src, ext, size, pct, state, speed, eta, cat, added = r
    ic = icon(EXT_ICON.get(ext, "box"), 16)
    _, _, _, fill = STATE[state]
    held = name in HELD
    held_chip = f'<span class="held">{icon("lock", 11)}Held</span>' if held else ""
    show_acts = hover or (sel and not checked)
    acts = row_actions(state, held, name) if show_acts else f'<span class="ab" style="opacity:.75">{icon("more", 15)}</span>'
    if state == "retrying" and "eta" in keys:
        speed_cell = f'<div class="cell"><span class="btn xs tertiary" style="padding:0 6px;margin-left:-6px">{icon("refresh", 12)}Retry now</span></div>'
    elif "eta" in keys:
        speed_cell = f'<div class="cell">{speed}</div>'
    else:
        speed_cell = f'<div class="cell">{speed if speed == "—" else speed + " · " + eta}</div>'
    cells = {
        "check": f'<div class="check {"on" if checked else ""}">{icon("check", 11, stroke=3) if checked else ""}</div>',
        "name": f'<div class="name">{ic}<div class="t"><b>{name}</b><small>{prio_mark(name)}{src}</small></div></div>',
        "cat": f'<div class="cell t2">{cat}</div>',
        "type": f'<div class="cell t2">{ext if ext != "APPIMAGE" else "BIN"}</div>',
        "size": f'<div class="cell">{size}</div>',
        "prog": (f'<div class="prog"><div class="bar"><i style="width:{pct}%;background:{fill}"></i></div><span class="pct">{pct}%</span></div>' if state != "waiting" else '<div class="prog"><div class="bar"></div><span class="pct">—</span></div>'),
        "stat": f'<div class="cell">{state_badge(state)}{held_chip}</div>',
        "speed": speed_cell,
        "eta": f'<div class="cell t2">{eta}</div>',
        "added": f'<div class="cell t2">{added}</div>',
        "act": f'<div class="acts">{acts}</div>',
    }
    cls = "tr" + (" sel" if sel else "") + (" hover" if hover else "")
    return f'<div class="{cls}">' + "".join(cells[k] for k in keys) + "</div>"


def table(width, rows, selected=(), hover=None, checked=()):
    cs = colset_for(width)
    keys, tpl = cols_template(cs)
    head = "".join(f"<div>{HEAD[k]}</div>" for k in keys)
    body = "".join(row(r, keys, sel=i in selected, hover=(i == hover), checked=i in checked) for i, r in enumerate(rows))
    return f'<div class="table" style="--cols:{tpl}"><div class="thead">{head}</div>{body}</div>'


def sidebar(active="All", counts=None, collapsed=False):
    counts = counts or {"All": 15, "Downloading": 3, "Waiting": 2, "Paused": 1, "Completed": 6, "Failed": 1}
    def item(label, ic, n=None):
        on = " active" if label == active else ""
        num = f'<span class="n">{n}</span>' if n is not None else ""
        return f'<div class="sb-item{on}">{icon(ic, 16)}<span>{label}</span>{num}</div>'
    h = '<div class="sb-title">Downloads</div>'
    for l, i in [("All", "download"), ("Downloading", "arrow-down"), ("Waiting", "clock"), ("Paused", "pause"), ("Completed", "check-circle"), ("Failed", "x-circle")]:
        h += item(l, i, counts.get(l))
    h += '<div class="sb-title">Categories</div>'
    for l, i, n in [("Video", "video", 5), ("Music", "music", 1), ("Images", "image", 1), ("Documents", "doc", 2), ("Archives", "archive", 2), ("Other", "box", 4)]:
        h += item(l, i, n if counts.get("All") else 0)
    h += '<div class="sb-title">Tools</div>'
    for l, i in [("Queues", "list"), ("Scheduler", "calendar"), ("Devices", "phone"), ("Settings", "gear")]:
        h += item(l, i)
    return f'<aside class="sidebar">{h}</aside>'


def toolbar(compact=False):
    def b(ic, label, cls=""):
        return f'<div class="tb {cls}">{icon(ic, 18)}<span>{label}</span></div>'
    return ('<div class="toolbar">' + b("plus", "Add", "primary") + '<div class="tsep"></div>' + b("play", "Start Queue") +
            b("stop", "Stop Queue") + '<div class="tsep"></div>' + b("list", "Queues") + b("gear", "Settings") + "</div>")


def statusbar(active=2, waiting=3, speed="43.6 MB/s", idle=False):
    if idle:
        return f'<div class="statusbar"><span>{icon("dot-hollow", 10)} Idle</span><span class="r">Limit: Unlimited {icon("chev-down", 12)}</span></div>'
    return (f'<div class="statusbar"><span class="c-info">{icon("dot", 10)}</span><span>{active} active</span><span>{waiting} waiting</span>'
            f'<span>{icon("arrow-down", 12)} {speed}</span><span class="r">Limit: Unlimited {icon("chev-down", 12)}</span></div>')


def filterbar(count="14 tasks", status="All", sort="Newest", focus_search=False):
    return (f'<div class="filterbar"><div class="field grow{" focus" if focus_search else ""}" style="max-width:340px">{icon("search", 16)}<span>Search downloads…</span></div>'
            f'<div class="field text">Status: {status} {icon("chev-down", 14)}</div><div class="field text">Sort: {sort} {icon("chev-down", 14)}</div>'
            f'<span class="count">{count}</span></div>')


def bulkbar(n, send_enabled=False, held_any=True):
    d = "" if send_enabled else " dis"
    rel = "" if held_any else " dis"
    return (f'<div class="bulk"><b>{n} selected</b><div class="btn">{icon("pause", 14)}Pause</div><div class="btn">{icon("play", 14)}Resume</div>'
            f'<div class="btn">{icon("lock", 14)}Hold</div><div class="btn{rel}">{icon("unlock", 14)}Release</div>'
            f'<div class="btn">{icon("arrow-up", 14)}Priority {icon("chev-down", 12)}</div><div class="btn">{icon("x-circle", 14)}Cancel</div>'
            f'<div class="tsep" style="margin:0 4px"></div><div class="btn{d}">{icon("send", 14)}Send…</div>'
            f'<div class="btn dtert">{icon("trash", 14)}Delete</div><div class="btn clr">{icon("x", 14)}Clear selection</div></div>')


def main_content(width, title="All Downloads", rows=ROWS, height=768, selected=(), hover=None, bulk=None, checked=(), status="All", hint=True, count=None):
    head = f'<div class="pagehead"><h1>{title}</h1></div>'
    top = bulkbar(bulk[0], bulk[1]) if bulk else filterbar(count or f"{len(rows)} tasks", status=status)
    hint_html = '<div class="hint">Double-click opens details · Right-click shows actions · <span class="kbd">Ctrl</span>+<span class="kbd">N</span> adds a download</div>' if hint else ""
    scroll = " scroll" if len(rows) * 42 + 30 > height - 82 - 28 - 130 else ""
    return f'<main class="content{scroll}">{head}{top}{table(width, rows, selected, hover, checked)}{hint_html}</main>'


def window(theme, w, h, inner, overlay="", title=None):
    return (f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"><!-- NON-PRODUCTION DESIGN PROTOTYPE -->'
            f'<style>{base_css()}{CSS}</style></head><body class="desk"><div class="win" style="width:{w}px;height:{h}px">'
            f'<div class="menubar"><span>File</span><span>Tasks</span><span>Tools</span><span>Help</span></div>{toolbar()}'
            f'{inner}</div>{overlay}</body></html>')


def main_window(theme, w, h, view="All", overlay="", **kw):
    rows = kw.pop("rows", ROWS)
    counts = kw.pop("counts", None)
    stat = kw.pop("stat", {})
    inner = f'<div class="body">{sidebar(view, counts)}{main_content(w, rows=rows, height=h, **kw)}</div>{statusbar(**stat)}'
    return window(theme, w, h, inner, overlay)


def dialog(inner, width, top=None, extra=""):
    pos = f"top:{top}px;transform:translate(-50%,0);" if top else ""
    return f'<div class="scrim"></div><div class="dlg" style="width:{width}px;{pos}{extra}">{inner}</div>'


def dh(title, ic=None, ic_cls=""):
    i = f'<span class="{ic_cls}">{icon(ic, 18)}</span>' if ic else ""
    return f'<div class="dlg-h">{i}<span>{title}</span><span class="x">{icon("x", 16)}</span></div>'


def btn(label, cls="", ic=None):
    i = icon(ic, 14) if ic else ""
    return f'<div class="btn {cls}">{i}{label}</div>'


def field(text, ic=None, cls="text", extra=""):
    i = icon(ic, 16) if ic else ""
    return f'<div class="field {cls}" style="{extra}">{i}<span style="overflow:hidden;text-overflow:ellipsis">{text}</span></div>'


# ---- dialogs --------------------------------------------------------------
def dlg_add():
    b = (f'<div class="lbl">URL</div><div style="display:flex;gap:8px">{field("https://mirror.fedoraproject.org/pub/fedora/linux/releases/41/Workstation/x86_64/iso/Fedora-Workstation-Live-x86_64-41.iso", "link", extra="flex:1;min-width:0")}{btn("", "icon", "copy")}</div>'
         f'<div class="t2" style="margin-top:6px;font-size:11.5px">{icon("check-circle", 13, "c-success")} ISO image · 2.1 GB · resumable</div>'
         f'<div class="lbl">Save to</div><div style="display:flex;gap:8px">{field("~/Downloads", "folder", extra="flex:1")}{btn("Browse…")}</div>'
         f'<div class="t2" style="margin:12px 0 4px;font-size:11.5px">Type and compatibility are detected automatically.</div>'
         f'<div class="t2" style="display:flex;align-items:center;gap:6px;margin-top:8px">{icon("chev-right", 14)}<span>Advanced</span></div>')
    f = f'<div class="dlg-f">{btn("Cancel")}{btn("Download", "primary", "download")}</div>'
    return dialog(dh("Add download") + f'<div class="dlg-b">{b}</div>' + f, 560)


def dlg_media():
    top = (f'<div style="display:flex;gap:14px;margin-top:6px"><div class="thumb">{icon("play", 26)}<small>08:12</small></div>'
           f'<div style="min-width:0"><b style="font-size:13px;line-height:18px;display:block">Blender 4.2 LTS - Release Highlights</b>'
           f'<div class="t2" style="margin-top:4px">youtube.com · Blender Studio</div>'
           f'<div class="t2" style="margin-top:8px;font-size:11.5px">{icon("check-circle", 13, "c-success")} Detected by the media resolver</div></div></div>')
    sel = lambda t: f'<div class="field text" style="justify-content:space-between">{t}{icon("chev-down", 14)}</div>'
    b = (top + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
         f'<div><div class="lbl">Video quality</div>{sel("Best available (1080p MP4)")}</div>'
         f'<div><div class="lbl">Audio track</div>{sel("Original")}</div></div>'
         f'<div class="lbl">Save to</div><div style="display:flex;gap:8px">{field("~/Downloads", "folder", extra="flex:1")}{btn("Browse…")}</div>'
         '<div class="t2" style="margin-top:10px;font-size:11.5px">Choices are automatic. Change them only if you need to.</div>')
    f = f'<div class="dlg-f">{btn("Cancel")}{btn("Download", "primary", "download")}</div>'
    return dialog(dh("Add download") + f'<div class="dlg-b">{b}</div>' + f, 600)


def dlg_details(tab="Information"):
    tabs = "".join(f'<div class="tab{" on" if t == tab else ""}">{t}</div>' for t in ["Information", "Connections", "Log"])
    if tab == "Information":
        kv = [("Name", "video.mp4"), ("Status", state_badge("downloading") + " · 63%"), ("URL", "https://x.com/i/status/1834…/video"),
              ("Effective URL", "https://video.twimg.example/vid/avc1/1280x720/video.mp4"), ("Size", "84.2 MB"),
              ("Downloaded", "53.0 MB"), ("Speed", "12.4 MB/s"), ("Remaining", "00:03"), ("Priority", "High"), ("Queue position", "2 of 2 in High"), ("Held", "No"), ("Destination", "~/Downloads/video.mp4"),
              ("Resume support", "Yes (byte ranges)"), ("Backend", "Direct HTTP"), ("Resolver", "yt-dlp")]
        body = '<div class="kv">' + "".join(f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in kv) + "</div>"
    elif tab == "Connections":
        body = (f'<div class="table" style="--cols:30px 1fr 110px 110px 110px"><div class="thead"><div>#</div><div>Range</div><div>Downloaded</div><div>Speed</div><div>State</div></div>'
                f'<div class="tr"><div class="cell">1</div><div class="cell">0 - 84.2 MB</div><div class="cell">53.0 MB</div><div class="cell">12.4 MB/s</div><div class="cell">{state_badge("downloading")}</div></div></div>'
                '<div class="t2" style="margin-top:10px;font-size:11.5px">This download uses one resumable connection. Additional connections are listed here when a download is split into segments.</div>')
    else:
        lines = [("09:14:02", "Resolved media URL (yt-dlp)"), ("09:14:03", "Connected to video.twimg.example (TLS 1.3)"), ("09:14:03", "Server supports byte ranges"),
                 ("09:14:03", "Started transfer, 84.2 MB expected"), ("09:14:11", "Saved progress checkpoint at 24.0 MB"), ("09:14:17", "Saved progress checkpoint at 48.0 MB")]
        body = '<div class="log mono">' + "".join(f'<div><span class="t3">{t}</span>  <b>{m}</b></div>' for t, m in lines) + "</div>"
    hdr = (f'<div class="dlg-h">{icon("video", 18)}<span>video.mp4</span><span class="chip c-info" style="margin-left:6px">{icon("arrow-down", 12)}Downloading</span><span class="x">{icon("x", 16)}</span></div>')
    f = f'<div class="dlg-f"><div class="l" style="display:flex;gap:8px">{btn("Open folder", "", "folder")}{btn("Pause", "", "pause")}</div>{btn("Close", "primary")}</div>'
    return dialog(hdr + f'<div class="tabs" style="margin-top:8px">{tabs}</div><div class="dlg-b" style="padding:14px 16px;height:392px;overflow:hidden">{body}</div>' + f, 660)


def ctx_menu(x, y, kind="completed", priority_sub=False, hl=None, current="normal"):
    """kind: downloading | retrying | paused | waiting | held | failed | cancelled | completed.
    Only actions valid for the state are shown; Move up/down disable at a band edge."""
    it = lambda ic, l, k="", c="": f'<div class="mi {c}{" hl" if hl == l else ""}">{icon(ic, 15)}<span>{l}</span><span class="k">{k}</span></div>'
    sep = '<div class="sep"></div>'
    prio = f'<div class="mi{" hl" if priority_sub else ""}">{icon("arrow-up", 15)}<span>Priority</span><span class="k">{icon("chev-right", 14)}</span></div>'
    order = prio + it("arrow-up", "Move up") + it("arrow-down", "Move down", "", "dis" if kind == "waiting" else "")
    cancel = it("x-circle", "Cancel", "", "dg")
    if kind == "downloading":
        m = it("pause", "Pause", "Space") + it("lock", "Hold") + sep + order + sep + cancel + sep + it("folder", "Open folder") + it("info", "Details", "Alt+Enter")
    elif kind == "retrying":
        m = it("refresh", "Retry now") + it("lock", "Hold") + sep + order + sep + cancel + sep + it("folder", "Open folder") + it("info", "Details", "Alt+Enter")
    elif kind == "paused":
        m = it("play", "Resume", "Space") + it("lock", "Hold") + sep + order + sep + cancel + sep + it("folder", "Open folder") + it("info", "Details", "Alt+Enter")
    elif kind == "waiting":
        m = it("lock", "Hold") + sep + order + sep + cancel + sep + it("info", "Details", "Alt+Enter")
    elif kind == "held":
        m = it("unlock", "Release") + sep + order + sep + cancel + sep + it("info", "Details", "Alt+Enter")
    elif kind in ("failed", "cancelled"):
        m = it("info", "Details", "Alt+Enter") + it("folder", "Open folder") + sep + it("x", "Remove from list", "Del") + it("trash", "Delete file…", "", "dg")
    else:
        m = (it("external", "Open", "Enter") + it("folder", "Open folder") + it("info", "Details", "Alt+Enter") + sep + it("send", "Share…", "", "hl") + sep +
             it("copy", "Copy source URL") + it("x", "Remove from list", "Del") + it("trash", "Delete file…", "", "dg"))
    out = f'<div class="menu" style="left:{x}px;top:{y}px">{m}</div>'
    if priority_sub:
        chk = lambda lab, key: f'<div class="mi">{icon("check", 15) if current == key else "<span style=width:15px></span>"}<span>{lab}</span></div>'
        sub = chk("High", "high") + chk("Normal", "normal") + chk("Low", "low") + '<div class="sep"></div><div class="t2" style="padding:2px 10px 4px;font-size:10.5px;line-height:14px;max-width:170px">Order changes apply within the same priority.</div>'
        out += f'<div class="menu" style="left:{x + 236}px;top:{y + 108}px;min-width:180px">{sub}</div>'
    return out


def dlg_share():
    o = lambda ic, t, d, sel="": f'<div class="opt {sel}"><div class="ico">{icon(ic, 20)}</div><div><b>{t}</b><small>{d}</small></div><span class="go">{icon("chev-right", 16)}</span></div>'
    b = (f'<div class="t2" style="margin:2px 0 12px;display:flex;align-items:center;gap:8px">{icon("video", 16)}<span>holiday.mp4 · 198 MB</span></div>' +
         o("phone", "Send to device", "Direct transfer to your phone with FriendSend.") +
         o("link", "Share by link", "Anyone with the link can open it while this PC is online."))
    return dialog(dh('Share "holiday.mp4"') + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Cancel")}</div>', 480)


def dev_row(name, ic, sub, sel=False, dis=False, chip=""):
    st = ' style="opacity:.55"' if dis else ""
    return (f'<div class="opt {"sel" if sel else ""}" {st}><div class="radio {"on" if sel else ""}"></div><div class="ico">{icon(ic, 20)}</div>'
            f'<div><b>{name}</b><small>{sub}</small></div><span style="margin-left:auto">{chip}</span></div>')


ONLINE = f'<span class="chip c-success">{icon("dot", 10)}Trusted · Online</span>'
OFFLINE = f'<span class="chip c-neutral">{icon("dot-hollow", 10)}Trusted · Offline</span>'
CHANGED = f'<span class="chip c-error">{icon("shield-alert", 12)}Identity changed</span>'
PAIRING = f'<span class="chip c-info">{icon("refresh", 12)}Pairing…</span>'
UNPAIRED = f'<span class="chip c-neutral">{icon("plus-circle", 12)}Not paired</span>'


def dlg_send(offline=False):
    file = f'<div class="t2" style="margin:2px 0 10px;display:flex;align-items:center;gap:8px">{icon("video", 16)}<span>holiday.mp4 · 198 MB</span></div>'
    d1 = dev_row("Pixel 8", "phone", "Last seen just now", sel=not offline, chip=ONLINE)
    d2 = dev_row("Galaxy Tab A9", "tablet", "Last seen yesterday, 18:42", sel=offline, dis=not offline, chip=OFFLINE)
    ban = ""
    if offline:
        ban = (f'<div class="banner c-warning">{icon("warning", 18)}<div class="tx"><b>Galaxy Tab A9 is offline</b>'
               'Open FriendSend on the tablet and connect it to the same Wi-Fi as this PC.</div></div>')
    note = '<div class="t2" style="font-size:11.5px;margin-top:6px">The file is checked for compatibility and converted only if needed. The original is never changed.</div>'
    link = f'<div style="margin-top:10px">{btn("Pair a new FriendSend…", "tertiary", "plus")}</div>'
    send = btn("Send", "primary dis" if offline else "primary", "send")
    retry = btn("Retry", "tertiary", "refresh") if offline else ""
    return dialog(dh("Send to device") + f'<div class="dlg-b">{file}{d1}{d2}{ban}{note}{link}</div><div class="dlg-f"><div class="l">{retry}</div>{btn("Cancel")}{send}</div>', 500)


def steps(cur):
    names = ["Prepare", "Send", "Done"]
    out = []
    for i, n in enumerate(names):
        cls = "done" if i < cur else "cur" if i == cur else ""
        mark = icon("check", 11, stroke=3) if i < cur else str(i + 1)
        out.append(f'<div class="s {cls}"><span class="n">{mark}</span>{n}</div>')
    return '<div class="steps">' + '<div class="ln"></div>'.join(out) + "</div>"


def dlg_progress(kind):
    title = "Sending to Pixel 8"
    if kind == "preparing":
        body = (steps(0) + '<b style="font-size:13px">Preparing compatible copy…</b><div class="bigprog"><i style="width:43%"></i></div>'
                '<div style="display:flex;justify-content:space-between" class="t2"><span>Optimizing container…</span><span>43%</span></div>'
                f'<div class="banner c-info" style="margin-top:14px">{icon("shield-check", 18)}<div class="tx">The original file will not be changed.</div></div>')
    elif kind == "transcoding":
        body = (steps(0) + '<b style="font-size:13px">Converting video for Pixel 8…</b><div class="bigprog"><i style="width:43%"></i></div>'
                '<div style="display:flex;justify-content:space-between" class="t2"><span>Converted 1:12 of 2:48</span><span>43%</span></div>'
                f'<div class="banner c-info" style="margin-top:14px">{icon("shield-check", 18)}<div class="tx">The original file will not be changed. This can take a few minutes; you can keep using Rýchlik.</div></div>')
    else:
        body = (steps(1) + '<b style="font-size:13px">Sending to Pixel 8…</b><div class="bigprog"><i style="width:62%"></i></div>'
                '<div style="display:flex;justify-content:space-between" class="t2"><span>122.8 MB of 198 MB · 18.4 MB/s</span><span>62%</span></div>'
                f'<div class="t2" style="margin-top:14px;display:flex;gap:8px;align-items:center">{icon("info", 15)}Keep FriendSend open on the device.</div>')
    return dialog(dh(title, "send") + f'<div class="dlg-b">{body}</div><div class="dlg-f">{btn("Cancel")}</div>', 480)


def dlg_result(kind):
    if kind == "received":
        head = f'<div style="text-align:center;padding:10px 8px 4px"><div class="ring" style="width:48px;height:48px;border-radius:50%;background:color-mix(in srgb,var(--success) 16%,transparent);display:grid;place-items:center;margin:0 auto 10px" ><span class="c-success">{icon("check-circle", 28)}</span></div><b style="font-size:14px;line-height:20px">Received by Pixel 8</b></div>'
        b = ('<div class="t2" style="text-align:center;line-height:18px">The file arrived and was verified.<br>Choose an app on the phone to continue sharing.<br>'
             'Delivery to other people happens in the app you choose.</div>')
        f = f'<div class="dlg-f">{btn("Send again", "tertiary")}{btn("Done", "primary")}</div>'
        return dialog(steps(2).replace("steps", "steps") and (head + f'<div class="dlg-b">{b}</div>' + f), 480)
    b = (f'<div class="banner c-error">{icon("x-circle", 20)}<div class="tx"><b>Couldn\'t send the file</b>The connection to Pixel 8 was interrupted. Nothing was saved on the phone.</div></div>'
         f'<div class="t2" style="display:flex;align-items:center;gap:6px;margin-top:8px">{icon("chev-down", 14)}<span>Details</span></div>'
         '<div class="log mono" style="margin-top:8px">Stage: Sending (61%)<br>Reason: connection lost<br>Code: CONNECTION_FAILED</div>')
    return dialog(dh("Send to Pixel 8", "send") + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Close")}{btn("Try again", "primary", "refresh")}</div>', 500)


def dlg_pair(success=False):
    if success:
        b = (f'<div style="text-align:center;padding:12px 0"><span class="c-success">{icon("check-circle", 40)}</span><div style="font-size:14px;font-weight:600;margin:8px 0 2px">Paired with Pixel 8</div>'
             '<div class="t2">You can now send files to it from any completed download.</div></div>')
        return dialog(dh("Pair FriendSend") + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Done", "primary")}</div>', 520)
    st = lambda n, t: f'<div style="display:flex;gap:10px;margin:8px 0"><span class="n" style="width:20px;height:20px;border-radius:50%;background:var(--accent-tint);color:var(--accent-text);display:grid;place-items:center;font-weight:700;font-size:11px;flex:none">{n}</span><div>{t}</div></div>'
    b = (st(1, "Open <b>FriendSend</b> on your phone. Connect it to the same Wi-Fi as this PC.") + st(2, "Copy the pairing code below.") + st(3, "Paste it into FriendSend and tap <b>Pair</b>.") +
         '<div class="code mono" style="margin-top:8px">{"protocol_version":1,"security_profile":"pinned-tls-signature-v1","pairing_session_id":"9c1e…","desktop_endpoint":{"host":"192.168.1.20","port":41873},"secret":"••••••••••••••••", …}</div>'
         f'<div style="display:flex;align-items:center;gap:10px;margin-top:10px">{btn("Copy pairing code", "primary", "copy")}<span class="c-info" style="display:inline-flex;gap:6px;align-items:center">{icon("refresh", 14)}Waiting for FriendSend…</span><span class="t2" style="margin-left:auto">Expires in 4:32</span></div>'
         '<div class="t2" style="font-size:11.5px;margin-top:10px">The code works once. Only pair a phone you own; never share this code.</div>')
    return dialog(dh("Pair FriendSend", "phone") + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Cancel")}</div>', 560)


def dlg_device(kind="details"):
    if kind == "forget":
        b = (f'<div style="display:flex;gap:12px;margin-top:4px"><span class="c-warning">{icon("warning", 22)}</span><div><b style="font-size:13px">Forget Pixel 8?</b>'
             '<div class="t2" style="margin-top:4px;line-height:18px">You will need to pair again before sending files to it. Files already on the phone are not affected.</div></div></div>')
        return dialog(dh("Forget device") + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Cancel")}{btn("Forget device", "danger", "trash")}</div>', 440)
    if kind == "changed":
        b = (f'<div class="banner c-error">{icon("shield-alert", 22)}<div class="tx"><b>Device identity changed</b>The saved security identity no longer matches this device.</div></div>'
             '<div class="t2" style="line-height:18px;margin-top:6px">If this change is expected, forget the device and pair it again. Files are not sent while the identity does not match.</div>')
        return dialog(dh("Moto G84", "phone") + f'<div class="dlg-b">{b}</div><div class="dlg-f">{btn("Cancel")}{btn("Forget device", "danger", "trash")}</div>', 480)
    kv = [("Status", ONLINE), ("Device ID", '<span class="mono">1004658d…fe21e</span>'), ("Identity", f'<span class="c-success">{icon("shield-check", 14)} Verified and pinned</span>'),
          ("Paired", "28 Sep 2026, 08:20"), ("Last seen", "Just now"), ("Connection", "Wi-Fi · 192.168.1.42")]
    b = '<div class="kv" style="margin-top:6px">' + "".join(f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in kv) + "</div>"
    return dialog(dh("Pixel 8", "phone") + f'<div class="dlg-b">{b}</div><div class="dlg-f"><div class="l">{btn("Forget device…", "dtert", "trash")}</div>{btn("Close", "primary")}</div>', 500)


def dlg_settings():
    cats = ["General", "Downloads", "Queue & speed", "Appearance", "Devices & sharing", "Advanced"]
    side = "".join(f'<div class="sb-item{" active" if c == "Appearance" else ""}"><span>{c}</span></div>' for c in cats)
    row_ = lambda l, c: f'<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border)"><span>{l}</span>{c}</div>'
    body = (f'<b style="font-size:14px">Appearance</b><div style="margin-top:6px">' +
            row_("Theme", '<div class="seg"><span>Light</span><span class="on">Dark</span><span>System</span></div>') +
            row_("Language", f'<div class="field text" style="width:170px;justify-content:space-between">English {icon("chev-down", 14)}</div>') +
            row_("Compact rows", '<div class="toggle on"></div>') + row_("Show desktop notifications", '<div class="toggle on"></div>') + "</div>"
            '<div class="t2" style="margin-top:10px;font-size:11.5px">Interface size follows the system display scaling.</div>')
    inner = (dh("Settings") + f'<div style="display:flex;height:330px"><div style="width:180px;border-right:1px solid var(--border);padding:8px">{side}</div><div style="flex:1;padding:14px 20px">{body}</div></div>'
             f'<div class="dlg-f">{btn("Close", "primary")}</div>')
    return dialog(inner, 720)


def devices_view(w):
    keys = ["name", "stat", "sec", "seen", "act"]
    tpl = "minmax(240px,1fr) 190px 190px 150px 150px"
    def r(nm, ic, sub, chip, sec, seen, act):
        return (f'<div class="tr"><div class="name">{icon(ic, 18)}<div class="t"><b>{nm}</b><small>{sub}</small></div></div><div class="cell">{chip}</div>'
                f'<div class="cell">{sec}</div><div class="cell t2">{seen}</div><div class="cell" style="text-align:right">{act}</div></div>')
    rows_ = (r("Pixel 8", "phone", "FriendSend 1.0", ONLINE, f'<span class="c-success">{icon("shield-check", 14)} Verified</span>', "Just now", btn("Details", ""))
             + r("Galaxy Tab A9", "tablet", "FriendSend 1.0", OFFLINE, f'<span class="c-success">{icon("shield-check", 14)} Verified</span>', "Yesterday, 18:42", btn("Details", ""))
             + r("Moto G84", "phone", "FriendSend 1.0", CHANGED, f'<span class="c-error">{icon("shield-alert", 14)} Does not match</span>', "3 days ago", btn("Resolve…", "danger"))
             + r("Kitchen tablet", "tablet", "Found on this network", UNPAIRED, '<span class="t2">Not verified</span>', "Just now", btn("Pair…", "primary")))
    head = ''.join(f"<div>{h}</div>" for h in ["Device", "Status", "Security", "Last seen", ""])
    return (f'<main class="content"><div class="pagehead"><h1>Devices</h1><div class="sp">{btn("Pair FriendSend…", "primary", "plus")}</div></div>'
            f'<div class="t2" style="margin:2px 0 10px">Phones and tablets running FriendSend. Send is available from any completed download.</div>'
            f'<div class="table" style="--cols:{tpl}"><div class="thead">{head}</div>{rows_}</div>'
            f'<div class="t2" style="margin-top:12px;font-size:11.5px;display:flex;gap:8px">{icon("info", 15)}<span>Finding a device on your network does not make it trusted. Only devices you pair can receive files.</span></div></main>')


def empty_main(theme, w, h):
    inner = (f'<div class="body">{sidebar("All", {"All": 0})}<main class="content"><div class="pagehead"><h1>All Downloads</h1></div>'
             f'<div class="empty"><div class="ring">{icon("download", 26)}</div><b>No downloads yet</b>Paste a link with <span class="kbd">Ctrl</span>+<span class="kbd">V</span> or press <span class="kbd">Ctrl</span>+<span class="kbd">N</span> to add one.'
             f'<div style="margin-top:14px">{btn("Add download", "primary", "plus")}</div></div></main></div>{statusbar(idle=True)}')
    return window(theme, w, h, inner)


def component_sheet(theme, w=1366, h=900):
    sw = lambda label, css: f'<div style="display:flex;align-items:center;gap:8px"><i style="width:26px;height:26px;border-radius:6px;background:{css};border:1px solid var(--border-strong)"></i><span class="t2">{label}</span></div>'
    btns = "".join(btn(l, c) for l, c in [("Primary", "primary"), ("Secondary", ""), ("Tertiary", "tertiary"), ("Destructive", "danger"), ("Delete", "dtert"), ("Disabled", "dis")]) + btn("", "icon", "more")
    badges = "".join(f'<div style="margin:6px 0">{state_badge(s)}</div>' for s in STATE)
    chips = "".join(f'<div style="margin:6px 0">{c}</div>' for c in [ONLINE, OFFLINE, PAIRING, CHANGED, UNPAIRED])
    bars = "".join(f'<div class="prog" style="margin:10px 0;width:240px"><div class="bar"><i style="width:{p}%;background:{STATE[s][3]}"></i></div><span class="pct">{p}%</span></div>' for s, p in [("downloading", 63), ("paused", 40), ("completed", 100), ("failed", 22)])
    sect = lambda t, b: f'<div style="flex:1;min-width:260px"><div class="lbl" style="margin-top:0">{t}</div>{b}</div>'
    swatches = "".join(sw(n, f"var(--{v})") for n, v in [("background", "bg"), ("surface", "surface"), ("surface2", "surface2"), ("elevated", "elev"), ("accent", "accent"), ("info", "info"), ("success", "success"), ("warning", "warning"), ("error", "error")])
    inner = (f'<div style="padding:24px 28px;display:flex;flex-direction:column;gap:22px"><div style="font-size:15px;font-weight:600">Rýchlik Desktop - component sheet ({theme})</div>'
             f'<div style="display:flex;gap:28px">{sect("Buttons", f"<div style=\"display:flex;gap:8px;flex-wrap:wrap\">{btns}</div><div class=\"lbl\">Focus ring</div><div style=\"display:flex;gap:10px\">{btn("Focused", "focus")}<div class=\"field focus\" style=\"width:160px\">Search…</div></div>")}'
             f'{sect("Fields / controls", field("Search downloads…", "search", "grow") + "<div style=\"margin:10px 0;display:flex;gap:14px;align-items:center\"><div class=toggle></div><div class=\"toggle on\"></div><div class=seg><span>Light</span><span class=on>Dark</span></div><div class=check></div><div class=\"check on\">" + icon("check", 11, stroke=3) + "</div></div>")}</div>'
             f'<div style="display:flex;gap:28px">{sect("Download status", badges)}{sect("Device status", chips)}{sect("Progress (6 px)", bars)}{sect("Palette", "<div style=display:grid;gap:8px>" + swatches + "</div>")}</div>'
             f'<div style="display:flex;gap:28px">{sect("Table rows", "<div style=width:600px>" + table(1300, ROWS[:1], selected=(0,)).replace("--cols:", "--cols:") + "</div>")}{sect("Context menu (static)", "<div style=\"position:relative;height:120px\">" + ctx_menu(0, 0).replace("position:absolute", "position:relative") + "</div>")}</div></div>')
    return (f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"><!-- NON-PRODUCTION DESIGN PROTOTYPE --><style>{base_css()}{CSS}</style></head>'
            f'<body class="desk"><div class="win" style="width:{w}px;height:{h}px;overflow:auto">{inner}</div></body></html>')


def dlg_link(state="online"):
    chips = {"online": ('<span class="chip c-success">' + icon("dot", 10) + "Online</span>"),
             "offline": ('<span class="chip c-neutral">' + icon("dot-hollow", 10) + "Offline</span>")}
    b = (f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0 4px"><span class="t2">{icon("video", 16)}</span><b>holiday.mp4</b><span class="t2">· 198 MB</span><span style="margin-left:auto">{chips[state]}</span></div>'
         f'<div class="lbl">Link</div><div style="display:flex;gap:8px">{field("https://s.friendsend.example/kQ7xZ2mPaB9r", "link", extra="flex:1;min-width:0")}{btn("Copy link", "primary", "copy")}</div>'
         f'<div style="display:flex;gap:8px;margin-top:12px">{btn("Open in browser", "", "external")}{btn("Show QR code", "", "qr")}{btn("Share via phone", "", "phone")}</div>'
         f'<div class="banner c-info" style="margin-top:14px">{icon("info", 18)}<div class="tx"><b>Available while this PC is online.</b>Anyone with the link can watch or download the video. Stop sharing to close the link.</div></div>')
    f = f'<div class="dlg-f"><div class="l">{btn("Stop sharing", "dtert", "x-circle")}</div>{btn("Close", "primary")}</div>'
    return dialog(dh("Share by link", "link") + f'<div class="dlg-b">{b}</div>' + f, 560)


def queues_view(w):
    byname = {r[0]: r for r in ROWS}
    bands = [("High", ["Fedora-Workstation-Live-x86_64-41.iso", "video.mp4"]),
             ("Normal", ["lecture-03-networking.webm", "archive.zip", "setup.AppImage"]),
             ("Low", ["backup-2026-09.tar.zst", "movie.mkv"])]
    tpl = "22px 34px minmax(240px,1fr) 80px 170px 100px 60px"
    head = "".join(f"<div>{h}</div>" for h in ["", "#", "Name", "Size", "Status", "Held", ""])
    body = ""
    for band, names in bands:
        body += f'<div class="band">{band} priority<span class="n">{len(names)} downloads</span></div>'
        for i, n in enumerate(names, 1):
            r = byname[n]
            sel = n == "archive.zip"
            held = f'<span class="held" style="margin:0">{icon("lock", 11)}Held</span>' if n in HELD else '<span class="t3">—</span>'
            body += (f'<div class="tr{" sel" if sel else ""}"><div class="grip">{icon("grip", 16)}</div><div class="cell t2">{i}</div>'
                     f'<div class="name">{icon(EXT_ICON.get(r[2], "box"), 16)}<div class="t"><b>{n}</b><small>{r[1]}</small></div></div>'
                     f'<div class="cell">{r[3]}</div><div class="cell">{state_badge(r[5])}</div><div class="cell">{held}</div>'
                     f'<div class="acts"><span class="ab">{icon("arrow-up", 15)}</span><span class="ab">{icon("arrow-down", 15)}</span></div></div>')
    bar = (f'<div class="bulk"><b>1 selected</b><div class="btn">{icon("arrow-up", 14)}Move up</div><div class="btn">{icon("arrow-down", 14)}Move down</div>'
           f'<div class="btn">{icon("arrow-up", 14)}Priority {icon("chev-down", 12)}</div><div class="btn dis">{icon("lock", 14)}Hold</div><div class="btn">{icon("unlock", 14)}Release</div></div>')
    return (f'<main class="content"><div class="pagehead"><h1>Queues</h1></div>'
            f'<div class="t2" style="margin:2px 0 0">One download queue with three priority bands. Downloads start from the top of High, then Normal, then Low. Reordering works within a band.</div>'
            f'{bar}<div class="table" style="--cols:{tpl}"><div class="thead">{head}</div>{body}</div>'
            f'<div class="t2" style="margin-top:12px;font-size:11.5px;display:flex;gap:8px">{icon("info", 15)}<span><b>Held</b> keeps a download from starting and does not stop one that is running. <b>Pause</b> stops a running transfer.</span></div></main>')
