import stat
import sys
import textwrap

import pytest

from rychlik.share.development_tunnel import (
    DevelopmentTunnelProvider,
    TunnelStartError,
    extract_public_url,
)


def _make_fake_cloudflared(tmp_path, script_body: str):
    path = tmp_path / "fake_cloudflared.py"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(script_body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# --- pure URL extraction -------------------------------------------------


def test_extract_public_url_from_realistic_cloudflared_output():
    line = "2026-09-26T10:00:00Z INF |  https://random-words-here.trycloudflare.com  |"
    assert extract_public_url(line) == "https://random-words-here.trycloudflare.com"


def test_extract_public_url_no_match():
    assert extract_public_url("2026-09-26T10:00:00Z INF Starting tunnel...") is None


def test_extract_public_url_ignores_other_https_urls():
    line = "2026-09-26T10:00:00Z INF connecting to https://api.cloudflare.com/some/path"
    assert extract_public_url(line) is None


# --- lifecycle against a fake cloudflared process (deterministic, no network) --


def test_start_picks_up_public_url(tmp_path):
    fake = _make_fake_cloudflared(
        tmp_path,
        """
        import time, sys
        print("INF Starting tunnel", flush=True)
        print("INF |  https://fake-tunnel-abc.trycloudflare.com  |", flush=True)
        time.sleep(5)
        """,
    )
    provider = DevelopmentTunnelProvider(local_url="http://127.0.0.1:9999", cloudflared_path=fake)
    try:
        url = provider.start(timeout=5.0)
        assert url == "https://fake-tunnel-abc.trycloudflare.com"
        assert provider.public_url == url
    finally:
        provider.stop()


def test_start_timeout_when_no_url_ever_printed(tmp_path):
    fake = _make_fake_cloudflared(
        tmp_path,
        """
        import time
        print("INF still trying...", flush=True)
        time.sleep(10)
        """,
    )
    provider = DevelopmentTunnelProvider(local_url="http://127.0.0.1:9999", cloudflared_path=fake)
    with pytest.raises(TunnelStartError):
        provider.start(timeout=0.5)
    assert provider.public_url is None


def test_double_start_is_idempotent(tmp_path):
    fake = _make_fake_cloudflared(
        tmp_path,
        """
        import time
        print("INF |  https://fake-tunnel-xyz.trycloudflare.com  |", flush=True)
        time.sleep(5)
        """,
    )
    provider = DevelopmentTunnelProvider(local_url="http://127.0.0.1:9999", cloudflared_path=fake)
    try:
        first = provider.start(timeout=5.0)
        second = provider.start(timeout=5.0)
        assert first == second
    finally:
        provider.stop()


def test_double_stop_is_safe(tmp_path):
    fake = _make_fake_cloudflared(
        tmp_path,
        """
        import time
        print("INF |  https://fake-tunnel-def.trycloudflare.com  |", flush=True)
        time.sleep(5)
        """,
    )
    provider = DevelopmentTunnelProvider(local_url="http://127.0.0.1:9999", cloudflared_path=fake)
    provider.start(timeout=5.0)
    provider.stop()
    provider.stop()  # must not raise
