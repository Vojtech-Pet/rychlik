"""Desktop persistent cryptographic identity (Prompt A15 §13-16).

A stable, non-secret `desktop_instance_id` plus an Ed25519 signing
keypair used to authenticate this desktop to a paired FriendSend device
for every handoff -- never a hostname/username/MAC address, and never
reused as the TLS identity (that belongs to FriendSend's own self-signed
cert; the desktop is a client here, not a TLS server).
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "rychlik" / "friendsend"


@dataclass(frozen=True)
class DesktopIdentity:
    desktop_instance_id: str
    private_key: Ed25519PrivateKey
    public_key_bytes: bytes  # 32 raw bytes (§15)

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)


class DesktopIdentityStore:
    """Persists the desktop's identity separately from download state
    (§16/§17 -- never inside state.db). Directory permissions 0700, key
    file permissions 0600 where POSIX applies (§16/§166)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or _default_data_dir()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def _identity_file(self) -> Path:
        return self._data_dir / "desktop_identity.json"

    def load_or_create(self) -> DesktopIdentity:
        if self._identity_file.exists():
            return self._load()
        return self._create()

    def _load(self) -> DesktopIdentity:
        raw = self._identity_file.read_text()
        try:
            data = json.loads(raw)
            desktop_instance_id = data["desktop_instance_id"]
            seed = bytes.fromhex(data["ed25519_private_key_seed_hex"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            # §89: a corrupt identity file must fail visibly, never
            # silently regenerate a new identity while trust records
            # elsewhere still reference the old one.
            raise DesktopIdentityCorruptError(str(self._identity_file)) from exc
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return DesktopIdentity(desktop_instance_id=desktop_instance_id, private_key=private_key, public_key_bytes=public_bytes)

    def _create(self) -> DesktopIdentity:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._data_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass  # best-effort on non-POSIX platforms

        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        desktop_instance_id = str(uuid.uuid4())

        payload = json.dumps(
            {"desktop_instance_id": desktop_instance_id, "ed25519_private_key_seed_hex": seed.hex()}
        )
        # Atomic write (§89): a crash mid-write must never leave a
        # truncated file later misread as a valid (or silently
        # regenerable) identity.
        tmp_path = self._identity_file.with_suffix(".tmp")
        tmp_path.write_text(payload)
        try:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
        os.replace(tmp_path, self._identity_file)

        return DesktopIdentity(desktop_instance_id=desktop_instance_id, private_key=private_key, public_key_bytes=public_bytes)


class DesktopIdentityCorruptError(Exception):
    """Raised when the persisted identity file exists but cannot be
    parsed -- never silently replaced with a fresh identity (§89/§90)."""
