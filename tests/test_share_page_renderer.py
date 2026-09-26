from datetime import datetime, timezone

from rychlik.share.contracts import ShareStatus
from rychlik.share.public_url_builder import PublicUrlBuilder
from rychlik.share.share_page_renderer import render_not_found_page, render_share_page
from rychlik.share.share_preview import MediaKind, SharePreview

URLS = PublicUrlBuilder("http://127.0.0.1:8080")
UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _preview(**overrides) -> SharePreview:
    defaults = dict(
        artifact_id="artifact-1",
        title="my video",
        description="Video • 1:00",
        media_kind=MediaKind.VIDEO,
        created_at=UTC_NOW,
        duration_seconds=60.0,
        width=1920,
        height=1080,
        thumbnail_path=None,
        thumbnail_mime=None,
    )
    defaults.update(overrides)
    return SharePreview(**defaults)


def test_active_video_renders_video_element():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(), urls=URLS, media_mime_type="video/mp4"
    )
    assert "<video" in html
    assert "http://127.0.0.1:8080/media/abc" in html


def test_active_without_thumbnail_omits_og_image_and_poster():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(thumbnail_path=None),
        urls=URLS, media_mime_type="video/mp4",
    )
    assert "og:image" not in html
    assert "poster=" not in html


def test_active_with_thumbnail_includes_og_image_and_poster():
    from pathlib import Path

    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc",
        preview=_preview(thumbnail_path=Path("/cache/x.jpg")), urls=URLS, media_mime_type="video/mp4",
    )
    assert 'property="og:image" content="http://127.0.0.1:8080/preview/abc"' in html
    assert 'poster="http://127.0.0.1:8080/preview/abc"' in html
    assert 'og:image:width" content="1920"' in html
    assert 'og:image:height" content="1080"' in html


def test_creating_shows_preparing_message_no_video_element():
    html = render_share_page(
        status=ShareStatus.CREATING, share_id="abc", preview=_preview(), urls=URLS, media_mime_type=None
    )
    assert "Preparing your share" in html
    assert "<video" not in html


def test_offline_message():
    html = render_share_page(
        status=ShareStatus.OFFLINE, share_id="abc", preview=_preview(), urls=URLS, media_mime_type=None
    )
    assert "sender is offline" in html


def test_revoked_message():
    html = render_share_page(
        status=ShareStatus.REVOKED, share_id="abc", preview=None, urls=URLS, media_mime_type=None
    )
    assert "has been revoked" in html


def test_expired_message():
    html = render_share_page(
        status=ShareStatus.EXPIRED, share_id="abc", preview=None, urls=URLS, media_mime_type=None
    )
    assert "has expired" in html


def test_failed_message():
    html = render_share_page(
        status=ShareStatus.FAILED, share_id="abc", preview=None, urls=URLS, media_mime_type=None
    )
    assert "unavailable" in html


def test_no_preview_falls_back_to_generic_title():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=None, urls=URLS, media_mime_type=None
    )
    assert "<title>Shared file</title>" in html


def test_robots_noindex_present():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(), urls=URLS, media_mime_type="video/mp4"
    )
    assert '<meta name="robots" content="noindex,nofollow">' in html


def test_og_title_description_url_present():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(), urls=URLS, media_mime_type="video/mp4"
    )
    assert 'property="og:title" content="my video"' in html
    assert 'property="og:description" content="Video • 1:00"' in html
    assert 'property="og:url" content="http://127.0.0.1:8080/s/abc"' in html


def test_unicode_title_renders():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc",
        preview=_preview(title="české slovenské video"), urls=URLS, media_mime_type="video/mp4",
    )
    assert "české slovenské video" in html


# --- HTML injection / escaping -----------------------------------------


def test_script_tag_title_is_escaped():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc",
        preview=_preview(title="<script>alert(1)</script>"), urls=URLS, media_mime_type="video/mp4",
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_attribute_breakout_title_is_escaped():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc",
        preview=_preview(title='"><img src=x onerror=alert(1)>'), urls=URLS, media_mime_type="video/mp4",
    )
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&quot;&gt;&lt;img" in html


def test_ampersand_in_title_is_escaped():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(title="A&B"),
        urls=URLS, media_mime_type="video/mp4",
    )
    assert "A&amp;B" in html
    assert "A&B" not in html


def test_quotes_in_description_are_escaped():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc",
        preview=_preview(description='"video" description'), urls=URLS, media_mime_type="video/mp4",
    )
    assert "&quot;video&quot;" in html


def test_no_local_path_or_source_url_ever_in_output():
    html = render_share_page(
        status=ShareStatus.ACTIVE, share_id="abc", preview=_preview(), urls=URLS, media_mime_type="video/mp4"
    )
    assert "/home/" not in html
    assert "source_url" not in html


# --- not found page -----------------------------------------------------


def test_not_found_page_is_generic():
    html = render_not_found_page()
    assert "does not exist" in html
    assert '<meta name="robots" content="noindex,nofollow">' in html
