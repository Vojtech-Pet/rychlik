"""A bounded, cancellable FFmpeg process runner (Prompt A16 §51/§65-69).

Mirrors the house subprocess-safety style (argv list, never `shell=True`,
bounded timeout) established in `rychlik.share.media_probe`/
`thumbnail_generator`, extended with real-time machine-readable progress
(`-progress pipe:1 -nostats`, never parsed from decorative human stderr)
and cooperative cancellation (terminate -> bounded wait -> kill -> reap,
never a leaked zombie process, §74).
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_STDERR_TAIL_LIMIT = 4000  # bytes -- never retain unbounded ffmpeg logs (§66)
_TERMINATE_GRACE_SECONDS = 3.0


@dataclass(frozen=True)
class FFmpegRunResult:
    success: bool
    exit_code: int | None
    stderr_tail: str
    cancelled: bool = False


ProgressCallback = Callable[[float | None, float | None], None]  # (processed_seconds, duration_seconds)


class FFmpegProcessRunner:
    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg_path = ffmpeg_path

    def run(
        self,
        args: list[str],
        *,
        duration_seconds: float | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> FFmpegRunResult:
        """`args` are the FFmpeg arguments AFTER the executable itself
        (never shell text, §51) -- this method always appends
        `-progress pipe:1 -nostats` so progress is read from a real
        machine-readable stream, not scraped from stderr (§67)."""
        full_args = [self._ffmpeg_path, "-y", "-progress", "pipe:1", "-nostats", *args]

        try:
            process = subprocess.Popen(
                full_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return FFmpegRunResult(success=False, exit_code=None, stderr_tail=str(exc))

        stderr_tail = _BoundedTail(_STDERR_TAIL_LIMIT)
        stderr_thread = threading.Thread(target=_drain_stderr, args=(process, stderr_tail), daemon=True)
        stderr_thread.start()

        cancelled = False
        deadline = time.monotonic() + timeout if timeout else None

        assert process.stdout is not None
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if deadline is not None and time.monotonic() > deadline:
                cancelled = True
                break
            key, _, value = line.strip().partition("=")
            if key == "out_time_ms" and progress_callback is not None:
                try:
                    processed = int(value) / 1_000_000.0
                except ValueError:
                    processed = None
                progress_callback(processed, duration_seconds)

        if cancelled:
            self._terminate(process)
        else:
            process.wait()

        stderr_thread.join(timeout=2)

        if cancelled:
            return FFmpegRunResult(success=False, exit_code=process.returncode, stderr_tail=stderr_tail.text(), cancelled=True)

        success = process.returncode == 0
        return FFmpegRunResult(success=success, exit_code=process.returncode, stderr_tail=stderr_tail.text())

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        """§74: graceful terminate -> bounded wait -> force kill -> reap.
        Never leaves a zombie process."""
        try:
            process.terminate()
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


class _BoundedTail:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: list[str] = []
        self._size = 0
        self._lock = threading.Lock()

    def add(self, chunk: str) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self._limit and len(self._chunks) > 1:
                dropped = self._chunks.pop(0)
                self._size -= len(dropped)

    def text(self) -> str:
        with self._lock:
            joined = "".join(self._chunks)
            return joined[-self._limit :]


def _drain_stderr(process: subprocess.Popen, tail: _BoundedTail) -> None:
    assert process.stderr is not None
    try:
        for line in process.stderr:
            tail.add(line)
    except (ValueError, OSError):
        pass


def probe_encoder_available(codec_name: str, *, ffmpeg_path: str = "ffmpeg", timeout: int = 10) -> bool:
    """§41/§42: preflight encoder availability -- never silently choose a
    different, unrelated codec when the required one is missing."""
    try:
        completed = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    return any(line.split()[1] == codec_name for line in completed.stdout.splitlines() if len(line.split()) > 1)
