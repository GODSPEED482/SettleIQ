"""PayPal refunds adapter (task 4.10, spec 10.3, 10.6, AC-PP-12, AC-PLY-02).

Implements capabilities:
- `refunds.create` (1.0.0): Refunds only the exact capture named, sends idempotency_key
  as PayPal-Request-Id, and refuses any non-Sandbox environment.
- `refunds.read` (1.0.0): Retrieves refund details by refund ID.

An ambiguous outcome (network drop, timeout or 5xx) raises RefundOutcomeUnknown. The Worker
(task 5.15) settles it by captures.read and refunds.read, and repeats the call only under the
same PayPal-Request-Id, never a new one (spec 9.4, 10.3). The Worker calls create_refund only
under a grant for the exact capture and amount the approval named.
"""

import logging
import re
from typing import Any

from app.connectors.paypal.client import PayPalClient, PayPalError
from app.errors import conflict, validation_failed

logger = logging.getLogger("settleiq.connectors.paypal.refunds")


class RefundOutcomeUnknown(Exception):
    """refunds.create may or may not have created a refund (timeout, network drop or 5xx)."""


AMOUNT_PATTERN = re.compile(r"^[0-9]+\.[0-9]{2}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def create_refund(
    client: PayPalClient,
    *,
    capture_id: str,
    amount: str,
    currency: str,
    idempotency_key: str,
) -> tuple[dict[str, Any], bytes]:
    """Execute refund against a specific capture in PayPal Sandbox."""
    if not capture_id:
        raise validation_failed("capture_id is required.")
    if not AMOUNT_PATTERN.match(amount):
        raise validation_failed(f"Invalid amount format: {amount!r}; expected two decimal places.")
    if not CURRENCY_PATTERN.match(currency):
        raise validation_failed(f"Invalid currency code: {currency!r}; expected 3-letter code.")
    if not idempotency_key or not idempotency_key.strip():
        raise validation_failed("idempotency_key is required.")

    try:
        status, data, raw_bytes = client.refunds_create(
            capture_id=capture_id,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
        )
    except PayPalError as exc:
        # The client already repeated the call under the same PayPal-Request-Id. Whether a
        # refund exists is now unknown: the caller settles it by readback, never by a new key.
        # A capture that reads REFUNDED is not proof either, since another refund could have
        # done that.
        logger.warning("refund outcome for capture %s is unknown: %s", capture_id, exc)
        raise RefundOutcomeUnknown(str(exc)) from exc

    if status not in (200, 201):
        raise conflict(
            "REFUND_FAILED",
            f"PayPal refused refund (status {status}): {data}",
        )

    return data, raw_bytes


def check_refundable(
    client: PayPalClient, *, capture_id: str, amount: str, currency: str
) -> dict[str, Any]:
    """Before refunding, prove from captures.read that the exact capture is COMPLETED, for the
    approved amount and currency, and so carries no refund yet (spec 9.2). Returns the capture.
    A partial or full refund changes the capture's status away from COMPLETED."""
    capture, _ = client.captures_read(capture_id)
    observed = capture.get("amount") or {}
    if capture.get("status") != "COMPLETED":
        raise conflict("CAPTURE_NOT_REFUNDABLE",
                       f"The capture is {capture.get('status')}, not COMPLETED.")
    if (observed.get("value"), observed.get("currency_code")) != (amount, currency):
        raise conflict("CAPTURE_AMOUNT_CHANGED",
                       "The capture's amount or currency differs from the approved refund.")
    return capture


def refund_failures(
    refund: dict[str, Any], *, capture_id: str, amount: str, currency: str
) -> list[str]:
    """Why a refunds.read response does not show a completed refund of exactly this capture,
    for exactly this amount; empty when it does (AC-PP-08)."""
    failures = []
    if refund.get("status") != "COMPLETED":
        failures.append(f"refund is {refund.get('status')}, not COMPLETED")
    observed = refund.get("amount") or {}
    if (observed.get("value"), observed.get("currency_code")) != (amount, currency):
        failures.append("refund amount or currency differs from the approved refund")
    up = [link.get("href", "") for link in refund.get("links") or [] if link.get("rel") == "up"]
    if not any(href.rstrip("/").endswith(f"/captures/{capture_id}") for href in up):
        failures.append("refund is not linked to the refunded capture")
    return failures


def read_refund(
    client: PayPalClient,
    *,
    refund_id: str,
) -> tuple[dict[str, Any], bytes]:
    """Retrieve details of a refund by refund ID."""
    if not refund_id:
        raise validation_failed("refund_id is required.")
    return client.refunds_read(refund_id)
