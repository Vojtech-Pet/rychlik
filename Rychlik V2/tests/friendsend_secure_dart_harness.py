"""Prompt A15: drives the REAL secure (pinned-tls-signature-v1)
FriendSend Dart receiver core as a real child process -- the secure
analogue of friendsend_dart_harness.py (Prompt A14). See
friendsend/bin/secure_receiver_harness.dart for the exact wire contract
this speaks.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path

from friendsend_dart_harness import _dart_executable  # reuse the same SDK resolution

_FRIENDSEND_DIR = Path(__file__).resolve().parent.parent / "friendsend"


class FriendSendSecureDartHarness:
    def __init__(self, tmp_path: Path, *, trust_desktops: list[tuple[str, bytes]] | None = None) -> None:
        self._cache_dir = tmp_path / "friendsend_secure_cache"
        self._identity_dir = tmp_path / "friendsend_secure_identity"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._identity_dir.mkdir(parents=True, exist_ok=True)

        import base64

        args = [
            _dart_executable(),
            "run",
            "bin/secure_receiver_harness.dart",
            f"--cache-dir={self._cache_dir}",
            f"--identity-dir={self._identity_dir}",
        ]
        for desktop_instance_id, public_key in trust_desktops or []:
            args.append(f"--trust-desktop={desktop_instance_id}:{base64.b64encode(public_key).decode()}")

        self._process = subprocess.Popen(
            args,
            cwd=str(_FRIENDSEND_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._handshake: dict | None = None
        self._handshake_ready = threading.Event()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        if not self._handshake_ready.wait(timeout=20):
            raise RuntimeError("FriendSend secure Dart receiver harness did not start within 20s")
        assert self._handshake is not None

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            brace = line.find("{")
            if brace == -1:
                continue
            try:
                payload = json.loads(line[brace:])
            except ValueError:
                continue
            if self._handshake is None and "port" in payload and "tls_spki_sha256" in payload:
                self._handshake = payload
                self._handshake_ready.set()
            elif "event" in payload:
                self._events.put(payload)

    @property
    def identity_dir(self) -> Path:
        """The directory holding this receiver's real TLS identity +
        desktop trust store -- callers pairing against this exact
        receiver (e.g. via run_dart_pairing_client) must use this same
        directory so the pairing client observes the SAME
        friendsend_tls_spki_sha256 the receiver actually presents on the
        wire, not a freshly-generated, unrelated identity."""
        return self._identity_dir

    @property
    def device_id(self) -> str:
        return self._handshake["device_id"]

    @property
    def host(self) -> str:
        return self._handshake["host"]

    @property
    def port(self) -> int:
        return self._handshake["port"]

    @property
    def tls_spki_sha256(self) -> str:
        return self._handshake["tls_spki_sha256"]

    def trust_desktop(self, desktop_instance_id: str, public_key: bytes) -> None:
        import base64

        self._send(f"TRUST_DESKTOP {desktop_instance_id} {base64.b64encode(public_key).decode()}")

    def cancel(self, handoff_id: str) -> None:
        self._send(f"CANCEL {handoff_id}")

    def wait_for_event(self, kind: str, *, handoff_id: str | None = None, timeout: float = 10.0) -> dict:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                event = self._events.get(timeout=remaining)
            except queue.Empty:
                break
            if event.get("event") == kind and (handoff_id is None or event.get("handoff_id") == handoff_id):
                return event
        raise TimeoutError(f"no {kind!r} event observed within {timeout}s")

    def _send(self, line: str) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()

    def stop(self) -> None:
        try:
            self._send("STOP")
        except (BrokenPipeError, ValueError):
            pass
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)


def run_dart_pairing_client(
    *,
    identity_dir: Path,
    device_id: str,
    display_name: str,
    friendsend_host: str,
    friendsend_port: int,
    payload_json_line: str,
    timeout: float = 20.0,
) -> dict:
    """Runs the real Dart pairing CLIENT (bin/pairing_client_harness.dart
    -- the exact class the Flutter app's pairing screen uses) as a
    one-shot subprocess against a real desktop pairing-bootstrap
    listener. Returns the parsed `{"pairing_result": "ok"|"error", ...}`
    line it prints."""
    args = [
        _dart_executable(),
        "run",
        "bin/pairing_client_harness.dart",
        f"--device-id={device_id}",
        f"--display-name={display_name}",
        f"--identity-dir={identity_dir}",
        f"--friendsend-host={friendsend_host}",
        f"--friendsend-port={friendsend_port}",
    ]
    result = subprocess.run(
        args,
        cwd=str(_FRIENDSEND_DIR),
        input=payload_json_line + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    import sys

    print(f"DART_CLIENT_STDERR: {result.stderr}", file=sys.stderr)
    for line in result.stdout.splitlines():
        brace = line.find("{")
        if brace == -1:
            continue
        try:
            payload = json.loads(line[brace:])
        except ValueError:
            continue
        if "pairing_result" in payload:
            return payload
    raise RuntimeError(f"pairing client produced no result line; stdout={result.stdout!r} stderr={result.stderr!r}")
