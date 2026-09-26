"""DevelopmentTunnelProvider (Prompt 12): experiment-only Cloudflare Quick
Tunnel adapter. Not production infrastructure.

Deliberately NOT a multi-provider abstraction (no TunnelRegistry, no
CloudflareAdapter/TailscaleAdapter/NgrokAdapter hierarchy) — a single
concrete class for one provider, until real usage data justifies more.
Tailscale Funnel is the documented fallback if Cloudflare proves unusable,
not something built speculatively now.

Cloudflare's own docs describe Quick Tunnel as intended for development/
testing, not production (see docs/LINK_TUNNEL_EXPERIMENT.md).
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

_PUBLIC_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelStartError(Exception):
    """Raised when the tunnel process fails to report a public URL in time."""


class DevelopmentTunnelProvider:
    """Wraps `cloudflared tunnel --url <local_url>`. One tunnel per instance."""

    def __init__(self, *, local_url: str, cloudflared_path: str = "cloudflared") -> None:
        self._local_url = local_url
        self._cloudflared_path = cloudflared_path
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._public_url: str | None = None
        self._url_found = threading.Event()
        self._output_lines: list[str] = []
        self._lock = threading.Lock()

    @property
    def public_url(self) -> str | None:
        return self._public_url

    def start(self, *, timeout: float = 30.0) -> str:
        with self._lock:
            if self._process is not None:
                return self._public_url  # idempotent

            self._process = subprocess.Popen(
                [self._cloudflared_path, "tunnel", "--url", self._local_url, "--no-autoupdate"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

        found = self._url_found.wait(timeout=timeout)
        if not found:
            self.stop()
            raise TunnelStartError(
                f"cloudflared did not report a public URL within {timeout}s; "
                f"last output:\n" + "\n".join(self._output_lines[-20:])
            )
        return self._public_url

    def _read_output(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._output_lines.append(line.rstrip())
            if self._public_url is None:
                match = _PUBLIC_URL_RE.search(line)
                if match:
                    self._public_url = match.group(0)
                    self._url_found.set()

    def stop(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return  # idempotent
            self._process = None

        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)


def extract_public_url(line: str) -> str | None:
    """Pure helper, unit-testable without spawning a real process."""
    match = _PUBLIC_URL_RE.search(line)
    return match.group(0) if match else None
