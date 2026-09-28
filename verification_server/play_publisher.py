"""Google Play Developer API client for verifying a FriendSend lifetime-unlock purchase.

Wraps `purchases.products.get` (Android Publisher API) behind a narrow function so the HTTP layer and its tests
never touch Google's client library directly. A service account JSON key with access to the app's Play Console
listing (Android Publisher API enabled, "View app information" + "Manage orders and subscriptions" permission)
is required; see README.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class VerificationResult(Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    TEMPORARY_FAILURE = "temporary_failure"


@dataclass(frozen=True)
class PurchaseCheck:
    package_name: str
    product_id: str
    purchase_token: str


class ProductPurchaseError(Exception):
    """Wraps whatever the Play API client raised, with the HTTP status if there was one (never guessed)."""

    def __init__(self, message: str, *, status: int | None) -> None:
        super().__init__(message)
        self.status = status


class ProductsClient(Protocol):
    """The one Android Publisher call this module needs -- real client: `androidpublisher.purchases().products()`."""

    def get(self, *, packageName: str, productId: str, token: str) -> ProductPurchaseError | object: ...


def load_products_client(service_account_json_path: str):
    """Builds the real Google API client. Imports are local so tests never need these packages installed."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        service_account_json_path, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )
    service = build("androidpublisher", "v3", credentials=credentials, cache_discovery=False)
    return service.purchases().products()


def verify_purchase(client, check: PurchaseCheck) -> VerificationResult:
    """Never raises for an ordinary API-reported problem: any error becomes a `VerificationResult`. `client` is
    whatever `.get(...).execute()` protocol the real (or a fake, for tests) Android Publisher client offers."""
    try:
        response = client.get(packageName=check.package_name, productId=check.product_id, token=check.purchase_token).execute()
    except Exception as exc:  # noqa: BLE001 - classify below; a bug here must never crash verification into "verified"
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
        return _classify_error(status)

    # purchaseState: 0 = purchased, 1 = canceled (refunded/revoked), 2 = pending.
    state = response.get("purchaseState")
    if state == 1:
        return VerificationResult.REJECTED
    if state == 2:
        return VerificationResult.TEMPORARY_FAILURE  # not completed yet; the client will retry
    if state == 0:
        return VerificationResult.VERIFIED
    return VerificationResult.TEMPORARY_FAILURE  # an unrecognised shape is our bug, not proof the purchase is bad


def _classify_error(status: int | None) -> VerificationResult:
    if status == 404:
        return VerificationResult.REJECTED  # no such purchase for this token/product: not a transient problem
    return VerificationResult.TEMPORARY_FAILURE  # auth/quota/network/anything else: our problem, not the purchase's
