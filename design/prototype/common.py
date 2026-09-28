# NON-PRODUCTION DESIGN PROTOTYPE -- not wired into the application.
"""Shared tokens->CSS generation and the single icon language (1.75px rounded
stroke, 24px grid, drawn originally -- no third-party or brand assets)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = json.loads((ROOT / "final_design_tokens.json").read_text())

ICONS = {
    "download": '<path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/>',
    "arrow-down": '<path d="M12 5v13m0 0l-5-5m5 5l5-5"/>',
    "play": '<path d="M8 5l11 7-11 7z" fill="currentColor"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.2-4.2"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "pause": '<path d="M9 6v12M15 6v12"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M21 16l-5-5-8 8"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/>',
    "box": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    "tablet": '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M11 18h2"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "send": '<path d="M21 3L10 14M21 3l-7 18-4-7-7-4z"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "qr": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M14 21h3M21 17v4"/>',
    "more": '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
    "chev-down": '<path d="M6 9l6 6 6-6"/>',
    "chev-right": '<path d="M9 6l6 6-6 6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    "shield-check": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "shield-alert": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M12 8v5M12 16v.01"/>',
    "wifi": '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1" fill="currentColor"/>',
    "wifi-off": '<path d="M2 9a15 15 0 0 1 4-2.6M22 9a15 15 0 0 0-8.5-4.6M5 12.5a10 10 0 0 1 3-2M19 12.5a10 10 0 0 0-3-2M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1" fill="currentColor"/><path d="M3 3l18 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    "warning": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.01"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2 6M20 4v7h-7"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v5H5V6h5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "plus-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "dot": '<circle cx="12" cy="12" r="5" fill="currentColor"/>',
    "dot-hollow": '<circle cx="12" cy="12" r="5"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "app": '<rect x="4" y="4" width="16" height="16" rx="4"/>',
}


def icon(name, size=16, cls="", stroke=1.75):
    return (
        f'<svg class="ic {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f"{ICONS[name]}</svg>"
    )


def theme_vars(theme):
    c = T["color"][theme]
    s, t, st, ac, bd = c["surface"], c["text"], c["status"], c["accent"], c["border"]
    o = T["elevation"]
    return f"""
  --bg:{s['background']}; --surface:{s['primary']}; --surface2:{s['secondary']}; --elev:{s['elevated']};
  --text:{t['primary']}; --text2:{t['secondary']}; --text3:{t['disabled']}; --on-accent:{t['onAccent']};
  --border:{bd['default']}; --border-strong:{bd['strong']};
  --accent:{ac['primary']}; --accent-hover:{ac['hover']}; --accent-pressed:{ac['pressed']}; --accent-text:{ac['text']};
  --accent-tint:color-mix(in srgb, {ac['primary']} {int(ac['tintOpacity'] * 100)}%, transparent);
  --info:{st['info']}; --info-text:{st['infoText']}; --success:{st['success']}; --success-text:{st['successText']};
  --warning:{st['warning']}; --warning-text:{st['warningText']}; --error:{st['error']}; --error-text:{st['errorText']}; --error-solid:{st['errorSolid']};
  --neutral:{st['neutral']}; --icon:{c['icon']['neutral']}; --focus:{c['focus']}; --scrim:{c['scrim']};
  --sh-dialog:{o['dialog'][theme]}; --sh-pop:{o['popover'][theme]}; --sh-card:{o['mobileCard'][theme]};
"""


def base_css():
    d, m = T["radius"]["desktop"], T["radius"]["mobile"]
    ty = T["typography"]
    fam = ty["family"]
    return f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:100%;height:100%;overflow:hidden}}
:root[data-theme=dark]{{{theme_vars('dark')}}}
:root[data-theme=light]{{{theme_vars('light')}}}
body{{background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}}
.ic{{flex:none;display:inline-block;vertical-align:middle}}
.desk{{font-family:{fam['desktop']};font-size:12px;line-height:18px;--r-s:{d['small']}px;--r-m:{d['medium']}px;--r-l:{d['large']}px}}
.mob{{font-family:{fam['mobile']};font-size:15px;line-height:22px;--r-s:{m['small']}px;--r-m:{m['medium']}px;--r-l:{m['large']}px}}
.mono{{font-family:{fam['mono']}}}
.c-info{{color:var(--info-text)}} .c-success{{color:var(--success-text)}} .c-warning{{color:var(--warning-text)}}
.c-error{{color:var(--error-text)}} .c-neutral{{color:var(--text2)}} .c-accent{{color:var(--accent-text)}}
.t2{{color:var(--text2)}} .t3{{color:var(--text3)}}
button,.btn{{font:inherit;color:inherit}}
"""
