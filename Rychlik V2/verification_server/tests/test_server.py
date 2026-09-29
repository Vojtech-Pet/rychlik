"""The HTTP verification server: real HTTP against the real server, with a fake Publisher client underneath."""

from __future__ import annotations

import http.client
import json

import pytest

from play_publisher import VerificationResult
from server import TOKEN_HEADER, VerificationServer


class _FakeClient:
    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def get(self, *, packageName, productId, token):
        self.calls.append({"packageName": packageName, "productId": productId, "token": token})
        return self

    def execute(self):
        state = {VerificationResult.VERIFIED: 0, VerificationResult.REJECTED: 1, VerificationResult.TEMPORARY_FAILURE: 2}[self.result]
        return {"purchaseState": state}


@pytest.fixture
def rig():
    client = _FakeClient(VerificationResult.VERIFIED)
    server = VerificationServer(package_name="app.friendsend.friendsend", products_client=client, token="secret-token", port=0, max_requests_per_minute=8)
    assert server.start()
    yield server, client
    server.stop()


def post(server, path, body=None, *, token=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    data = raw if raw is not None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[TOKEN_HEADER] = token
    conn.request("POST", path, body=data, headers=headers)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, (json.loads(payload) if payload else {})


VALID = {"productId": "friendsend_lifetime_unlock", "purchaseToken": "tok-1"}


def test_authorised_request_returns_the_publisher_api_result(rig):
    server, client = rig
    status, body = post(server, "/verify", VALID, token="secret-token")
    assert (status, body) == (200, {"ok": True, "result": "verified"})
    assert client.calls == [{"packageName": "app.friendsend.friendsend", "productId": "friendsend_lifetime_unlock", "token": "tok-1"}]


def test_rejected_and_temporary_failure_map_through(rig):
    server, client = rig
    client.result = VerificationResult.REJECTED
    assert post(server, "/verify", VALID, token="secret-token") == (200, {"ok": True, "result": "rejected"})
    client.result = VerificationResult.TEMPORARY_FAILURE
    assert post(server, "/verify", VALID, token="secret-token") == (200, {"ok": True, "result": "temporary_failure"})


def test_wrong_or_missing_token_is_unauthorized_and_never_calls_the_publisher_api(rig):
    server, client = rig
    for token in (None, "", "wrong", "secret-token-extra"):
        assert post(server, "/verify", VALID, token=token)[0] == 401
    assert client.calls == []


def test_bad_bodies_are_rejected_without_calling_the_publisher_api(rig):
    server, client = rig
    for body in ({"productId": "x"}, {"purchaseToken": "y"}, {"productId": "", "purchaseToken": "y"}, {}, ["list"]):
        assert post(server, "/verify", body, token="secret-token")[0] == 400, body
    assert post(server, "/verify", raw=b"{not json", token="secret-token")[0] == 400
    assert post(server, "/verify", raw=b"x" * 5000, token="secret-token")[0] == 413
    assert client.calls == []


def test_unknown_path_and_get_are_refused(rig):
    server, _ = rig
    assert post(server, "/other", VALID, token="secret-token")[0] == 404
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", "/verify")
    assert conn.getresponse().status == 405
    conn2 = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn2.request("GET", "/healthz")
    assert conn2.getresponse().status == 200


def test_rate_limit_caps_requests(rig):
    server, client = rig
    statuses = [post(server, "/verify", VALID, token="secret-token")[0] for _ in range(12)]
    assert statuses[:8] == [200] * 8 and set(statuses[8:]) == {429}


def test_port_in_use_is_reported_not_fatal(rig):
    server, client = rig
    second = VerificationServer(package_name="x", products_client=client, token="t", port=server.port)
    assert second.start() is False
