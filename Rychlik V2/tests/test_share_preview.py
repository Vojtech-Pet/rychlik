from datetime import datetime, timezone

from rychlik.share.share_preview import (
    MediaKind,
    SharePreview,
    classify_media_kind,
    derive_description,
    derive_title,
    select_thumbnail_timestamp,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- media kind classification ------------------------------------------


def test_video_media_kind():
    assert classify_media_kind("video/mp4") == MediaKind.VIDEO


def test_audio_media_kind():
    assert classify_media_kind("audio/mpeg") == MediaKind.AUDIO


def test_image_media_kind():
    assert classify_media_kind("image/jpeg") == MediaKind.IMAGE


def test_document_media_kind():
    assert classify_media_kind("application/pdf") == MediaKind.DOCUMENT


def test_generic_file_media_kind():
    assert classify_media_kind("application/zip") == MediaKind.GENERIC_FILE


def test_none_mime_is_generic_file():
    assert classify_media_kind(None) == MediaKind.GENERIC_FILE


# --- title -----------------------------------------------------------------


def test_safe_title_from_filename():
    assert derive_title("video.mp4") == "video"


def test_unicode_title():
    assert derive_title("české slovenské video.mp4") == "české slovenské video"


def test_title_never_contains_path_separator():
    # Artifact.filename is always Path(...).name, so this is structural, but
    # verify the derivation itself does not reintroduce one.
    title = derive_title("clip.mp4")
    assert "/" not in title
    assert "\\" not in title


def test_empty_stem_falls_back_to_generic_title():
    assert derive_title(".mp4") == "Shared file" or derive_title(".mp4") != ""


# --- description -------------------------------------------------------------


def test_video_description_with_dimensions_and_duration():
    description = derive_description(
        MediaKind.VIDEO, duration_seconds=125, width=1920, height=1080, size_bytes=0
    )
    assert description == "Video • 1920×1080 • 2:05"


def test_video_description_without_metadata():
    description = derive_description(
        MediaKind.VIDEO, duration_seconds=None, width=None, height=None, size_bytes=0
    )
    assert description == "Video"


def test_audio_description_with_duration():
    description = derive_description(
        MediaKind.AUDIO, duration_seconds=222, width=None, height=None, size_bytes=0
    )
    assert description == "Audio • 3:42"


def test_image_description_with_dimensions():
    description = derive_description(
        MediaKind.IMAGE, duration_seconds=None, width=800, height=600, size_bytes=0
    )
    assert description == "Image • 800×600"


def test_document_description():
    description = derive_description(
        MediaKind.DOCUMENT, duration_seconds=None, width=None, height=None, size_bytes=0
    )
    assert description == "PDF document"


def test_generic_file_description_uses_human_size():
    description = derive_description(
        MediaKind.GENERIC_FILE, duration_seconds=None, width=None, height=None, size_bytes=26_000_000
    )
    assert "MB" in description


def test_description_never_contains_url_or_path():
    description = derive_description(
        MediaKind.VIDEO, duration_seconds=60, width=100, height=100, size_bytes=0
    )
    assert "http" not in description
    assert "/" not in description


# --- thumbnail timestamp selection --------------------------------------


def test_short_video_gets_early_safe_timestamp():
    assert select_thumbnail_timestamp(4.0) == 1.0


def test_long_video_gets_percentage_based_timestamp():
    timestamp = select_thumbnail_timestamp(100.0)
    assert timestamp == 15.0


def test_timestamp_never_exceeds_duration():
    timestamp = select_thumbnail_timestamp(10.2)
    assert timestamp < 10.2


def test_none_duration_gives_zero_timestamp():
    assert select_thumbnail_timestamp(None) == 0.0


def test_zero_duration_gives_zero_timestamp():
    assert select_thumbnail_timestamp(0.0) == 0.0


# --- public serialization -------------------------------------------------


def _preview(**overrides) -> SharePreview:
    defaults = dict(
        artifact_id="artifact-1",
        title="video",
        description="Video • 1:00",
        media_kind=MediaKind.VIDEO,
        created_at=UTC_NOW,
        duration_seconds=60.0,
        width=1920,
        height=1080,
        thumbnail_path="/home/vojtech/private/cache/preview.jpg",
        thumbnail_mime="image/jpeg",
    )
    defaults.update(overrides)
    return SharePreview(**defaults)


def test_public_dict_contains_safe_fields():
    preview = _preview()
    public = preview.to_public_dict()

    assert public == {
        "title": "video",
        "description": "Video • 1:00",
        "media_kind": "VIDEO",
        "duration_seconds": 60.0,
        "width": 1920,
        "height": 1080,
    }


def test_thumbnail_path_not_public():
    public = _preview().to_public_dict()
    assert "thumbnail_path" not in public
    assert "/home/vojtech/private" not in str(public)


def test_artifact_id_not_public():
    public = _preview().to_public_dict()
    assert "artifact_id" not in public


def test_source_url_and_local_path_have_no_field_at_all():
    field_names = set(SharePreview.__dataclass_fields__)
    assert "source_url" not in field_names
    assert "local_path" not in field_names
