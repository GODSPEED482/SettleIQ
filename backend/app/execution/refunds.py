"""PB-002's action: refunds.create for the duplicate capture, in PayPal Sandbox (task 5.15).

- Before refunding, captures.read must show the duplicate COMPLETED, for the approved amount
  and currency (so not refunded at all yet), and the original capture the proof names still
  COMPLETED. Otherwise nothing is called and the case returns to HUMAN_REVIEW.
- The refund is sent once, with the execution's idempotency key as PayPal-Request-Id.
- An unknown outcome is settled only by readback. If the duplicate now reads refunded, the call
  is repeated under the same PayPal-Request-Id, which returns the refund PayPal already made, so
  its ID can be read back. Otherwise the execution goes to MANUAL_REVIEW. No retry ever uses a
  new key (spec 9.4).
- Verification needs all of these: refunds.read shows COMPLETED, for the exact amount, linked
  to the duplicate; the duplicate reads REFUNDED; the original still reads COMPLETED (AC-PP-08).
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.connectors.paypal.client import PayPalClient, PayPalError
from app.connectors.paypal.refunds import (
    RefundOutcomeUnknown,
    check_refundable,
    create_refund,
    read_refund,
    refund_failures,
)
from app.connectors.paypal.sync import _parse_credentials
from app.db import scoped_tx
from app.errors import ApiError
from app.execution.executors import Acted, Job, Refused, Verified, evidence_hash
from app.sources.secrets import resolve_secret

REFUNDED = frozenset({"REFUNDED", "PARTIALLY_REFUNDED"})


def _tenant_client(tenant_id: UUID, source_id: UUID) -> PayPalClient:
    """The tenant's own PayPal app, from the source the duplicate capture was observed on."""
    with scoped_tx(tenant_id) as conn:
        source = conn.execute(
            text("SELECT connector_id, status, secret_reference FROM data_sources "
                 "WHERE id = :id"),
            {"id": source_id},
        ).one_or_none()
    if source is None or source.connector_id != "paypal" or source.status != "ACTIVE":
        raise Refused("PAYPAL_SOURCE_UNRESOLVED")
    return PayPalClient(*_parse_credentials(resolve_secret(tenant_id, source.secret_reference)))


# Replaced in tests with a client on a mock transport.
paypal_client: Callable[[UUID, UUID], PayPalClient] = _tenant_client


def _capture_digest(capture: dict[str, Any]) -> dict[str, Any]:
    return {"id": capture.get("id"), "status": capture.get("status"),
            "amount": capture.get("amount"), "evidence_hash": evidence_hash(capture)}


class DuplicateCaptureRefund:
    def prepare(self, job: Job) -> dict[str, Any]:
        target = job.target
        original = (job.proof.get("original") or {}).get("capture_id")
        if not original or original == target.get("capture_id"):
            raise Refused("ORIGINAL_CAPTURE_UNKNOWN")
        try:
            source_id = UUID(str(target.get("paypal_source_id")))
        except ValueError:
            raise Refused("PAYPAL_SOURCE_UNRESOLVED") from None
        client = paypal_client(job.tenant_id, source_id)
        job.state.update(client=client, original=original)
        try:
            duplicate = check_refundable(client, capture_id=target["capture_id"],
                                         amount=target["amount"], currency=target["currency"])
            kept, _ = client.captures_read(original)
        except PayPalError as exc:
            raise Refused("PROVIDER_UNAVAILABLE", {"detail": exc.message}) from None
        except ApiError as exc:
            raise Refused(exc.code, {"capture_id": target["capture_id"]}) from None
        if kept.get("status") != "COMPLETED":
            raise Refused("ORIGINAL_CAPTURE_CHANGED", {"original": _capture_digest(kept)})
        return {"duplicate": _capture_digest(duplicate), "original": _capture_digest(kept)}

    def _refund(self, job: Job) -> dict[str, Any]:
        data, _ = create_refund(job.state["client"], capture_id=job.target["capture_id"],
                                amount=job.target["amount"], currency=job.target["currency"],
                                idempotency_key=job.idempotency_key)
        job.state["refund_id"] = data.get("id")
        return {"refund_id": data.get("id"), "status": data.get("status"),
                "evidence_hash": evidence_hash(data)}

    def act(self, job: Job) -> Acted:
        try:
            evidence = self._refund(job)
        except RefundOutcomeUnknown:
            return Acted("OUTCOME_UNKNOWN", "PROVIDER_OUTCOME_UNKNOWN")
        except ApiError as exc:
            return Acted("FAILED", "REFUND_REJECTED" if exc.code == "REFUND_FAILED" else exc.code)
        if not evidence["refund_id"]:
            return Acted("OUTCOME_UNKNOWN", "NO_REFUND_ID", evidence)
        return Acted("SUCCEEDED", None, evidence)

    def settle(self, job: Job) -> Acted:
        try:
            duplicate, _ = job.state["client"].captures_read(job.target["capture_id"])
        except PayPalError:
            return Acted("OUTCOME_UNKNOWN", "PROVIDER_UNAVAILABLE")
        if duplicate.get("status") not in REFUNDED:
            # No refund is visible. Sending again could double-refund if one is in flight, so a
            # person decides (no blind retry).
            return Acted("OUTCOME_UNKNOWN", "NO_REFUND_OBSERVED",
                         {"duplicate": _capture_digest(duplicate)})
        try:
            evidence = self._refund(job)  # the same PayPal-Request-Id returns the same refund
        except (RefundOutcomeUnknown, ApiError):
            return Acted("OUTCOME_UNKNOWN", "REFUND_NOT_IDENTIFIED",
                         {"duplicate": _capture_digest(duplicate)})
        if not evidence["refund_id"]:
            return Acted("OUTCOME_UNKNOWN", "NO_REFUND_ID", evidence)
        return Acted("SUCCEEDED", None, {"duplicate": _capture_digest(duplicate), **evidence})

    def verify(self, job: Job) -> Verified:
        client: PayPalClient = job.state["client"]
        target = job.target
        try:
            refund, _ = read_refund(client, refund_id=job.state["refund_id"])
            duplicate, _ = client.captures_read(target["capture_id"])
            original, _ = client.captures_read(job.state["original"])
        except PayPalError:
            return Verified(False, "refunds.read", {}, "PROVIDER_UNAVAILABLE")
        failures = refund_failures(refund, capture_id=target["capture_id"],
                                   amount=target["amount"], currency=target["currency"])
        if duplicate.get("status") != "REFUNDED":
            failures.append(f"duplicate capture is {duplicate.get('status')}, not REFUNDED")
        if original.get("status") != "COMPLETED":
            failures.append(f"original capture is {original.get('status')}, not COMPLETED")
        evidence = {
            "refund": {"id": refund.get("id"), "status": refund.get("status"),
                       "amount": refund.get("amount"), "evidence_hash": evidence_hash(refund)},
            "duplicate": _capture_digest(duplicate),
            "original": _capture_digest(original),
            "failures": failures,
        }
        if failures:
            return Verified(False, "refunds.read", evidence, "READBACK_MISMATCH")
        return Verified(True, "refunds.read", evidence)
