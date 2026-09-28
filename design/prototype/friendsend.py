# NON-PRODUCTION DESIGN PROTOTYPE -- not wired into the application.
"""FriendSend Android mockups (Flutter logical px). App icons in the share-target
grid are neutral placeholders: real icons/labels come from Android's PackageManager at runtime."""
from common import base_css, icon

CSS = """
.ph{position:relative;width:100vw;height:100vh;display:flex;flex-direction:column;background:var(--bg);overflow:hidden}
.sysbar{height:28px;flex:none;display:flex;align-items:center;justify-content:space-between;padding:0 22px 0 24px;font-size:13px;font-weight:500}
.sysbar .r{display:flex;gap:6px;align-items:center}
.batt{width:22px;height:11px;border:1.5px solid currentColor;border-radius:3px;position:relative;padding:1px}
.batt i{display:block;height:100%;width:70%;background:currentColor;border-radius:1px}
.gest{height:22px;flex:none;display:grid;place-items:center}.gest i{width:120px;height:4px;border-radius:2px;background:var(--text2);opacity:.7}
.apph{height:56px;flex:none;display:flex;align-items:center;gap:12px;padding:0 8px 0 20px}
.logo{width:30px;height:30px;border-radius:9px;background:var(--accent);color:var(--on-accent);display:grid;place-items:center}
.apph .t{font-size:20px;line-height:28px;font-weight:600}
.apph .r{margin-left:auto;width:48px;height:48px;display:grid;place-items:center;color:var(--text2);border-radius:24px}
.apph .back{margin-left:-12px;width:48px;height:48px;display:grid;place-items:center;color:var(--text)}
.main{flex:1;min-height:0;display:flex;flex-direction:column;padding:8px 20px 12px}
.h1{font-size:28px;line-height:36px;font-weight:600;letter-spacing:-.2px}
.h2{font-size:20px;line-height:28px;font-weight:600}
.p{color:var(--text2);font-size:15px;line-height:22px}
.cap{color:var(--text2);font-size:12px;line-height:16px}
.card{background:var(--surface);border-radius:20px;box-shadow:var(--sh-card);padding:16px}
.field{background:var(--surface);border:1.5px solid var(--border-strong);border-radius:14px;padding:12px 14px;min-height:96px;color:var(--text2);position:relative;font-size:15px;line-height:22px}
.field.filled{color:var(--text);font-size:13px;line-height:19px;word-break:break-all}
.field.err{border-color:var(--error-text)}
.field .paste{position:absolute;right:10px;bottom:10px}
.btn{height:52px;border-radius:26px;display:flex;align-items:center;justify-content:center;gap:10px;font-size:16px;font-weight:600;padding:0 24px;width:100%}
.btn.primary{background:var(--accent);color:var(--on-accent)}
.btn.secondary{background:transparent;border:1.5px solid var(--border-strong);color:var(--text)}
.btn.tert{background:transparent;color:var(--accent-text);height:48px}
.btn.dtert{background:transparent;color:var(--error-text);height:48px}
.btn.dis{background:color-mix(in srgb,var(--text) 12%,transparent);color:var(--text3)}
.btn.danger{background:var(--error-solid);color:#fff}
.btn.sm{height:40px;width:auto;padding:0 16px;font-size:14px;border-radius:20px}
.chip{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 12px;border-radius:14px;font-size:13px;font-weight:600;border:1px solid currentColor;background:color-mix(in srgb,currentColor 10%,transparent)}
.hero{display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px}
.orb{width:104px;height:104px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,currentColor 14%,transparent);position:relative}
.orb::before{content:"";position:absolute;inset:-14px;border-radius:50%;border:1.5px solid color-mix(in srgb,currentColor 25%,transparent)}
.orb.sm{width:80px;height:80px}
.bar{height:8px;border-radius:4px;background:color-mix(in srgb,var(--text) 12%,transparent);overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px;background:var(--info)}
.bar.ind i{width:100%;background:repeating-linear-gradient(115deg,var(--info) 0 14px,color-mix(in srgb,var(--info) 55%,transparent) 14px 28px)}
.frow{display:flex;align-items:center;gap:12px}
.fic{width:48px;height:48px;border-radius:14px;background:var(--accent-tint);color:var(--accent-text);display:grid;place-items:center;flex:none}
.fic.thumb{background:linear-gradient(135deg,#3b3f8f,#6C5DF6 55%,#468BFF);color:#fff}
.frow b{font-size:16px;line-height:22px;font-weight:600;display:block;word-break:break-all}
.kv{display:flex;justify-content:space-between;padding:14px 0;border-bottom:1px solid var(--border)}.kv:last-child{border-bottom:none}
.kv .k{color:var(--text2)}
.stepl{display:flex;flex-direction:column;gap:14px;margin-top:18px}
.stepl .s{display:flex;align-items:center;gap:12px;color:var(--text2)}
.stepl .s.cur{color:var(--text);font-weight:600}
.dotc{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;border:1.5px solid var(--border-strong);flex:none}
.dotc.done{background:var(--success);border-color:var(--success);color:#0b1a10}
.dotc.cur{border-color:var(--info);color:var(--info-text)}
.scrim{position:absolute;inset:0;background:var(--scrim)}
.sheet{position:absolute;left:0;right:0;bottom:0;background:var(--elev);border-radius:28px 28px 0 0;padding:10px 20px 8px;box-shadow:0 -8px 32px rgba(0,0,0,.35)}
.sheet .grab{width:36px;height:4px;border-radius:2px;background:var(--border-strong);margin:0 auto 14px}
.grid{display:grid;gap:8px 0;margin:14px 0 6px}
.tile{display:flex;flex-direction:column;align-items:center;gap:7px;height:92px;padding-top:6px;text-align:center}
.tile .ico{width:52px;height:52px;border-radius:16px;display:grid;place-items:center;font-size:20px;font-weight:700;color:#fff}
.tile span{font-size:12px;line-height:16px;color:var(--text);max-width:80px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.morerow{display:flex;align-items:center;gap:12px;min-height:52px;margin-top:10px;padding:0 16px;border-radius:16px;background:color-mix(in srgb,var(--text) 7%,transparent)}
.morerow b{font-size:15px;font-weight:600}
.morerow .cap{margin-left:auto}
.morerow .ic{color:var(--text2)}
.dlgm{position:absolute;left:24px;right:24px;top:50%;transform:translateY(-50%);background:var(--elev);border-radius:28px;padding:24px;box-shadow:0 12px 40px rgba(0,0,0,.5)}
.dlgm .row{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}
.sys{background:#2b2b31;color:#e6e6ea;border-radius:28px 28px 0 0}
.tag{position:absolute;top:96px;left:50%;transform:translateX(-50%);background:#000c;color:#fff;font-size:11px;border:1px dashed #fff8;padding:3px 10px;border-radius:12px;z-index:5;white-space:nowrap}
.spin{width:56px;height:56px;border-radius:50%;border:5px solid color-mix(in srgb,var(--info) 22%,transparent);border-top-color:var(--info)}
.grow{flex:1}
"""

