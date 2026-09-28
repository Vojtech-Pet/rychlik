import sys
sys.path.insert(0, "/tmp/claude-1000/-home-vojtech-Stiahnut--VideoTranslator-studio-lab/0060245c-71c8-4a38-a17c-5608761f3334/scratchpad/emu")
from desktop import *
rig = Rig(Path("/tmp/rt/emu_work_cancel"))
try:
    rig.service._chunk_size = 16384
    rig.service._chunk_delay = 0.03
    src = MF.make_compatible_mp4(rig.work / "long.mp4", duration=240.0, width=640, height=480)
    print("SIZE", src.stat().st_size)
    art = rig.download(src, "emu-cancel")
    dialog = DD.SendToDeviceDialog(art, rig.controller)
    dialog.send_button.click()
    assert pump(lambda: dialog.headline.text().startswith("Sending") and dialog.bar.value() > 20, 60), dialog.headline.text()
    time.sleep(0.5); app.processEvents()
    shot("phone_receiving_mid")
    dialog.grab().save(str(SHOTS / "send_mid_desktop.png"))
    snap = rig.controller.snapshot(dialog._handoff_id)
    print("MID bytes_sent", snap.bytes_sent, "of", snap.total_bytes)
    dialog.cancel_button.click()
    assert pump(lambda: rig.controller.snapshot(dialog._handoff_id).state.name in ("CANCELLED", "FAILED"), 30)
    snap = rig.controller.snapshot(dialog._handoff_id)
    print("FINAL", snap.state.name, snap.failure_code, dialog.headline.text())
    time.sleep(2); app.processEvents()
    shot("phone_after_cancel")
    print("CACHE", repr(adb("shell", "run-as", PKG, "ls", "cache/friendsend", check=False)))
finally:
    rig.close()
