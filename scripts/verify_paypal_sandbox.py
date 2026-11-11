#!/usr/bin/env python3
"""PayPal Sandbox connectivity and scope verification script (task 4.1, OPEN-PP-01).

Usage:
    python scripts/verify_paypal_sandbox.py [--client-id ID] [--client-secret SECRET] [--tunnel-url URL]

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


def verify_sandbox(client_id: str, client_secret: str, tunnel_url: str | None = None) -> dict:
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
            target_url = f"{tunnel_url.rstrip('/')}/api/webhooks/paypal/00000000-0000-0000-0000-000000000001"
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


def main():
    parser = argparse.ArgumentParser(description="Verify PayPal Sandbox access and scopes.")
    parser.add_argument("--client-id", default=os.environ.get("PAYPAL_SANDBOX_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("PAYPAL_SANDBOX_CLIENT_SECRET"))
    parser.add_argument("--tunnel-url", default=os.environ.get("PAYPAL_WEBHOOK_TUNNEL_URL"))
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print("Note: PAYPAL_SANDBOX_CLIENT_ID and PAYPAL_SANDBOX_CLIENT_SECRET not provided.")
        print("Set them in environment or pass via flags to run against live PayPal Sandbox.")
        sys.exit(0)

    result = verify_sandbox(args.client_id, args.client_secret, args.tunnel_url)
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()

