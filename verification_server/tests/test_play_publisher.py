"""verify_purchase against a fake Android Publisher client -- no network, no real Google credentials."""

from __future__ import annotations

import pytest

from play_publisher import PurchaseCheck, VerificationResult, verify_purchase

CHECK = PurchaseCheck(package_name="app.friendsend.friendsend", product_id="friendsend_lifetime_unlock", purchase_token="tok-1")


class _Execute:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result, self._error = result, error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, execute: _Execute) -> None:
        self._execute = execute
        self.calls: list[dict] = []

    def get(self, *, packageName, productId, token):
        self.calls.append({"packageName": packageName, "productId": productId, "token": token})
        return self._execute


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


def test_purchase_state_0_is_verified():
    client = _FakeClient(_Execute({"purchaseState": 0}))
    assert verify_purchase(client, CHECK) == VerificationResult.VERIFIED


def test_purchase_state_1_canceled_is_rejected():
    client = _FakeClient(_Execute({"purchaseState": 1}))
    assert verify_purchase(client, CHECK) == VerificationResult.REJECTED


def test_purchase_state_2_pending_is_temporary_failure():
    client = _FakeClient(_Execute({"purchaseState": 2}))
    assert verify_purchase(client, CHECK) == VerificationResult.TEMPORARY_FAILURE


def test_unrecognised_response_shape_is_temporary_failure_not_verified():
    client = _FakeClient(_Execute({"somethingElse": True}))
    assert verify_purchase(client, CHECK) == VerificationResult.TEMPORARY_FAILURE


def test_404_no_such_purchase_is_rejected():
    client = _FakeClient(_Execute(error=_HttpError(404)))
    assert verify_purchase(client, CHECK) == VerificationResult.REJECTED


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503, None])
def test_auth_quota_network_errors_are_temporary_failure_never_rejected_or_verified(status):
    client = _FakeClient(_Execute(error=_HttpError(status) if status else RuntimeError("no network")))
    assert verify_purchase(client, CHECK) == VerificationResult.TEMPORARY_FAILURE


def test_calls_the_client_with_exactly_the_given_package_product_and_token():
    client = _FakeClient(_Execute({"purchaseState": 0}))
    verify_purchase(client, CHECK)
    assert client.calls == [{"packageName": "app.friendsend.friendsend", "productId": "friendsend_lifetime_unlock", "token": "tok-1"}]
