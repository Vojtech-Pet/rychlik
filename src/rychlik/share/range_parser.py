"""Pure single-range HTTP Range header parser (Prompt 07).

Documented policy (see docs/LOCAL_SHARE_ORIGIN_RESULT.md for the full table):

- No Range header               -> NONE   (serve full content, 200)
- Syntactically invalid Range   -> IGNORED (serve full content, 200; RFC 7233
  permits ignoring a malformed Range header rather than rejecting the request)
- Multiple ranges (comma list)  -> IGNORED (multipart ranges are explicitly
  out of scope for this phase; served as a full 200 response)
- start > end, start >= size,
  zero-length suffix, or Range
  requested against a 0-byte file -> UNSATISFIABLE (416)
- Otherwise                      -> SINGLE(start, end) inclusive, end clamped
  to size - 1 if the client asked for more bytes than exist (this is a
  normal, RFC-permitted clamp, not a silent fix of an invalid range)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

_MAX_DIGITS = 19  # guards against parsing pathologically long digit strings


class RangeOutcome(Enum):
    NONE = auto()
    IGNORED = auto()
    SINGLE = auto()
    UNSATISFIABLE = auto()


@dataclass(frozen=True)
class ParsedRange:
    outcome: RangeOutcome
    start: int | None = None
    end: int | None = None  # inclusive


_NONE = ParsedRange(RangeOutcome.NONE)
_IGNORED = ParsedRange(RangeOutcome.IGNORED)
_UNSATISFIABLE = ParsedRange(RangeOutcome.UNSATISFIABLE)


def parse_range(header_value: str | None, size: int) -> ParsedRange:
    if header_value is None:
        return _NONE

    value = header_value.strip()
    if "=" not in value:
        return _IGNORED

    unit, _, spec = value.partition("=")
    if unit.strip().lower() != "bytes":
        return _IGNORED

    if "," in spec:
        return _IGNORED  # multi-range: unsupported, treated as unsupported/ignored

    spec = spec.strip()
    if not spec:
        return _IGNORED

    if spec.startswith("-"):
        # suffix range: bytes=-N (last N bytes)
        digits = spec[1:]
        if not _is_plain_digits(digits):
            return _IGNORED
        suffix_len = int(digits)
        if suffix_len == 0 or size == 0:
            return _UNSATISFIABLE
        start = max(0, size - suffix_len)
        end = size - 1
        return ParsedRange(RangeOutcome.SINGLE, start, end)

    start_str, sep, end_str = spec.partition("-")
    if not sep:
        return _IGNORED  # no '-' at all, e.g. "bytes=100"

    if not _is_plain_digits(start_str):
        return _IGNORED

    start = int(start_str)

    if end_str == "":
        # open-ended range: bytes=N-
        if size == 0 or start >= size:
            return _UNSATISFIABLE
        return ParsedRange(RangeOutcome.SINGLE, start, size - 1)

    if not _is_plain_digits(end_str):
        return _IGNORED

    end = int(end_str)

    if start > end:
        return _UNSATISFIABLE
    if size == 0 or start >= size:
        return _UNSATISFIABLE

    end = min(end, size - 1)  # RFC-permitted clamp of an over-long (but valid) range
    return ParsedRange(RangeOutcome.SINGLE, start, end)


def _is_plain_digits(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit() and len(value) <= _MAX_DIGITS
