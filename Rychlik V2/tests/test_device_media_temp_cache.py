"""Prompt A16: DeviceMediaTempCache -- TTL cleanup, startup sweep,
path-traversal safety, and ownership-safe cleanup (§53-57/§86-89/
§135-137)."""

from __future__ import annotations

from rychlik.device.media.temp_cache import DeviceMediaTempCache


def test_allocate_never_uses_untrusted_filename(tmp_path):
    cache = DeviceMediaTempCache(tmp_path / "cache")
    directory = cache.allocate_directory("../../evil")
    assert directory.parent == tmp_path / "cache"
    assert ".." not in str(directory.relative_to(tmp_path / "cache"))


def test_discard_removes_the_directory(tmp_path):
    cache = DeviceMediaTempCache(tmp_path / "cache")
    directory = cache.allocate_directory("p1")
    (directory / "output.mp4").write_bytes(b"data")
    cache.discard("p1")
    assert not directory.exists()


def test_cleanup_expired_uses_injected_clock_no_real_waiting(tmp_path):
    now = [1000.0]
    cache = DeviceMediaTempCache(tmp_path / "cache", ttl_seconds=3600, clock=lambda: now[0])
    directory = cache.allocate_directory("p1")
    cache.mark_retained("p1")

    now[0] += 1800  # within TTL
    assert cache.cleanup_expired() == []
    assert directory.exists()

    now[0] += 3600  # past TTL
    assert cache.cleanup_expired() == ["p1"]
    assert not directory.exists()


def test_sweep_untracked_on_startup_removes_leftovers(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(parents=True)
    stale = root / "leftover-from-crash"
    stale.mkdir()
    (stale / "partial.mp4").write_bytes(b"x")

    cache = DeviceMediaTempCache(root)
    cache.sweep_untracked_on_startup()
    assert not stale.exists()
    assert root.exists()  # the cache root itself is never removed


def test_cleanup_never_deletes_outside_its_own_root(tmp_path):
    outside = tmp_path / "unrelated_important_file.txt"
    outside.write_text("do not delete me")
    cache = DeviceMediaTempCache(tmp_path / "cache")

    # Simulate a corrupt/forged record pointing outside the cache root.
    cache._entries["forged"] = cache._entries.get("forged")  # no-op, documents intent
    from rychlik.device.media.temp_cache import _Entry

    cache._entries["forged"] = _Entry(directory=outside.parent, created_at=0.0)
    cache.discard("forged")

    assert outside.exists()  # never touched -- outside.parent == tmp_path, not inside cache root
