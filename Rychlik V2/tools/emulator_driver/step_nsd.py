import sys, time
sys.path.insert(0, "/mnt/Data/Rychlik-app/src")
from rychlik.device.security.discovery import FriendSendDiscoveryService
found = []
svc = FriendSendDiscoveryService()
svc.subscribe(on_found=lambda d: found.append(d))
svc.start()
end = time.monotonic() + 25
while time.monotonic() < end and not found:
    time.sleep(0.5)
print("FOUND", [(d.device_id, d.endpoint.host, d.endpoint.port, d.security_profile) for d in found])
svc.stop()
