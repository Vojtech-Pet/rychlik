import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rychlik.core.partial_transfer import (
    PartialTransferConsistencyError,
    PartialTransferState,
    ResumeDecisionKind,
    Validator,
    ValidatorKind,
    classify_validator,
    plan_resume,
    validate_local_partial,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _partial(tmp_path, *, durable_bytes, prefix_sha256, validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"abc"', attempt_count_snapshot=1, expected_total_bytes=100):
    return PartialTransferState(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count_snapshot=attempt_count_snapshot,
        temp_path=tmp_path / "video.mp4.part",
        final_path=tmp_path / "video.mp4",
        durable_bytes=durable_bytes,
        expected_total_bytes=expected_total_bytes,
        validator_kind=validator_kind,
        validator_value=validator_value,
        prefix_sha256=prefix_sha256,
        created_at=T0,
        updated_at=T0,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- validator classification -------------------------------------------


def test_strong_etag_preferred():
    v = classify_validator(etag='"abc123"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
    assert v.kind == ValidatorKind.STRONG_ETAG
    assert v.value == '"abc123"'


def test_weak_etag_is_not_strong():
    v = classify_validator(etag='W/"abc123"', last_modified=None)
    assert v.kind == ValidatorKind.NONE


def test_weak_etag_falls_back_to_last_modified():
    v = classify_validator(etag='W/"abc123"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
    assert v.kind == ValidatorKind.LAST_MODIFIED


def test_no_validator_at_all():
    v = classify_validator(etag=None, last_modified=None)
    assert v.kind == ValidatorKind.NONE
    assert v.if_range_header is None


def test_validator_none_rejects_nonempty_value():
    with pytest.raises(ValueError):
        Validator(ValidatorKind.NONE, "x")


def test_validator_non_none_requires_value():
    with pytest.raises(ValueError):
        Validator(ValidatorKind.STRONG_ETAG, None)


# --- local validation: ownership -----------------------------------------


def test_path_ownership_rejects_outside_destination(tmp_path):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    outside_file = other_dir / "victim.txt"
    outside_file.write_bytes(b"do not touch me")

    dest = tmp_path / "dest"
    dest.mkdir()
    partial = PartialTransferState(
        queue_entry_id="qe-A", task_id="A", attempt_count_snapshot=1,
        temp_path=outside_file, final_path=dest / "video.mp4",
        durable_bytes=5, expected_total_bytes=None,
        validator_kind=ValidatorKind.STRONG_ETAG, validator_value='"x"',
        prefix_sha256=_sha256(b"do no"), created_at=T0, updated_at=T0,
    )
    with pytest.raises(PartialTransferConsistencyError):
        validate_local_partial(partial, expected_dir=dest)
    assert outside_file.read_bytes() == b"do not touch me"  # untouched


def test_path_ownership_rejects_wrong_suffix(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    wrong = dest / "video.mp4"  # missing .part suffix
    wrong.write_bytes(b"hello")
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    object.__setattr__(partial, "temp_path", wrong)
    with pytest.raises(PartialTransferConsistencyError):
        validate_local_partial(partial, expected_dir=dest)


def test_path_ownership_rejects_symlink_escape(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"top secret")
    link = dest / "video.mp4.part"
    link.symlink_to(secret)

    partial = _partial(dest, durable_bytes=4, prefix_sha256=_sha256(b"top "))
    with pytest.raises(PartialTransferConsistencyError):
        validate_local_partial(partial, expected_dir=dest)
    assert secret.read_bytes() == b"top secret"


# --- local validation: size/hash -----------------------------------------


def test_missing_partial_file_is_invalid(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    is_valid, reason = validate_local_partial(partial, expected_dir=dest)
    assert is_valid is False
    assert "does not exist" in reason


def test_shorter_than_durable_bytes_is_invalid(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hell")  # 4 bytes, need 5
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    is_valid, reason = validate_local_partial(partial, expected_dir=dest)
    assert is_valid is False
    assert "too short" in reason


def test_larger_than_durable_bytes_is_truncated_then_validated(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hello WORLD EXTRA")  # extra crash-window bytes
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    is_valid, reason = validate_local_partial(partial, expected_dir=dest)
    assert is_valid is True
    assert (dest / "video.mp4.part").stat().st_size == 5
    assert (dest / "video.mp4.part").read_bytes() == b"hello"


def test_prefix_hash_mismatch_is_invalid(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hellX")  # corrupted last byte
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    is_valid, reason = validate_local_partial(partial, expected_dir=dest)
    assert is_valid is False
    assert "mismatch" in reason


def test_exact_match_is_valid(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hello")
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    is_valid, reason = validate_local_partial(partial, expected_dir=dest)
    assert is_valid is True


# --- plan_resume: pure decision ------------------------------------------


def test_plan_resume_no_partial():
    decision = plan_resume(None, expected_dir=Path("/tmp"), task_attempt_count=1)
    assert decision.kind == ResumeDecisionKind.NO_PARTIAL


def test_plan_resume_valid_partial_attempts_range(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hello")
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"), attempt_count_snapshot=1)
    decision = plan_resume(partial, expected_dir=dest, task_attempt_count=2)
    assert decision.kind == ResumeDecisionKind.ATTEMPT_RANGE
    assert decision.offset == 5
    assert decision.validator.kind == ValidatorKind.STRONG_ETAG


def test_plan_resume_no_validator_forces_full_restart(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hello")
    partial = _partial(
        dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"), validator_kind=ValidatorKind.NONE, validator_value=None
    )
    decision = plan_resume(partial, expected_dir=dest, task_attempt_count=2)
    assert decision.kind == ResumeDecisionKind.FULL_RESTART
    assert "validator" in decision.reason


def test_plan_resume_newer_generation_forces_full_restart(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"hello")
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"), attempt_count_snapshot=5)
    decision = plan_resume(partial, expected_dir=dest, task_attempt_count=2)  # older/current task generation
    assert decision.kind == ResumeDecisionKind.FULL_RESTART
    assert "generation" in decision.reason


def test_plan_resume_invalid_local_state_forces_full_restart(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "video.mp4.part").write_bytes(b"XXXXX")  # wrong content
    partial = _partial(dest, durable_bytes=5, prefix_sha256=_sha256(b"hello"))
    decision = plan_resume(partial, expected_dir=dest, task_attempt_count=2)
    assert decision.kind == ResumeDecisionKind.FULL_RESTART


# --- structural import test ------------------------------------------------


def test_module_has_no_forbidden_imports():
    import ast

    import rychlik.core.partial_transfer as module

    forbidden = {"PySide6", "requests", "sqlite3", "threading"}
    with open(module.__file__) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    top_level = {name.split(".")[0] for name in imported}
    assert top_level & forbidden == set()
