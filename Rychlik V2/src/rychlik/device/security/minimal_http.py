"""A tiny, deliberately manual HTTP/1.1 client over an already-connected
socket (Prompt A15).

`requests`/urllib3 give up direct control of exactly when the TLS
handshake happens relative to sending any request bytes, and Prompt A14
already found a real interoperability bug in `requests`' handling of a
generator request body (see git history / A14 result doc) -- for the
security-critical secure transport, this module instead does the
smallest amount of raw HTTP/1.1 framing needed, over a socket this
module's caller has ALREADY TLS-connected and pin-verified. This keeps
"verify the pin before any payload byte" trivially true by construction:
nothing here can send anything until the caller already has a live,
pin-checked socket in hand.
"""

from __future__ import annotations

import json
from typing import IO


def send_request(sock, method: str, path: str, headers: dict[str, str], body: bytes | None = None) -> None:
    lines = [f"{method} {path} HTTP/1.1", "Host: friendsend"]
    for key, value in headers.items():
        lines.append(f"{key}: {value}")
    if body is not None and "Content-Length" not in headers:
        lines.append(f"Content-Length: {len(body)}")
    lines.append("")
    lines.append("")
    sock.sendall("\r\n".join(lines).encode("ascii"))
    if body:
        sock.sendall(body)


def send_request_head_only(sock, method: str, path: str, headers: dict[str, str]) -> None:
    """Sends the request line + headers only -- the caller streams the
    body itself afterward via repeated `sock.sendall(chunk)` calls."""
    lines = [f"{method} {path} HTTP/1.1", "Host: friendsend"]
    for key, value in headers.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("")
    sock.sendall("\r\n".join(lines).encode("ascii"))


def read_response(reader: IO[bytes]) -> tuple[int, dict[str, str], bytes]:
    status_line = reader.readline().decode("ascii", errors="replace").strip()
    if not status_line:
        raise ConnectionError("empty response from peer")
    parts = status_line.split(" ", 2)
    status = int(parts[1])

    headers: dict[str, str] = {}
    while True:
        line = reader.readline().decode("ascii", errors="replace").strip()
        if not line:
            break
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()

    length = int(headers.get("content-length", "0"))
    body = reader.read(length) if length else b""
    return status, headers, body


def read_json_response(reader: IO[bytes]) -> tuple[int, dict]:
    status, _headers, body = read_response(reader)
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {}
    return status, parsed
