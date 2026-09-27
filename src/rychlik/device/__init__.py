"""Device Mode / FriendSend desktop handoff foundation (Prompt A13).

FriendSend Android does not exist yet. This package proves the
DESKTOP-side half of a versioned, authenticated handoff contract against
a deterministic test/dev receiver fixture (see tests/friendsend_receiver_
fixture.py) -- never a production phone application. See
docs/DEVICE_MODE_FOUNDATION.md and docs/FRIENDSEND_PROTOCOL_V1.md.

Device Mode is completely independent of Share by Link (rychlik.share) --
neither package imports the other, and this package never touches
DownloadManagerService's internals, only an already-validated
rychlik.core.artifact.Artifact.
"""
