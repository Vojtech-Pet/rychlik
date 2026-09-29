"""HTTP front end for purchase verification (`POST /verify`), called by FriendSend's backend-facing
`ServerPurchaseVerifier` once it exists on the client side. Loopback-style shared-secret auth (same pattern as
`rychlik.bridge.browser_bridge`): a bearer token in `X-Verify-Token`, checked with a constant-time compare.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from play_publisher import PurchaseCheck, VerificationResult, verify_purchase

MAX_BODY_BYTES = 4 * 1024
TOKEN_HEADER = "X-Verify-Token"
_RESULT_TO_JSON = {
    VerificationResult.VERIFIED: "verified",
    VerificationResult.REJECTED: "rejected",
    VerificationResult.TEMPORARY_FAILURE: "temporary_failure",
}


class _RateLimit:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self._max, self._window = max_events, window_seconds
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._events and now - self._events[0] > self._window:
                self._events.popleft()
            if len(self._events) >= self._max:
                return False
            self._events.append(now)
            return True


class VerificationServer:
    def __init__(self, *, package_name: str, products_client, token: str, port: int = 8787, max_requests_per_minute: int = 60) -> None:
        self._package_name = package_name
        self._client = products_client
        self._token = token
        self._port = port
        self._limit = _RateLimit(max_requests_per_minute, 60.0)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self._port

    def start(self) -> bool:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args) -> None:
                pass

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/healthz":
                    return self._reply(200, {"ok": True})
                self._reply(405, {"ok": False, "error": "POST only"})

            def do_POST(self) -> None:
                if self.path != "/verify":
                    return self._reply(404, {"ok": False, "error": "unknown endpoint"})
                supplied = self.headers.get(TOKEN_HEADER, "")
                if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), server._token.encode("utf-8")):
                    return self._reply(401, {"ok": False, "error": "unauthorized"})
                if not server._limit.allow():
                    return self._reply(429, {"ok": False, "error": "too many requests"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self._reply(400, {"ok": False, "error": "bad length"})
                if length <= 0 or length > MAX_BODY_BYTES:
                    return self._reply(413 if length > MAX_BODY_BYTES else 400, {"ok": False, "error": "bad body size"})
                try:
                    data = json.loads(self.rfile.read(length))
                    product_id = str(data["productId"])
                    purchase_token = str(data["purchaseToken"])
                    if not product_id or not purchase_token:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return self._reply(400, {"ok": False, "error": "invalid request"})

                check = PurchaseCheck(package_name=server._package_name, product_id=product_id, purchase_token=purchase_token)
                result = verify_purchase(server._client, check)
                self._reply(200, {"ok": True, "result": _RESULT_TO_JSON[result]})

        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)
        except OSError:
            self._server = None
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="verification-server", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()


def main() -> None:
    package_name = os.environ["FRIENDSEND_PACKAGE_NAME"]
    service_account_path = os.environ["FRIENDSEND_SERVICE_ACCOUNT_JSON"]
    token = os.environ["FRIENDSEND_VERIFY_TOKEN"]
    port = int(os.environ.get("FRIENDSEND_VERIFY_PORT", "8787"))

    from play_publisher import load_products_client

    client = load_products_client(service_account_path)
    server = VerificationServer(package_name=package_name, products_client=client, token=token, port=port)
    if not server.start():
        raise SystemExit(f"port {port} is already in use")
    print(f"FriendSend verification server listening on :{server.port}")
    threading.Event().wait()


if __name__ == "__main__":
    main()
