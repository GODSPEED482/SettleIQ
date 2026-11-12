"""The PayPal Orders v2 calls Kitebase's checkout makes, in Sandbox only (config.py refuses any
other base URL). Kitebase never refunds: refunds come from SettleIQ's PB-002 through the same
Sandbox app, and the source view reads them back from PayPal."""

import time
import uuid
from decimal import Decimal
from typing import Any

import httpx


class PayPalError(RuntimeError):
    def __init__(self, message: str, issue: str | None = None) -> None:
        super().__init__(message)
        self.issue = issue


def _issue(response: httpx.Response) -> str | None:
    try:
        details = response.json().get("details") or []
    except ValueError:
        return None
    return details[0].get("issue") if details else None


class PayPal:
    def __init__(self, base: str, client_id: str, secret: str, timeout: float = 20.0) -> None:
        self._base = base
        self._auth = (client_id, secret)
        self._client = httpx.Client(timeout=timeout)
        self._token: tuple[str, float] | None = None
        self.configured = bool(client_id and secret)

    def _bearer(self) -> str:
        if self._token and self._token[1] > time.monotonic():
            return self._token[0]
        response = self._client.post(
            f"{self._base}/v1/oauth2/token", auth=self._auth,
            data={"grant_type": "client_credentials"},
        )
        if response.status_code != 200:
            raise PayPalError("PayPal refused the app credentials.")
        body = response.json()
        self._token = (body["access_token"], time.monotonic() + body["expires_in"] - 60)
        return self._token[0]

    def _call(self, method: str, path: str, *, json: Any = None,
              request_id: str | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._bearer()}",
                   "Content-Type": "application/json", "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        response = self._client.request(method, f"{self._base}{path}", json=json,
                                        headers=headers)
        if response.status_code >= 400:
            issue = _issue(response)
            raise PayPalError(f"PayPal answered {response.status_code} ({issue or 'no detail'}).",
                              issue)
        return response.json()

    def create_order(self, order_ref: str, payment_id: str, amount: Decimal, currency: str,
                     description: str) -> str:
        """One PayPal order for one shop order. `invoice_id` is the order reference, which is
        how SettleIQ links PayPal's capture to the merchant's order."""
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": order_ref,
                "invoice_id": order_ref,
                "custom_id": payment_id,
                "description": description,
                "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            }],
        }
        return self._call("POST", "/v2/checkout/orders", json=body,
                          request_id=f"kitebase-create-{uuid.uuid4()}")["id"]

    def capture_order(self, paypal_order_id: str) -> dict[str, str]:
        body = self._call("POST", f"/v2/checkout/orders/{paypal_order_id}/capture",
                          request_id=f"kitebase-capture-{paypal_order_id}")
        capture = body["purchase_units"][0]["payments"]["captures"][0]
        return {"capture_id": capture["id"], "status": capture["status"]}

    def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/checkout/orders/{paypal_order_id}")
