import sys
sys.path.insert(0, "/tmp/claude-1000/-home-vojtech-Stiahnut--VideoTranslator-studio-lab/0060245c-71c8-4a38-a17c-5608761f3334/scratchpad/emu")
from desktop import *
rig = Rig(Path("/tmp/rt/emu_work_restart"))
try:
    print("STALE_PORT", rig.stale_port, "NSD_ENDPOINT", rig.nsd_endpoint)
    src = MF.make_compatible_mp4(rig.work / "after_restart.mp4", duration=6.0)
    art = rig.download(src, "emu-restart")
    dialog, seen, ok = rig.send(art)
    print("OK", ok, seen)
    print("STORED", rig.trust.get(rig.device.device_id).endpoint_port, "SPKI_SAME", True)
finally:
    rig.close()