APPS = [("Messenger", "M", "#4a6cf7"), ("WhatsApp", "W", "#2e9e5b"), ("Telegram", "T", "#2b93c9"), ("Signal", "S", "#4666d8"),
        ("Messages", "M", "#3a7a8a"), ("Gmail", "G", "#c4534d"), ("Drive", "D", "#7a6bd6"), ("Bluetooth", "B", "#4b6aa8")]


def page(theme, w, h, body, overlay="", header="brand", right=None, tag=""):
    hdr = ""
    if header == "brand":
        r = f'<div class="r">{icon(right, 24)}</div>' if right else ""
        hdr = f'<div class="apph"><div class="logo">{icon("send", 18)}</div><span class="t">FriendSend</span>{r}</div>'
    elif header == "back":
        hdr = f'<div class="apph"><div class="back">{icon("chev-right", 24).replace("M9 6l6 6-6 6", "M15 6l-6 6 6 6")}</div><span class="t">{right or ""}</span></div>'
    sysbar = f'<div class="sysbar"><span>9:41</span><span class="r">{icon("wifi", 15)}<span class="batt"><i></i></span></span></div>'
    t = f'<div class="tag">{tag}</div>' if tag else ""
    return (f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"><!-- NON-PRODUCTION DESIGN PROTOTYPE -->'
            f'<meta name="viewport" content="width={w}"><style>{base_css()}{CSS}</style></head><body class="mob">'
            f'<div class="ph">{t}{sysbar}{hdr}<div class="main">{body}</div><div class="gest"><i></i></div>{overlay}</div></body></html>')


