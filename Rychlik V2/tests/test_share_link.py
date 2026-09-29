from datetime import datetime, timedelta, timezone

import pytest

from rychlik.share.contracts import ShareStatus
from rychlik.share.share_link import (
    InvalidShareLinkTransition,
    ShareLink,
    TERMINAL_STATUSES,
    generate_share_id,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _link(**overrides) -> ShareLink:
    defaults = dict(share_id="abc123", artifact_id="artifact-1", created_at=UTC_NOW)
    defaults.update(overrides)
    return ShareLink(**defaults)


# --- share_id generation -----------------------------------------------


def test_generated_share_id_has_sufficient_entropy():
    share_id = generate_share_id()
    # token_urlsafe(16) -> 22 base64url chars encoding 128 bits
    assert len(share_id) >= 20


def test_generated_share_ids_are_unique_across_large_sample():
    ids = {generate_share_id() for _ in range(2000)}
    assert len(ids) == 2000


def test_generated_share_id_unrelated_to_artifact_filename():
    filename = "my-private-video.mp4"
    share_id = generate_share_id()
    assert filename not in share_id
    assert "private" not in share_id.lower()


# --- construction validation --------------------------------------------


def test_empty_share_id_rejected():
    with pytest.raises(ValueError):
        ShareLink(share_id="", artifact_id="a", created_at=UTC_NOW)


def test_empty_artifact_id_rejected():
    with pytest.raises(ValueError):
        ShareLink(share_id="s", artifact_id="", created_at=UTC_NOW)


def test_naive_created_at_rejected():
    with pytest.raises(ValueError):
        ShareLink(share_id="s", artifact_id="a", created_at=datetime(2026, 1, 1))


def test_naive_expires_at_rejected():
    with pytest.raises(ValueError):
        _link(expires_at=datetime(2026, 1, 2))


def test_default_status_is_creating():
    assert _link().status == ShareStatus.CREATING


# --- state transitions ----------------------------------------------------


def test_creating_to_active():
    link = _link()
    updated = link.transition_to(ShareStatus.ACTIVE, now=UTC_NOW)
    assert updated.status == ShareStatus.ACTIVE
    assert link.status == ShareStatus.CREATING  # original untouched (immutable)


def test_creating_to_failed():
    link = _link()
    updated = link.transition_to(ShareStatus.FAILED, now=UTC_NOW)
    assert updated.status == ShareStatus.FAILED


def test_active_to_offline():
    link = _link(status=ShareStatus.ACTIVE)
    assert link.transition_to(ShareStatus.OFFLINE, now=UTC_NOW).status == ShareStatus.OFFLINE


def test_offline_to_active():
    link = _link(status=ShareStatus.OFFLINE)
    assert link.transition_to(ShareStatus.ACTIVE, now=UTC_NOW).status == ShareStatus.ACTIVE


def test_active_to_revoked():
    link = _link(status=ShareStatus.ACTIVE)
    assert link.transition_to(ShareStatus.REVOKED, now=UTC_NOW).status == ShareStatus.REVOKED


def test_active_to_expired():
    link = _link(status=ShareStatus.ACTIVE)
    assert link.transition_to(ShareStatus.EXPIRED, now=UTC_NOW).status == ShareStatus.EXPIRED


def test_invalid_transition_rejected():
    link = _link()  # CREATING
    with pytest.raises(InvalidShareLinkTransition):
        link.transition_to(ShareStatus.OFFLINE, now=UTC_NOW)


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES, key=lambda s: s.name))
def test_terminal_states_have_no_outgoing_transitions(terminal_status):
    link = _link(status=terminal_status)
    for candidate in ShareStatus:
        with pytest.raises(InvalidShareLinkTransition):
            link.transition_to(candidate, now=UTC_NOW)


def test_is_terminal():
    assert _link(status=ShareStatus.REVOKED).is_terminal()
    assert not _link(status=ShareStatus.ACTIVE).is_terminal()


# --- expiry -----------------------------------------------------------


def test_no_expiry_never_expired():
    link = _link(expires_at=None)
    far_future = UTC_NOW + timedelta(days=3650)
    assert not link.is_expired(far_future)


def test_expiry_evaluation_before_and_after():
    expires_at = UTC_NOW + timedelta(hours=1)
    link = _link(expires_at=expires_at)

    assert not link.is_expired(UTC_NOW)
    assert link.is_expired(expires_at)
    assert link.is_expired(expires_at + timedelta(seconds=1))


# --- serialization safety ----------------------------------------------


def test_public_dict_exposes_only_safe_fields():
    link = _link(
        status=ShareStatus.ACTIVE,
        public_url="https://s.example/abc123",
        secret="top-secret",
    )

    public = link.to_public_dict()

    assert public == {
        "share_id": "abc123",
        "status": "ACTIVE",
        "public_url": "https://s.example/abc123",
        "expires_at": None,
    }
    assert "secret" not in public
    assert "artifact_id" not in public


def test_secret_not_exposed_in_repr():
    link = _link(secret="top-secret-value")
    assert "top-secret-value" not in repr(link)


def test_sharelink_never_holds_artifact_local_state():
    # Structural guarantee: ShareLink only ever stores artifact_id (an opaque
    # reference), never an Artifact/local_path/source_url, so there is no
    # leak surface to test against beyond field presence.
    link = _link()
    field_names = {f for f in link.__dataclass_fields__}
    assert "local_path" not in field_names
    assert "source_url" not in field_names
