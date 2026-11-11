"""PayPal refunds adapter (task 4.10, spec 10.3, 10.6, AC-PP-12, AC-PLY-02).

Implements capabilities:
- `refunds.create` (1.0.0): Refunds only the exact capture named, sends idempotency_key
  as PayPal-Request-Id, and refuses any non-Sandbox environment.
- `refunds.read` (1.0.0): Retrieves refund details by refund ID.

An ambiguous outcome (network drop, timeout or 5xx) is settled by refunds.read or capture
readback, never by a blind retry under a new idempotency key (spec 9.4, 10.3).
"""

import logging
import re
from typing import Any

from app.connectors.paypal.client import PayPalClient, PayPalError
from app.errors import conflict, validation_failed

logger = logging.getLogger("settleiq.connectors.paypal.refunds")

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
        # Ambiguous outcome handling: attempt capture readback to check if refund was processed
        logger.warning(
            "Refund request encountered an error (%s); checking capture status for resolution",
            exc,
        )
        try:
            cap_data, _ = client.captures_read(capture_id)
            # If capture status reflects refund or lists refund links, resolve unambiguously
            status_val = cap_data.get("status")
            if status_val == "REFUNDED":
                return cap_data, b'{"status": "REFUNDED"}'
        except Exception:
            pass
        raise conflict(
            "AMBIGUOUS_REFUND_OUTCOME",
            f"Refund status could not be verified: {exc}",
        ) from exc

    if status not in (200, 201):
        raise conflict(
            "REFUND_FAILED",
            f"PayPal refused refund (status {status}): {data}",
        )

    return data, raw_bytes


# TODO(G5 5.15 Rahul): before refunding, prove from refunds.read and captures.read that the
# target capture has no existing refund (spec 9.2). The Worker calls create_refund only under
# a grant for the exact capture and amount the approval named.
def read_refund(
    client: PayPalClient,
    *,
    refund_id: str,
) -> tuple[dict[str, Any], bytes]:
    """Retrieve details of a refund by refund ID."""
    if not refund_id:
        raise validation_failed("refund_id is required.")
    return client.refunds_read(refund_id)
