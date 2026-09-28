import json, sys, time
sys.path.insert(0, "/tmp/claude-1000/-home-vojtech-Stiahnut--VideoTranslator-studio-lab/0060245c-71c8-4a38-a17c-5608761f3334/scratchpad/emu")
from lib import *
from pathlib import Path
from rychlik.device.security.identity import DesktopIdentityStore
from rychlik.device.security.pairing_bootstrap import SecurePairingManager
from rychlik.device.security.trust_store import FriendSendTrustStore

STATE = Path("/tmp/rt/emu_desktop")
STATE.mkdir(parents=True, exist_ok=True)
identity = DesktopIdentityStore(STATE / "id").load_or_create()
trust = FriendSendTrustStore(STATE / "trust.json")
before = {d.device_id for d in trust.all_devices()}
mgr = SecurePairingManager(identity=identity, trust_store=trust, bind_host="127.0.0.1")
payload = mgr.create_session().to_wire_dict()
port = payload["desktop_endpoint"]["port"]
payload["desktop_endpoint"]["host"] = "10.0.2.2"  # emulator alias for the host loopback -- test setup only
launch(clear=True)
shot("p01_unpaired")
tap(540, 1020)  # paste field
type_text(json.dumps(payload, separators=(",", ":")))
shot("p02_filled")
tap(540, 1344)  # Pair (button sits above the keyboard)
time.sleep(4)
shot("p03_after_pair")
new = [d for d in trust.all_devices() if d.device_id not in before]
print("PAIRED", [(d.device_id, d.endpoint_host, d.endpoint_port) for d in new])
mgr.stop()
