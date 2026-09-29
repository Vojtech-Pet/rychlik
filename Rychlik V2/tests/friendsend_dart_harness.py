"""Prompt A14: drives the REAL FriendSend Dart receiver core as a real
child process, over a real socket -- this is not the Python test fixture
from A13 (tests/friendsend_receiver_fixture.py), and it is not a mock.

`friendsend/bin/receiver_harness.dart` constructs the exact same
`FriendSendReceiverServer` / `TempCache` classes the Flutter app itself
uses (see friendsend/lib/receiver/). This module only knows how to launch
that process and speak its line-delimited JSON stdin/stdout protocol; the
protocol/streaming/integrity logic under test is 100% real Dart app code.

Requires a Dart SDK on PATH, or `FRIENDSEND_DART_EXECUTABLE` pointing at
one (e.g. the Dart SDK bundled inside a Flutter install). See
docs/FRIENDSEND_ANDROID_MVP_RESULT.md for the exact toolchain this was
proven against.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from rychlik.device.contracts import FriendSendEndpoint

_FRIENDSEND_DIR = Path(__file__).resolve().parent.parent / "friendsend"


def _dart_executable() -> str:
    override = os.environ.get("FRIENDSEND_DART_EXECUTABLE")
    if override:
        return override
    found = shutil.which("dart")
    if found:
        return found
    fallback = Path("/mnt/Basic_data_partition1/vojtech/flutter/bin/cache/dart-sdk/bin/dart")
    if fallback.exists():
        return str(fallback)
    raise RuntimeError(
        "No Dart SDK found on PATH and FRIENDSEND_DART_EXECUTABLE is not set -- "
        "cannot run the real Python<->Dart cross-language E2E tests."
    )


class FriendSendDartHarness:
    """Launches and speaks to `bin/receiver_harness.dart` (real process,
    real socket, real dart:io HttpServer -- see that file's own docstring
    for the exact wire contract used here)."""

    def __init__(self, tmp_path: Path, *, seed_tokens: list[str] | None = None, **extra_args: str) -> None:
        self._cache_dir = tmp_path / "friendsend_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        args = [
            _dart_executable(),
            "run",
            "bin/receiver_harness.dart",
            f"--cache-dir={self._cache_dir}",
        ]
        for token in seed_tokens or []:
            args.append(f"--seed-token={token}")
        for key, value in extra_args.items():
            flag = key.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    args.append(f"--{flag}")
            else:
                args.append(f"--{flag}={value}")

        self._process = subprocess.Popen(
            args,
            cwd=str(_FRIENDSEND_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Merged into stdout and drained by the same reader thread --
            # otherwise an unread stderr pipe can fill its OS buffer and
            # deadlock the child process mid-run.
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
            raise RuntimeError("FriendSend Dart receiver harness did not start within 20s")
        assert self._handshake is not None

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            # `dart run`'s own "Running build hooks..." startup banner has
            # no trailing newline of its own, so it can end up
            # concatenated onto the same line as our first real JSON
            # message (observed: "Running build hooks...{...}") -- take
            # the JSON object starting at the first '{', never require
            # the whole line to already be valid JSON.
            brace = line.find("{")
            if brace == -1:
                continue
            try:
                payload = json.loads(line[brace:])
            except ValueError:
                continue
            if self._handshake is None and "port" in payload and "device_id" in payload:
                self._handshake = payload
                self._handshake_ready.set()
            elif "event" in payload:
                self._events.put(payload)

    @property
    def device_id(self) -> str:
        return self._handshake["device_id"]

    @property
    def endpoint(self) -> FriendSendEndpoint:
        return FriendSendEndpoint(host=self._handshake["host"], port=self._handshake["port"])

    def accept_token(self, token: str) -> None:
        self._send(f"ACCEPT_TOKEN {token}")

    def cancel(self, handoff_id: str) -> None:
        self._send(f"CANCEL {handoff_id}")

    def next_event(self, timeout: float = 10.0) -> dict:
        return self._events.get(timeout=timeout)

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
