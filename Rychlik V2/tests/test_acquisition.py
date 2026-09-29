import threading
import time

import pytest

from rychlik.acquisition.acquisition_service import AcquisitionService
from rychlik.acquisition.contracts import AcquisitionError, DownloadCancelled, DownloadRequest
from rychlik.core.artifact import Artifact
from http_fixture_server import NORMAL_BODY


def _service():
    return AcquisitionService()


def test_download_request_rejects_empty_url(tmp_path):
    with pytest.raises(ValueError):
        DownloadRequest(url="", destination_dir=tmp_path)


def test_download_request_rejects_non_http_scheme(tmp_path):
    with pytest.raises(ValueError):
        DownloadRequest(url="ftp://example.com/file", destination_dir=tmp_path)


def test_successful_http_acquisition(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path)

    completed = _service().acquire(request)

    assert completed.final_path.exists()
    assert completed.final_path.read_bytes() == NORMAL_BODY
    assert completed.size == len(NORMAL_BODY)
    assert completed.display_name == "normal.mp4"
    assert not completed.final_path.with_name(completed.final_path.name + ".part").exists()


def test_redirect_is_followed(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/redirect", destination_dir=tmp_path)

    completed = _service().acquire(request)

    assert completed.final_path.read_bytes() == NORMAL_BODY


def test_no_part_file_left_behind_on_success(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path)

    _service().acquire(request)

    leftovers = list(tmp_path.glob("*.part"))
    assert leftovers == []


def test_cancel_does_not_produce_completed_download(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/slow", destination_dir=tmp_path)
    cancel_event = threading.Event()

    def _cancel_soon():
        time.sleep(0.1)
        cancel_event.set()

    threading.Thread(target=_cancel_soon).start()

    with pytest.raises(DownloadCancelled):
        _service().acquire(request, cancel_event=cancel_event)

    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("slow")) == []


def test_http_404_does_not_create_artifact(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/notfound", destination_dir=tmp_path)

    with pytest.raises(AcquisitionError):
        _service().acquire(request)

    assert list(tmp_path.iterdir()) == []


def test_http_500_does_not_create_artifact(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/servererror", destination_dir=tmp_path)

    with pytest.raises(AcquisitionError):
        _service().acquire(request)

    assert list(tmp_path.iterdir()) == []


def test_connection_failure_does_not_create_artifact(tmp_path):
    # Nothing listens on this port -> ConnectionError from requests.
    request = DownloadRequest(url="http://127.0.0.1:1/unreachable", destination_dir=tmp_path)

    with pytest.raises(AcquisitionError):
        _service().acquire(request)

    assert list(tmp_path.iterdir()) == []


def test_empty_response_body_is_an_error(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/empty", destination_dir=tmp_path)

    with pytest.raises(AcquisitionError):
        _service().acquire(request)

    assert list(tmp_path.iterdir()) == []


def test_content_disposition_filename_is_used(http_fixture_server, tmp_path):
    request = DownloadRequest(
        url=f"{http_fixture_server.base_url}/with-content-disposition", destination_dir=tmp_path
    )

    completed = _service().acquire(request)

    assert completed.display_name == "named-file.mp4"


def test_completed_download_to_artifact(http_fixture_server, tmp_path):
    request = DownloadRequest(url=f"{http_fixture_server.base_url}/normal.mp4", destination_dir=tmp_path)
    completed = _service().acquire(request)

    artifact = Artifact.from_completed_download(completed.final_path, source_url=completed.source_url)

    assert artifact.local_path.exists()
    assert artifact.size == len(NORMAL_BODY)
    assert artifact.sha256
    assert artifact.mime_type == "video/mp4"

    public = artifact.to_public_dict()
    assert "local_path" not in public
    assert "source_url" not in public