def btn(label, cls="primary", ic=None):
    return f'<div class="btn {cls}">{icon(ic, 20) if ic else ""}{label}</div>'


def hero(ic, cls, title, text, size=44, small=False):
    return (f'<div class="hero"><div class="orb {"sm" if small else ""} {cls}">{icon(ic, size)}</div>'
            f'<div class="h1" style="margin-top:14px">{title}</div><div class="p" style="max-width:300px">{text}</div></div>')


# ---- setup -----------------------------------------------------------------
def unpaired(theme, w, h, filled=False, error=False):
    code = '{"protocol_version":1,"security_profile":"pinned-tls-signature-v1","pairing_session_id":"9c1e…","desktop_endpoint":{"host":"192.168.1.20","port":41873},"secret":"••••••••", …}'
    fld = (f'<div class="field filled">{code}</div>' if filled else
           f'<div class="field {"err" if error else ""}">Paste code from Rýchlik<div class="paste"><div class="btn secondary sm">{icon("copy", 18)}Paste</div></div></div>')
    if error:
        fld = '<div class="field filled err">hello world</div>'
    msg = ""
    if error:
        msg = f'<div class="cap c-error" style="margin-top:8px;display:flex;gap:6px;align-items:center">{icon("warning", 16)}This does not look like a pairing code.</div>'
    elif filled:
        msg = f'<div class="cap" style="margin-top:8px;display:flex;gap:6px;align-items:center"><span class="c-info">{icon("clock", 16)}</span>Code is valid for 4:12</div>'
    steps = "".join(f'<div class="frow" style="gap:12px;margin:10px 0"><span class="dotc" style="border-color:var(--accent);color:var(--accent-text);font-size:12px;font-weight:700">{i}</span><span class="p" style="color:var(--text)">{t}</span></div>'
                    for i, t in enumerate(["On your computer, open Rýchlik → Devices → Pair FriendSend.", "Copy the pairing code.", "Paste it here."], 1))
    body = (f'<div style="margin-top:8px" class="h1">Connect to Rýchlik</div><div class="p" style="margin-top:6px">Pair this phone once with your desktop.</div>'
            f'<div class="card" style="margin-top:20px;padding:6px 16px">{steps}</div><div style="margin-top:20px">{fld}{msg}</div>'
            f'<div class="grow"></div>{btn("Pair", "primary" if filled else "dis")}'
            f'<div class="cap" style="text-align:center;margin-top:12px">Your paired desktop will be remembered.</div>')
    return page(theme, w, h, body)


def pairing_progress(theme, w, h):
    body = ('<div class="grow"></div><div class="hero"><div class="spin"></div><div class="h2" style="margin-top:18px">Connecting to Rýchlik…</div>'
            '<div class="p">This takes a few seconds.</div></div><div class="grow"></div>')
    return page(theme, w, h, body)


def pairing_success(theme, w, h):
    body = ('<div class="grow"></div>' + hero("check-circle", "c-success", "Paired", "FriendSend will remember this computer. You will not need to pair again.") +
            '<div class="grow"></div>' + btn("Done"))
    return page(theme, w, h, body)


