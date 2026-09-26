"""Prompt A9: real (non-mock) HTTP tests for DirectHttpAcquisition's pause
and validated-resume mechanics, using the extended /resumable/<key> fixture
route."""

import hashlib
import threading
import time

import pytest

from rychlik.acquisition.contracts import (
    AcquisitionError,
    DownloadCancelled,
    DownloadPaused,
    DownloadRequest,
    ResumeRequest,
)
from rychlik.acquisition.direct_http import DirectHttpAcquisition
from rychlik.core.partial_transfer import PartialTransferState, ValidatorKind
from http_fixture_server import NORMAL_BODY

STRONG_ETAG = '"v1-strong"'
LAST_MOD = "Mon, 01 Jan 2026 00:00:00 GMT"

# requests.iter_content(chunk_size=64KiB) reads (blocking) until it fills a
# full 64KiB chunk or the stream ends (a real, previously-documented
# property of the existing acquisition chunk size -- see A7's RESULT doc):
# a body smaller than 64KiB arrives in one shot. A pause/checkpoint test
# needs a body well over 64KiB, streamed in large-enough sub-writes with a
# short delay, so a pause/cancel request genuinely lands mid-stream across
# several real 64KiB chunks within a fraction of a second.
BIG_BODY = NORMAL_BODY * 30  # ~510 KB
_SLOW_KWARGS = dict(slow=True, slow_chunk_bytes=16384, slow_delay=0.02)


def _resume_request(key, *, initial_partial=None, threshold=4096):
    saved = {}
    cleared = {"count": 0}

    def save(partial):
        saved["partial"] = partial

    def clear():
        cleared["count"] += 1

    req = ResumeRequest(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count=1,
        initial_partial=initial_partial,
        save_partial=save,
        clear_partial=clear,
        checkpoint_bytes_threshold=threshold,
    )
    return req, saved, cleared


