"""PayPal Sandbox REST API client (tasks 4.1, 4.2, 4.10, spec 10).

Adheres to:
- Sandbox only: refuses any base URL other than https://api-m.sandbox.paypal.com (spec 10.1).
- Client credentials token from /v1/oauth2/token, caching the token until expires_in (OPEN-PP-01).
- Retries on 429, 5xx and network errors with exponential backoff; honours Retry-After.
- Idempotent action calls use PayPal-Request-Id.
"""

import base64
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.errors import validation_failed

logger = logging.getLogger("settleiq.connectors.paypal")

SANDBOX_HOST = "api-m.sandbox.paypal.com"
SANDBOX_BASE_URL = f"https://{SANDBOX_HOST}"
TOKEN_PATH = "/v1/oauth2/token"
SAFETY_BUFFER_SECONDS = 60


class PayPalError(Exception):
    def __init__(self, message: str, status_code: int | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = SANDBOX_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.netloc != SANDBOX_HOST:
            raise ValueError(f"PayPal adapter refuses non-Sandbox host: {parsed.netloc!r}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: float = 0
        self._scopes: list[str] = []
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            transport=self._transport,
            timeout=15.0,
        )

    def get_token(self) -> tuple[str, list[str]]:
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token, self._scopes

        basic_auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        for attempt in range(1, 4):
            try:
                with self._client() as client:
                    resp = client.post(TOKEN_PATH, headers=headers, data=data)
                if resp.status_code == 200:
                    payload = resp.json()
                    access_token = payload["access_token"]
                    expires_in = int(payload.get("expires_in", 3600))
                    scope_str = payload.get("scope", "")
                    self._token = access_token
                    self._token_expires_at = now + max(0, expires_in - SAFETY_BUFFER_SECONDS)
                    self._scopes = scope_str.split()
                    return self._token, self._scopes
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    wait = float(resp.headers.get("Retry-After", 2**attempt))
                    time.sleep(wait)
                    continue
                raise PayPalError(f"OAuth token request failed: {resp.text}", resp.status_code)
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt == 3:
                    raise PayPalError(f"OAuth network error after retries: {exc}") from exc
                time.sleep(2**attempt)

        raise PayPalError("OAuth token request exhausted max attempts")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], bytes]:
        token, _ = self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(1, 4):
            try:
                with self._client() as client:
                    resp = client.request(
                        method,
                        path,
                        params=params,
                        json=json_data,
                        headers=headers,
                    )
                if resp.status_code in (200, 201, 202, 204):
                    data = resp.json() if resp.content else {}
                    return resp.status_code, data, resp.content
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    wait = float(resp.headers.get("Retry-After", 2**attempt))
                    time.sleep(wait)
                    continue
                try:
                    err_json = resp.json()
                except Exception:
                    err_json = resp.text
                return resp.status_code, {"error": err_json}, resp.content
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt == 3:
                    raise PayPalError(f"PayPal API network error: {exc}") from exc
                time.sleep(2**attempt)

        raise PayPalError("PayPal API request exhausted max attempts")

    def transactions_search(
        self,
        start_date: datetime,
        end_date: datetime,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[dict[str, Any], bytes]:
        if (end_date - start_date).days > 31:
            raise validation_failed("Transaction Search window must not exceed 31 days.")
        start_iso = start_date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "start_date": start_iso,
            "end_date": end_iso,
            "page": page,
            "page_size": min(500, max(1, page_size)),
            "fields": "all",
        }
        status, data, raw = self._request("GET", "/v1/reporting/transactions", params=params)
        if status != 200:
            raise PayPalError(f"Transaction Search failed: {data}", status, data)
        return data, raw

    def captures_read(self, capture_id: str) -> tuple[dict[str, Any], bytes]:
        if not capture_id:
            raise validation_failed("capture_id is required.")
        status, data, raw = self._request("GET", f"/v2/payments/captures/{capture_id}")
        if status != 200:
            raise PayPalError(f"Capture read failed: {data}", status, data)
        return data, raw

    def refunds_read(self, refund_id: str) -> tuple[dict[str, Any], bytes]:
        if not refund_id:
            raise validation_failed("refund_id is required.")
        status, data, raw = self._request("GET", f"/v2/payments/refunds/{refund_id}")
        if status != 200:
            raise PayPalError(f"Refund read failed: {data}", status, data)
        return data, raw

    def refunds_create(
        self,
        capture_id: str,
        amount: str,
        currency: str,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any], bytes]:
        if not capture_id or not idempotency_key:
            raise validation_failed("capture_id and idempotency_key are required.")
        headers = {"PayPal-Request-Id": idempotency_key}
        payload = {
            "amount": {
                "value": amount,
                "currency_code": currency,
            }
        }
        return self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json_data=payload,
            extra_headers=headers,
        )

    def verify_webhook_signature(
        self,
        *,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_id: str,
        webhook_event: dict[str, Any],
    ) -> bool:
        payload = {
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_id": webhook_id,
            "webhook_event": webhook_event,
        }
        status, data, _ = self._request(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            json_data=payload,
        )
        if status == 200 and data.get("verification_status") == "SUCCESS":
            return True
        return False
