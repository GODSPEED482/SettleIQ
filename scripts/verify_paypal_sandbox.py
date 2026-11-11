#!/usr/bin/env python3
"""PayPal Sandbox connectivity and scope verification script (task 4.1, OPEN-PP-01).

Usage:
    python scripts/verify_paypal_sandbox.py [--client-id ID] [--client-secret SECRET] [--tunnel-url URL]
        [--source-id SOURCE_ID]
    python scripts/verify_paypal_sandbox.py --live-capture ORDER_REF   # G4 live exit step
    python scripts/verify_paypal_sandbox.py --capture-order ORDER_ID   # after buyer approval

Checks:
1. Authenticates against https://api-m.sandbox.paypal.com/v1/oauth2/token
2. Verifies scopes include:
   - https://uri.paypal.com/services/reporting/search/read
   - https://uri.paypal.com/services/payments/refund
3. If --tunnel-url is provided, registers or validates webhook for PAYMENT.CAPTURE.COMPLETED
4. Records provider facts and Sandbox SFTP availability status for OPEN-PP-01
"""

import argparse
import base64
import json
import os
import sys
from urllib.parse import urlparse

import httpx

SANDBOX_HOST = "api-m.sandbox.paypal.com"
REQUIRED_SCOPES = [
    "https://uri.paypal.com/services/reporting/search/read",
    "https://uri.paypal.com/services/payments/refund",
]


def verify_sandbox(
    client_id: str,
    client_secret: str,
    tunnel_url: str | None = None,
    source_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict:
    print(f"Connecting to PayPal Sandbox ({SANDBOX_HOST})...")
    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}

    with httpx.Client(base_url=f"https://{SANDBOX_HOST}", timeout=15.0) as client:
        resp = client.post("/v1/oauth2/token", headers=headers, data=data)
        if resp.status_code != 200:
            print(f"FAILED: OAuth token request failed with status {resp.status_code}: {resp.text}")
            return {"success": False, "error": resp.text}

        token_data = resp.json()
        access_token = token_data.get("access_token")
        scopes = token_data.get("scope", "").split()
        expires_in = token_data.get("expires_in")
        print(f"SUCCESS: OAuth token issued (expires in {expires_in}s).")
        print(f"Total scopes returned: {len(scopes)}")

        scope_status = {}
        for req in REQUIRED_SCOPES:
            has_scope = req in scopes
            scope_status[req] = has_scope
            symbol = "✓" if has_scope else "✗"
            print(f"  [{symbol}] Scope: {req}")

        webhook_result = None
        if tunnel_url:
            print(f"Testing webhook endpoint on tunnel URL: {tunnel_url}")
            wh_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            target_url = f"{tunnel_url.rstrip('/')}/api/webhooks/paypal/{source_id}"
            wh_payload = {
                "url": target_url,
                "event_types": [
                    {"name": "PAYMENT.CAPTURE.COMPLETED"},
                    {"name": "PAYMENT.CAPTURE.REFUNDED"},
                    {"name": "PAYMENT.CAPTURE.DENIED"},
                ],
            }
            wh_resp = client.post("/v1/notifications/webhooks", headers=wh_headers, json=wh_payload)
            if wh_resp.status_code in (200, 201):
                wh_data = wh_resp.json()
                webhook_id = wh_data.get("id")
                print(f"SUCCESS: Webhook registered with PayPal Sandbox: ID={webhook_id}")
                webhook_result = {"status": "registered", "webhook_id": webhook_id}
            else:
                print(f"NOTICE: Webhook registration returned {wh_resp.status_code}: {wh_resp.text}")
                webhook_result = {"status": "response", "code": wh_resp.status_code, "text": wh_resp.text}

    return {
        "success": True,
        "scope_status": scope_status,
        "webhook_result": webhook_result,
        "sftp_status": "Manual support ticketing required for Sandbox SFTP (spec 10.3.1 fallback active)",
    }