# ---- ready / connectivity ---------------------------------------------------
def ready(theme, w, h, offline=False):
    if offline:
        top = hero("wifi-off", "c-warning", "No Wi-Fi connection", "Connect to the same Wi-Fi network as your computer to receive files.")
        bottom = btn("Open Wi-Fi settings", "secondary", "wifi")
    else:
        top = ('<div class="hero"><span class="chip c-success">' + icon("check-circle", 16) + 'Connected to Rýchlik</span>'
               f'<div class="orb c-accent" style="margin-top:22px">{icon("download", 44)}</div>'
               '<div class="h1" style="margin-top:22px">Ready to receive</div>'
               '<div class="p" style="max-width:300px">Keep FriendSend open while you send a file from your computer.</div></div>')
        bottom = (f'<div class="card frow"><div class="fic">{icon("monitor", 24)}</div><div><b style="font-size:16px;font-weight:600">Rýchlik</b>'
                  f'<div class="cap c-success" style="display:flex;gap:5px;align-items:center">{icon("shield-check", 14)}Trusted computer</div></div>'
                  f'<span style="margin-left:auto;color:var(--text2)">{icon("chev-right", 22)}</span></div>')
    body = f'<div class="grow"></div>{top}<div class="grow"></div>{bottom}'
    return page(theme, w, h, body, right="monitor")


# ---- receive pipeline --------------------------------------------------------
def receiving(theme, w, h):
    body = (f'<div class="h2" style="margin-top:8px">Receiving from Rýchlik</div>'
            f'<div class="card" style="margin-top:16px"><div class="frow"><div class="fic thumb">{icon("video", 24)}</div><div><b>holiday.mp4</b><div class="cap">198 MB</div></div></div>'
            f'<div class="bar" style="margin-top:20px"><i style="width:74%"></i></div>'
            f'<div style="display:flex;justify-content:space-between;margin-top:10px"><span class="p" style="color:var(--text)">74%</span><span class="cap" style="align-self:center">12.8 MB/s</span></div>'
            f'<div class="cap" style="margin-top:2px">146 MB of 198 MB</div></div>'
            f'<div class="grow"></div>{btn("Cancel", "secondary")}')
    return page(theme, w, h, body)


def pipeline(theme, w, h, stage):
    """stage: verifying | preparing"""
    st = lambda label, state: (f'<div class="s {"cur" if state == "cur" else ""}"><span class="dotc {state}">{icon("check", 14, stroke=3) if state == "done" else ""}</span>{label}</div>')
    if stage == "verifying":
        steps = [("Received", "done"), ("Verifying file", "cur"), ("Getting ready to share", "")]
        title, sub = "File received", "Checking that the file is complete and unchanged."
    else:
        steps = [("Received", "done"), ("Verified", "done"), ("Getting ready to share", "cur")]
        title, sub = "Almost ready", "Preparing the file so other apps can open it."
    body = (f'<div class="h2" style="margin-top:8px">{title}</div><div class="p" style="margin-top:4px">{sub}</div>'
            f'<div class="card" style="margin-top:16px"><div class="frow"><div class="fic thumb">{icon("video", 24)}</div><div><b>holiday.mp4</b><div class="cap">198 MB</div></div></div>'
            f'<div class="bar ind" style="margin-top:18px"><i></i></div></div>'
            f'<div class="stepl">{"".join(st(l, s) for l, s in steps)}</div><div class="grow"></div>'
            f'<div class="cap" style="text-align:center">You can’t share the file until it has been verified.</div>')
    return page(theme, w, h, body)