def _partial_for(tmp_path, *, durable_bytes, body_prefix, validator_kind=ValidatorKind.STRONG_ETAG, validator_value=STRONG_ETAG, attempt=1, total=None):
    from datetime import datetime, timezone

    part = tmp_path / "resumable.part"
    part.write_bytes(body_prefix)
    return PartialTransferState(
        queue_entry_id="qe-A",
        task_id="A",
        attempt_count_snapshot=attempt,
        temp_path=part,
        final_path=tmp_path / "resumable",
        durable_bytes=durable_bytes,
        expected_total_bytes=total,
        validator_kind=validator_kind,
        validator_value=validator_value,
        prefix_sha256=hashlib.sha256(body_prefix).hexdigest(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- pause -------------------------------------------------------------


def test_pause_stops_transfer_and_checkpoints_partial(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("pause1", etag=STRONG_ETAG, body=BIG_BODY, **_SLOW_KWARGS)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/pause1", destination_dir=tmp_path, filename_hint="resumable")
    pause_event = threading.Event()
    resume, saved, cleared = _resume_request("pause1")

    def _pause_soon():
        time.sleep(0.15)
        pause_event.set()

    threading.Thread(target=_pause_soon).start()

    with pytest.raises(DownloadPaused):
        DirectHttpAcquisition().acquire(request, pause_event=pause_event, resume=resume)

    assert (tmp_path / "resumable.part").exists()
    assert "partial" in saved
    partial = saved["partial"]
    assert 0 < partial.durable_bytes < len(BIG_BODY)
    assert cleared["count"] == 0  # never cleared on pause
    on_disk_prefix = (tmp_path / "resumable.part").read_bytes()[: partial.durable_bytes]
    assert hashlib.sha256(on_disk_prefix).hexdigest() == partial.prefix_sha256


def test_cancel_wins_and_clears_partial(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("pause2", etag=STRONG_ETAG, body=BIG_BODY, **_SLOW_KWARGS)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/pause2", destination_dir=tmp_path, filename_hint="resumable")
    pause_event = threading.Event()
    cancel_event = threading.Event()
    resume, saved, cleared = _resume_request("pause2")

    def _both_soon():
        time.sleep(0.15)
        cancel_event.set()
        pause_event.set()

    threading.Thread(target=_both_soon).start()

    with pytest.raises(DownloadCancelled):
        DirectHttpAcquisition().acquire(
            request, pause_event=pause_event, cancel_event=cancel_event, resume=resume
        )
    assert not (tmp_path / "resumable.part").exists()
    assert cleared["count"] == 1


# --- valid resume --------------------------------------------------------


def test_valid_resume_appends_only_missing_bytes(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("resume1", etag=STRONG_ETAG)
    offset = 5000
    partial = _partial_for(tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset])
    resume, saved, cleared = _resume_request("resume1", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/resume1", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)

    assert completed.final_path.read_bytes() == NORMAL_BODY
    last_req = http_fixture_server.resumable_last_request("resume1")
    assert last_req["range"] == f"bytes={offset}-"
    assert last_req["if_range"] == STRONG_ETAG
    assert cleared["count"] == 1  # cleared on success


def test_valid_resume_via_last_modified(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("resume2", last_modified=LAST_MOD)
    offset = 3000
    partial = _partial_for(
        tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset],
        validator_kind=ValidatorKind.LAST_MODIFIED, validator_value=LAST_MOD,
    )
    resume, saved, cleared = _resume_request("resume2", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/resume2", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY


def test_resume_progress_reports_cumulative_bytes(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("resume3", etag=STRONG_ETAG)
    offset = 5000
    partial = _partial_for(tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset])
    resume, saved, cleared = _resume_request("resume3", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/resume3", destination_dir=tmp_path, filename_hint="resumable")

    seen = []
    DirectHttpAcquisition().acquire(request, resume=resume, progress_callback=lambda b, t=None: seen.append(b))
    assert all(b >= offset for b in seen)
    assert seen[-1] == len(NORMAL_BODY)


def test_checkpoint_threshold_triggers_periodic_save(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("resume4", etag=STRONG_ETAG, body=BIG_BODY, **_SLOW_KWARGS)
    resume, saved, cleared = _resume_request("resume4", threshold=2000)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/resume4", destination_dir=tmp_path, filename_hint="resumable")

    class _Recorder:
        checkpoints = []

    def save(partial):
        _Recorder.checkpoints.append(partial.durable_bytes)

    resume = ResumeRequest(
        queue_entry_id="qe-A", task_id="A", attempt_count=1, initial_partial=None,
        save_partial=save, clear_partial=lambda: None, checkpoint_bytes_threshold=2000,
    )
    DirectHttpAcquisition().acquire(request, resume=resume)
    assert len(_Recorder.checkpoints) >= 2  # multiple periodic checkpoints during the ~510KB slow transfer


# --- unsafe resume fallbacks (all must produce a byte-exact final file) --


def test_no_validator_forces_full_restart(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("noval", etag=None, last_modified=None)
    offset = 5000
    partial = _partial_for(
        tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset],
        validator_kind=ValidatorKind.NONE, validator_value=None,
    )
    resume, saved, cleared = _resume_request("noval", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/noval", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY
    last_req = http_fixture_server.resumable_last_request("noval")
    assert last_req["range"] is None  # no Range attempted at all -- no validator to send If-Range with


def test_server_ignores_range_falls_back_to_full_restart(http_fixture_server, tmp_path):
    # etag configured differently from what the partial remembers ->
    # server's If-Range comparison fails -> 200 with full body.
    http_fixture_server.configure_resumable("changed1", etag='"v2-new"')
    offset = 5000
    partial = _partial_for(tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset], validator_value='"v1-old"')
    resume, saved, cleared = _resume_request("changed1", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/changed1", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY  # never old+new concatenated


def test_wrong_206_start_falls_back_to_full_restart(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("badrange", etag=STRONG_ETAG, force_bad_206=True)
    offset = 5000
    partial = _partial_for(tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset])
    resume, saved, cleared = _resume_request("badrange", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/badrange", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY


def test_416_falls_back_to_full_restart(http_fixture_server, tmp_path):
    http_fixture_server.configure_resumable("range416", etag=STRONG_ETAG, force_status=416)
    offset = 5000
    partial = _partial_for(tmp_path, durable_bytes=offset, body_prefix=NORMAL_BODY[:offset])
    resume, saved, cleared = _resume_request("range416", initial_partial=partial)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/range416", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY


def test_locally_corrupt_partial_never_reaches_network_range(tmp_path, http_fixture_server):
    """Local corruption is caught by plan_resume()/validate_local_partial()
    BEFORE a ResumeRequest is even built (that's the coordinator's job in
    the real pipeline) -- this test proves the contract at the boundary:
    passing initial_partial=None (as the coordinator would after local
    validation fails) never attempts Range at all."""
    http_fixture_server.configure_resumable("corrupt1", etag=STRONG_ETAG)
    resume, saved, cleared = _resume_request("corrupt1", initial_partial=None)
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/resumable/corrupt1", destination_dir=tmp_path, filename_hint="resumable")

    completed = DirectHttpAcquisition().acquire(request, resume=resume)
    assert completed.final_path.read_bytes() == NORMAL_BODY
    last_req = http_fixture_server.resumable_last_request("corrupt1")
    assert last_req["range"] is None