def _bearer(client: httpx.Client, client_id: str, client_secret: str) -> dict[str, str]:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = client.post(
        "/v1/oauth2/token",
        headers={"Authorization": f"Basic {basic}"},
        data={"grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}", "Content-Type": "application/json"}


def _capture(client: httpx.Client, headers: dict[str, str], order_id: str) -> dict | None:
    resp = client.post(
        f"/v2/checkout/orders/{order_id}/capture",
        headers=headers | {"PayPal-Request-Id": f"settleiq-capture-{order_id}"},
    )
    if resp.status_code not in (200, 201):
        print(f"NOTICE: capture of {order_id} returned {resp.status_code}: {resp.text}")
        return None
    capture = resp.json()["purchase_units"][0]["payments"]["captures"][0]
    print(f"SUCCESS: capture {capture['id']} is {capture['status']} (order {order_id}).")
    print("PayPal now sends PAYMENT.CAPTURE.COMPLETED to every webhook subscribed to it.")
    return capture


def live_capture(
    client_id: str, client_secret: str, order_ref: str, amount: str = "1.00"
) -> dict | None:
    """G4 live exit step: make PayPal Sandbox emit a real PAYMENT.CAPTURE.COMPLETED.

    Creates a USD order for `amount` (Sandbox test money, USD 1.00 by default) whose invoice_id
    is `order_ref`, so the event reconciles against a merchant row with that order reference,
    then captures it. A Sandbox card captures at once where the app has card processing;
    otherwise the buyer approval link is printed and `--capture-order` finishes the capture
    after approval.
    """
    with httpx.Client(base_url=f"https://{SANDBOX_HOST}", timeout=30.0) as client:
        headers = _bearer(client, client_id, client_secret)
        order = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": order_ref,
                "invoice_id": order_ref,
                "custom_id": order_ref,
                "amount": {"currency_code": "USD", "value": amount},
            }],
            "payment_source": {"card": {
                "number": "4111111111111111", "expiry": "2030-12",
                "security_code": "123", "name": "SettleIQ Sandbox",
            }},
        }
        resp = client.post(
            "/v2/checkout/orders", json=order,
            headers=headers | {"PayPal-Request-Id": f"settleiq-order-{order_ref}"},
        )
        if resp.status_code not in (200, 201):
            # Without card processing (PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING), fall back to a
            # buyer-approved PayPal checkout. Sandbox checkout fails without return URLs.
            order["payment_source"] = {"paypal": {"experience_context": {
                "return_url": "https://example.com/settleiq/approved",
                "cancel_url": "https://example.com/settleiq/cancelled",
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
                "brand_name": "SettleIQ Sandbox",
            }}}
            resp = client.post(
                "/v2/checkout/orders", json=order,
                headers=headers | {"PayPal-Request-Id": f"settleiq-order-wallet-{order_ref}"},
            )
            resp.raise_for_status()
        created = resp.json()
        print(f"Order {created['id']} is {created['status']}.")
        if created["status"] == "COMPLETED":
            capture = created["purchase_units"][0]["payments"]["captures"][0]
            print(f"SUCCESS: capture {capture['id']} is {capture['status']}.")
            return capture
        if created["status"] == "APPROVED":
            return _capture(client, headers, created["id"])
        approve = next(
            (link["href"] for link in created.get("links", [])
             if link["rel"] in ("approve", "payer-action")),
            None,
        )
        print(f"Approve as a Sandbox buyer: {approve}")
        print(f"Then run: --capture-order {created['id']}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Verify PayPal Sandbox access and scopes.")
    parser.add_argument("--client-id", default=os.environ.get("PAYPAL_SANDBOX_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("PAYPAL_SANDBOX_CLIENT_SECRET"))
    parser.add_argument("--tunnel-url", default=os.environ.get("PAYPAL_WEBHOOK_TUNNEL_URL"))
    parser.add_argument(
        "--source-id",
        default="00000000-0000-0000-0000-000000000001",
        help="registered PayPal source the webhook should deliver to",
    )
    parser.add_argument(
        "--live-capture",
        metavar="ORDER_REF",
        help="create and capture a Sandbox order with this invoice_id (G4 live exit step)",
    )
    parser.add_argument("--amount", default="1.00", help="USD amount for --live-capture")
    parser.add_argument("--capture-order", metavar="ORDER_ID", help="capture an approved order")
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print("Note: PAYPAL_SANDBOX_CLIENT_ID and PAYPAL_SANDBOX_CLIENT_SECRET not provided.")
        print("Set them in environment or pass via flags to run against live PayPal Sandbox.")
        sys.exit(0)

    if args.capture_order:
        with httpx.Client(base_url=f"https://{SANDBOX_HOST}", timeout=30.0) as client:
            headers = _bearer(client, args.client_id, args.client_secret)
            sys.exit(0 if _capture(client, headers, args.capture_order) else 1)
    if args.live_capture:
        live_capture(args.client_id, args.client_secret, args.live_capture, args.amount)
        return

    result = verify_sandbox(args.client_id, args.client_secret, args.tunnel_url, args.source_id)
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()

