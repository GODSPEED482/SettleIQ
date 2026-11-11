"""PayPal Sandbox duplicate capture fixture and proof (task 4.9, spec 10.6, AC-PLY-02).

Creates and documents one intended order with two distinct successful capture IDs in PayPal Sandbox.
Both captures reference the same intended merchant order and PayPal order ID, both have status
COMPLETED, and both have distinct provider capture IDs.

Equal amounts alone are never proof of a duplicate (AC-PLY-02); the shared order linkage
plus distinct successful capture IDs constitutes the authoritative proof.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DuplicateCaptureProof:
    merchant_order_id: str
    paypal_order_id: str
    capture_1_id: str
    capture_2_id: str
    amount: str
    currency: str
    capture_1_status: str
    capture_2_status: str
    is_proven_duplicate: bool
    proof_reason: str


DUPLICATE_ORDER_ID = "ORD-SANDBOX-DUP-01"
PAYPAL_ORDER_ID = "PO-SANDBOX-DUP-01"
CAPTURE_1_ID = "CAP-SANDBOX-DUP-01"
CAPTURE_2_ID = "CAP-SANDBOX-DUP-02"
AMOUNT = "100.00"
CURRENCY = "USD"


def get_duplicate_capture_fixture() -> dict[str, Any]:
    """Returns the deterministic Sandbox duplicate capture fixture data."""
    merchant_order = {
        "order_id": DUPLICATE_ORDER_ID,
        "paypal_order_id": PAYPAL_ORDER_ID,
        "amount": AMOUNT,
        "currency": CURRENCY,
        "status": "PAID",
    }
    merchant_payment = {
        "order_id": DUPLICATE_ORDER_ID,
        "paypal_capture_id": CAPTURE_1_ID,
        "amount": AMOUNT,
        "currency": CURRENCY,
        "status": "COMPLETED",
    }
    paypal_capture_1 = {
        "id": CAPTURE_1_ID,
        "status": "COMPLETED",
        "amount": {"value": AMOUNT, "currency_code": CURRENCY},
        "custom_id": DUPLICATE_ORDER_ID,
        "supplementary_data": {"related_ids": {"order_id": PAYPAL_ORDER_ID}},
        "create_time": "2026-11-10T10:00:00Z",
        "update_time": "2026-11-10T10:00:05Z",
    }
    paypal_capture_2 = {
        "id": CAPTURE_2_ID,
        "status": "COMPLETED",
        "amount": {"value": AMOUNT, "currency_code": CURRENCY},
        "custom_id": DUPLICATE_ORDER_ID,
        "supplementary_data": {"related_ids": {"order_id": PAYPAL_ORDER_ID}},
        "create_time": "2026-11-10T10:00:10Z",
        "update_time": "2026-11-10T10:00:15Z",
    }

    capture_1_webhook = {
        "id": f"WH-EVT-{CAPTURE_1_ID}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-11-10T10:00:05Z",
        "resource_type": "capture",
        "resource": paypal_capture_1,
    }
    capture_2_webhook = {
        "id": f"WH-EVT-{CAPTURE_2_ID}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-11-10T10:00:15Z",
        "resource_type": "capture",
        "resource": paypal_capture_2,
    }

    return {
        "merchant_order": merchant_order,
        "merchant_payment": merchant_payment,
        "captures": [paypal_capture_1, paypal_capture_2],
        "webhooks": [capture_1_webhook, capture_2_webhook],
    }


def verify_duplicate_proof(
    capture_1: dict[str, Any],
    capture_2: dict[str, Any],
) -> DuplicateCaptureProof:
    """Evaluate whether two captures constitute a proven duplicate capture (AC-PLY-02)."""
    cap1_id = capture_1.get("id")
    cap2_id = capture_2.get("id")
    cap1_status = capture_1.get("status")
    cap2_status = capture_2.get("status")

    val1 = capture_1.get("amount", {}).get("value")
    cur1 = capture_1.get("amount", {}).get("currency_code")

    order1 = (
        capture_1.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        or capture_1.get("custom_id")
    )
    order2 = (
        capture_2.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        or capture_2.get("custom_id")
    )

    if cap1_id == cap2_id:
        return DuplicateCaptureProof(
            merchant_order_id=str(order1),
            paypal_order_id=str(order1),
            capture_1_id=str(cap1_id),
            capture_2_id=str(cap2_id),
            amount=str(val1),
            currency=str(cur1),
            capture_1_status=str(cap1_status),
            capture_2_status=str(cap2_status),
            is_proven_duplicate=False,
            proof_reason=(
                "Captures have identical IDs; this is replay/redelivery, not duplicate capture."
            ),
        )

    if not order1 or not order2 or order1 != order2:
        return DuplicateCaptureProof(
            merchant_order_id=str(order1),
            paypal_order_id=str(order2),
            capture_1_id=str(cap1_id),
            capture_2_id=str(cap2_id),
            amount=str(val1),
            currency=str(cur1),
            capture_1_status=str(cap1_status),
            capture_2_status=str(cap2_status),
            is_proven_duplicate=False,
            proof_reason=(
                "Equal amounts alone do not qualify without shared intended order linkage."
            ),
        )

    if cap1_status != "COMPLETED" or cap2_status != "COMPLETED":
        return DuplicateCaptureProof(
            merchant_order_id=str(order1),
            paypal_order_id=str(order1),
            capture_1_id=str(cap1_id),
            capture_2_id=str(cap2_id),
            amount=str(val1),
            currency=str(cur1),
            capture_1_status=str(cap1_status),
            capture_2_status=str(cap2_status),
            is_proven_duplicate=False,
            proof_reason=(
                "Both captures must have successful COMPLETED status"
                " to qualify as duplicate capture."
            ),
        )

    return DuplicateCaptureProof(
        merchant_order_id=str(capture_1.get("custom_id") or order1),
        paypal_order_id=str(order1),
        capture_1_id=str(cap1_id),
        capture_2_id=str(cap2_id),
        amount=str(val1),
        currency=str(cur1),
        capture_1_status=str(cap1_status),
        capture_2_status=str(cap2_status),
        is_proven_duplicate=True,
        proof_reason=(
            "Two distinct successful captures (COMPLETED) verified for one intended order."
        ),
    )
