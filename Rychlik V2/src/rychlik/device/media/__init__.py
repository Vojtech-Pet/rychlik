"""Device Mode automatic media compatibility (Prompt A16).

See docs/DEVICE_MEDIA_COMPATIBILITY.md for the normative design. This
package is deliberately independent of `rychlik.device.security`
(transport/pin/auth) and of `rychlik.share` (Share by Link preview
generation, audited but not reused/extended here -- see the audit note
in docs/DEVICE_MEDIA_COMPATIBILITY.md for why a purpose-built probe was
written instead of extending `rychlik.share.media_probe`, whose
`ProbeResult` is duration/width/height only and insufficient for
codec-level compatibility decisions).
"""
