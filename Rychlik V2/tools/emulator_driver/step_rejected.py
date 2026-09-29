import sys
sys.path.insert(0, "/tmp/claude-1000/-home-vojtech-Stiahnut--VideoTranslator-studio-lab/0060245c-71c8-4a38-a17c-5608761f3334/scratchpad/emu")
from desktop import *
rig = Rig(Path("/tmp/rt/emu_work_rejected"))
try:
    src = MF.make_compatible_mp4(rig.work / "rejected.mp4", duration=3.0)
    art = rig.download(src, "emu-rejected")
    dialog, seen, ok = rig.send(art)
    print("OK", ok, seen, "|", dialog.headline.text(), "|", dialog.detail.text())
    dialog.grab().save(str(SHOTS / "desktop_rejected.png"))
    snap = rig.controller.snapshot(dialog._handoff_id)
    print("STATE", snap.state.name, snap.failure_code)
finally:
    rig.close()
