import sys
sys.path.insert(0, "/tmp/claude-1000/-home-vojtech-Stiahnut--VideoTranslator-studio-lab/0060245c-71c8-4a38-a17c-5608761f3334/scratchpad/emu")
from desktop import *
kind = sys.argv[1]
makers = {"pass": (MF.make_compatible_mp4, "holiday.mp4"), "remux": (MF.make_remux_mkv, "clip.mkv"), "transcode": (MF.make_full_transcode_source, "movie.webm")}
mk, name = makers[kind]
rig = Rig(Path(f"/tmp/rt/emu_work_{kind}"))
try:
    src = mk(rig.work / name, duration=8.0) if kind != "pass" else mk(rig.work / name, duration=8.0)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    art = rig.download(src, "emu-" + kind)
    launch_needed = False
    dialog, seen, ok = rig.send(art)
    print("KIND", kind, "OK", ok, "STAGES", seen, "SRC_SHA_SAME", hashlib.sha256(src.read_bytes()).hexdigest() == sha, "ART_SHA", art.sha256[:12])
    dialog.grab().save(str(SHOTS / f"send_{kind}_dialog.png"))
    time.sleep(1.5)
    shot(f"phone_after_{kind}")
finally:
    rig.close()
