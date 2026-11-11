"""PB-001's action: the internal-merchant status correction (spec 4.4.1, task 5.6).

The adapter runs against the tenant's own internal-merchant source, which is registered apart
from the merchant-postgres read source that observed the payment. Exactly one ACTIVE one must
exist; zero or several fail closed before any side effect. Verification re-reads the payment
through merchant.payment.read, never trusting the correction's own response (AC-MERCH-01).
"""

from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.connectors.internal_merchant import correct_payment_status, read_payment
from app.connectors.internal_merchant.actions import CONNECTOR_ID, READ_CAPABILITY
from app.db import scoped_tx
from app.errors import ApiError
from app.execution.executors import Acted, Job, Refused, Verified, evidence_hash

# The adapter's answer when the merchant database could not be reached. The connector's retry
# policy repeats only this, and always under the same idempotency key.
UNAVAILABLE = "MERCHANT_UNAVAILABLE"


def _write_source(tenant_id: UUID) -> UUID:
    with scoped_tx(tenant_id) as conn:
        sources = conn.execute(
            text(
                "SELECT id FROM data_sources WHERE connector_id = :connector "
                "AND status = 'ACTIVE' ORDER BY id LIMIT 2"
            ),
            {"connector": CONNECTOR_ID},
        ).scalars().all()
    if len(sources) != 1:
        raise Refused("MERCHANT_SOURCE_UNRESOLVED", {"active_sources": len(sources)})
    return sources[0]


class MerchantCorrection:
    def prepare(self, job: Job) -> dict[str, Any]:
        source_id = _write_source(job.tenant_id)
        job.state["source_id"] = source_id
        job.state["params"] = {
            "tenant_id": str(job.tenant_id),
            "payment_id": job.target.get("payment_id"),
            "expected_current_status": job.target.get("expected_current_status"),
            "requested_status": job.target.get("requested_status"),
        }
        return {"source_id": str(source_id), "params": job.state["params"]}

    def act(self, job: Job) -> Acted:
        try:
            outcome = correct_payment_status(job.tenant_id, job.state["source_id"],
                                             job.state["params"], job.idempotency_key)
        except ApiError as exc:
            return Acted("RETRY" if exc.code == UNAVAILABLE else "FAILED", exc.code)
        evidence = asdict(outcome)
        if outcome.outcome == "APPLIED":
            return Acted("SUCCEEDED", None, evidence)
        # STALE_STATE or TARGET_UNRESOLVED: nothing changed (AC-MERCH-02, AC-MERCH-03).
        return Acted("FAILED", outcome.outcome, evidence)

    def settle(self, job: Job) -> Acted:
        # The adapter records each key in the merchant's own transaction, so the readback that
        # follows is the authority on whether the payment now reads COMPLETED.
        return Acted("SUCCEEDED", None, {"settled_by": READ_CAPABILITY})

    def verify(self, job: Job) -> Verified:
        payment = read_payment(job.tenant_id, job.state["source_id"],
                               {"tenant_id": str(job.tenant_id),
                                "payment_id": job.state["params"]["payment_id"]})
        observed = asdict(payment)
        evidence = {"payment": observed, "evidence_hash": evidence_hash(observed)}
        if payment.status != job.state["params"]["requested_status"]:
            return Verified(False, READ_CAPABILITY, evidence, "READBACK_MISMATCH")
        return Verified(True, READ_CAPABILITY, evidence)