def received_body():
    return (f'<div class="grow"></div><div class="hero"><span class="chip c-success">{icon("check-circle", 16)}Verified</span>'
            f'<div class="fic thumb" style="width:120px;height:120px;border-radius:28px;margin-top:20px">{icon("video", 48)}</div>'
            f'<div class="h1" style="margin-top:20px">Video ready</div><div class="frow" style="display:block"><b style="margin-top:4px">holiday.mp4</b><div class="cap" style="margin-top:2px">198 MB</div></div></div>'
            f'<div class="grow"></div>{btn("Choose app", "primary", "send")}{btn("Discard", "dtert")}'
            f'<div class="cap" style="text-align:center">The file is kept only until you share or discard it.</div>')


def received(theme, w, h):
    return page(theme, w, h, received_body())


def tiles(w, apps):
    cols = max(3, (w - 40) // 84)
    items = "".join(f'<div class="tile"><div class="ico" style="background:{c}">{ch}</div><span>{n}</span></div>' for n, ch, c in apps)
    return f'<div class="grid" style="grid-template-columns:repeat({cols},1fr)">{items}</div>'


def more_row():
    return (f'<div class="morerow">{icon("grid", 22)}<b>More apps…</b><span class="cap">Open Android Sharesheet</span></div>')


def picker(theme, w, h, apps=None):
    apps = apps if apps is not None else APPS[:6]
    sheet = (f'<div class="sheet"><div class="grab"></div><div class="h2">Where do you want to send it?</div>'
             f'<div class="frow" style="margin-top:12px"><div class="fic thumb" style="width:36px;height:36px;border-radius:10px">{icon("video", 18)}</div><span class="p" style="color:var(--text)">holiday.mp4 · 198 MB</span></div>'
             f'{tiles(w, apps)}{more_row()}<div class="cap" style="text-align:center;margin-top:10px">Pick an app, then choose the person inside that app.</div>'
             f'<div style="margin-top:6px">{btn("Discard", "dtert")}</div></div>')
    return page(theme, w, h, received_body(), overlay=f'<div class="scrim"></div>{sheet}')


def picker_none(theme, w, h):
    sheet = (f'<div class="sheet"><div class="grab"></div><div class="hero" style="padding:8px 0 4px"><div class="orb sm c-neutral">{icon("grid", 32)}</div>'
             f'<div class="h2" style="margin-top:10px">No compatible apps found</div><div class="p" style="max-width:320px">Install a messaging or storage app, or open the system share menu. The file is kept until you discard it.</div></div>'
             f'<div style="margin-top:16px">{btn("More apps…", "primary", "grid")}{btn("Try again", "tert")}{btn("Discard", "dtert")}</div></div>')
    return page(theme, w, h, received_body(), overlay=f'<div class="scrim"></div>{sheet}')


def system_sheet(theme, w, h):
    cols = max(3, (w - 40) // 84)
    items = "".join(f'<div class="tile"><div class="ico" style="background:{c};border-radius:50%">{ch}</div><span style="color:#e6e6ea">{n}</span></div>' for n, ch, c in APPS)
    sheet = (f'<div class="sheet sys"><div class="grab" style="background:#77777f"></div><div style="font-size:18px;font-weight:500;margin-bottom:12px">Sharing 1 file</div>'
             f'<div style="background:#3a3a42;border-radius:16px;padding:12px 14px;display:flex;gap:10px;align-items:center">{icon("video", 20)}<span>holiday.mp4</span></div>'
             f'<div class="grid" style="grid-template-columns:repeat({cols},1fr)">{items}</div></div>')
    return page(theme, w, h, received_body(), overlay=f'<div class="scrim"></div>{sheet}', tag="Reference only: Android system Sharesheet (not designed by FriendSend)")


def handoff(theme, w, h):
    body = ('<div class="grow"></div>' + hero("send", "c-success", "Handed off to Messenger", "Choose who to send it to inside Messenger. FriendSend can’t see whether it was delivered.", 40) +
            '<div class="grow"></div>' + btn("Done") + btn("Send with another app", "tert") +
            '<div class="cap" style="text-align:center">The temporary copy is removed when you tap Done, or automatically after a while.</div>')
    return page(theme, w, h, body)


def outcome(theme, w, h, kind):
    m = {
        "cancelled": ("x-circle", "c-neutral", "Transfer cancelled", "No complete file was saved.", "Done", ""),
        "failed": ("warning", "c-warning", "Couldn't receive the file", "The connection was interrupted. To try again, send the file from Rýchlik.", "Done", "Details"),
        "integrity": ("shield-alert", "c-error", "File verification failed", "The received file did not match the file sent by Rýchlik and was discarded.", "Done", ""),
    }[kind]
    ic, cls, title, text, b, det = m
    extra = (f'<div class="cap" style="display:flex;justify-content:center;gap:6px;align-items:center;margin-top:6px">{icon("chev-right", 14)}Details</div>' if det else "")
    body = '<div class="grow"></div>' + hero(ic, cls, title, text) + extra + '<div class="grow"></div>' + btn(b)
    return page(theme, w, h, body)


# ---- trusted desktop ---------------------------------------------------------
def trusted(theme, w, h, confirm=False):
    rows = [("Status", f'<span class="chip c-success">{icon("shield-check", 14)}Trusted</span>'), ("Identity", '<span style="font-family:var(--mono,monospace)">b630 ef25 …</span>'),
            ("Paired", "28 Sep 2026, 08:20")]
    kv = "".join(f'<div class="kv"><span class="k">{k}</span><span>{v}</span></div>' for k, v in rows)
    body = (f'<div class="card frow" style="margin-top:8px"><div class="fic">{icon("monitor", 26)}</div><div><b style="font-size:18px">Rýchlik</b><div class="cap">Your computer</div></div></div>'
            f'<div class="card" style="margin-top:12px;padding:4px 16px">{kv}</div>'
            f'<div class="cap" style="margin-top:12px">FriendSend only accepts files from computers you paired.</div><div class="grow"></div>{btn("Forget this computer", "dtert", "trash")}')
    ov = ""
    if confirm:
        ov = ('<div class="scrim"></div><div class="dlgm"><div class="h2">Forget this computer?</div>'
              '<div class="p" style="margin-top:10px">You will need to pair again before receiving files from it.</div>'
              f'<div class="row"><div class="btn tert sm" style="width:auto">Cancel</div><div class="btn danger sm">Forget</div></div></div>')
    return page(theme, w, h, body, overlay=ov, header="back", right="Trusted computer")


def jobs():
    W, H = 390, 844
    P = lambda name, f, *a, w=W, h=H, **k: (f"friendsend/{name}.png", w, h, f(*a, **k))
    yield P("01_pairing", unpaired, "dark", W, H)
    yield P("01b_pairing_light", unpaired, "light", W, H)
    yield P("01c_pairing_filled", unpaired, "dark", W, H, filled=True)
    yield P("01d_pairing_error", unpaired, "dark", W, H, error=True)
    yield P("01e_pairing_progress", pairing_progress, "dark", W, H)
    yield P("01f_pairing_success", pairing_success, "dark", W, H)
    yield P("02_ready", ready, "dark", W, H)
    yield P("02b_ready_light", ready, "light", W, H)
    yield P("02c_no_wifi", ready, "dark", W, H, offline=True)
    yield P("03_receiving", receiving, "dark", W, H)
    yield P("03b_receiving_light", receiving, "light", W, H)
    yield P("04_verifying", pipeline, "dark", W, H, "verifying")
    yield P("04b_preparing_share", pipeline, "dark", W, H, "preparing")
    yield P("05_received", received, "dark", W, H)
    yield P("06_share_target_picker", picker, "light", W, H)
    yield P("07_share_target_picker_dark", picker, "dark", W, H)
    yield P("07b_picker_few_apps", picker, "dark", W, H, apps=APPS[:3])
    yield P("07c_picker_many_apps", picker, "dark", W, H, apps=APPS)
    yield P("07d_no_compatible_apps", picker_none, "dark", W, H)
    yield P("07e_system_more_apps", system_sheet, "dark", W, H)
    yield P("07f_handoff_accepted", handoff, "dark", W, H)
    yield P("08_transfer_error", outcome, "dark", W, H, "failed")
    yield P("08b_cancelled", outcome, "dark", W, H, "cancelled")
    yield P("08c_integrity_failure", outcome, "dark", W, H, "integrity")
    yield P("09_trusted_desktop", trusted, "dark", W, H)
    yield P("10_forget_confirmation", trusted, "dark", W, H, confirm=True)
    yield P("10b_trusted_desktop_light", trusted, "light", W, H)
    yield ("friendsend/11_components_dark.png", 1000, 820, components("dark"))
    yield ("friendsend/12_components_light.png", 1000, 820, components("light"))
    for (w, h) in [(360, 800), (412, 915)]:
        yield P(f"sizes/pairing_{w}x{h}", unpaired, "dark", w, h, w=w, h=h)
        yield P(f"sizes/receiving_{w}x{h}", receiving, "dark", w, h, w=w, h=h)
        yield P(f"sizes/picker_{w}x{h}", picker, "dark", w, h, w=w, h=h)
        yield P(f"sizes/picker_light_{w}x{h}", picker, "light", w, h, w=w, h=h, apps=APPS)


def components(theme, w=1000, h=820):
    sect = lambda t, b: f'<div style="flex:1;min-width:280px"><div class="cap" style="font-weight:600;margin-bottom:10px;text-transform:uppercase;letter-spacing:.06em">{t}</div>{b}</div>'
    btns = (btn("Primary", "primary") + '<div style="height:10px"></div>' + btn("Secondary", "secondary") + '<div style="height:10px"></div>' + btn("Tertiary", "tert") +
            '<div style="height:10px"></div>' + btn("Destructive", "danger") + '<div style="height:10px"></div>' + btn("Discard", "dtert") + '<div style="height:10px"></div>' + btn("Disabled", "dis"))
    chips = "".join(f'<div style="margin:8px 0"><span class="chip {c}">{icon(i, 16)}{t}</span></div>' for c, i, t in
                    [("c-success", "check-circle", "Connected to Rýchlik"), ("c-success", "shield-check", "Trusted"), ("c-info", "clock", "Code valid 4:12"), ("c-warning", "warning", "No Wi-Fi"), ("c-error", "shield-alert", "Verification failed")])
    prog = ('<div class="bar"><i style="width:74%"></i></div><div class="cap" style="margin:6px 0 16px">Receiving 74% (8 dp, pill)</div><div class="bar ind"><i></i></div><div class="cap" style="margin-top:6px">Verifying (indeterminate)</div>')
    tile = tiles(360, APPS[:3]).replace("repeat(4", "repeat(4")
    card = (f'<div class="card frow"><div class="fic thumb">{icon("video", 24)}</div><div><b>holiday.mp4</b><div class="cap">198 MB · Verified</div></div></div>'
            f'<div style="height:14px"></div><div class="card" style="text-align:center"><span class="c-warning">{icon("warning", 32)}</span><div class="h2" style="margin-top:6px">Error state</div><div class="p">Title, one sentence, one action.</div></div>')
    body = (f'<div class="h2" style="margin-bottom:18px">FriendSend - component sheet ({theme})</div><div style="display:flex;gap:28px;flex-wrap:wrap">'
            f'{sect("Buttons (52 dp, pill)", btns)}{sect("Status chips (icon + text)", chips)}{sect("Progress + cards", prog + "<div style=height:18px></div>" + card)}'
            f'{sect("Share target tiles (84 x 92 dp) + system row", "<div style=width:340px>" + tile + more_row() + "</div>")}</div>')
    return (f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"><!-- NON-PRODUCTION DESIGN PROTOTYPE --><style>{base_css()}{CSS}</style></head>'
            f'<body class="mob"><div class="ph" style="padding:28px 32px;width:{w}px;height:{h}px;overflow:auto">{body}</div></body></html>')
