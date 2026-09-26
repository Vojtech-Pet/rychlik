import pytest

from rychlik.core.artifact import Artifact, ArtifactError


def test_valid_completed_file(tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"fake mp4 bytes")

    artifact = Artifact.from_completed_download(file_path, source_url="https://example.com/v")

    assert artifact.filename == "video.mp4"
    assert artifact.mime_type == "video/mp4"
    assert artifact.size == len(b"fake mp4 bytes")
    assert artifact.source_url == "https://example.com/v"
    assert artifact.artifact_id


def test_missing_file_raises(tmp_path):
    missing = tmp_path / "missing.mp4"

    with pytest.raises(ArtifactError):
        Artifact.from_completed_download(missing)


def test_hash_consistency(tmp_path):
    file_path = tmp_path / "a.bin"
    file_path.write_bytes(b"same content")

    a1 = Artifact.from_completed_download(file_path)
    a2 = Artifact.from_completed_download(file_path)

    assert a1.sha256 == a2.sha256
    assert a1.artifact_id != a2.artifact_id  # identity is per-construction, not content-derived


def test_mime_detection_unknown_extension(tmp_path):
    file_path = tmp_path / "data.unknownext"
    file_path.write_bytes(b"x")

    artifact = Artifact.from_completed_download(file_path)

    assert artifact.mime_type == "application/octet-stream"


def test_metadata_mapping(tmp_path):
    file_path = tmp_path / "clip.webm"
    file_path.write_bytes(b"x")

    artifact = Artifact.from_completed_download(
        file_path, duration=12.5, metadata={"width": 1920, "height": 1080}
    )

    assert artifact.duration == 12.5
    assert artifact.metadata == {"width": 1920, "height": 1080}


def test_public_dict_excludes_private_fields(tmp_path):
    file_path = tmp_path / "secret.mp4"
    file_path.write_bytes(b"x")

    artifact = Artifact.from_completed_download(file_path, source_url="https://private.example/x")
    public = artifact.to_public_dict()

    assert "local_path" not in public
    assert "source_url" not in public
    assert public["filename"] == "secret.mp4"
