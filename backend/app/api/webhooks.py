"""Provider webhook ingress (spec 10.4, POL-15).

Order is binding: verify the signature, derive the tenant from the trusted source
registration, then open `scoped_tx`. Never read a tenant ID from the payload, query or
headers, and write nothing tenant-owned for an invalid signature (AC-PP-03).
"""

from fastapi import APIRouter

from app.authz.guard import public
from app.errors import not_implemented

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


# TODO(G4 exit Rahul): AC-WEBHOOK-01 and AC-PP-03, 04, 05, 06 and 10 pass with automated
# tests, and a real Sandbox PAYMENT.CAPTURE.COMPLETED delivery is received end to end (stored,
# processed, STATUS_MISMATCH raised against the PENDING merchant row). Record the event ID.


# TODO(G4 4.3 Rahul): resolve source_id through a narrowly privileged lookup that returns only
# the trusted tenant, connector instance and registered PayPal webhook ID, and is not a general
# tenant query (spec 10.4). Verify the signature against that webhook ID and the transmission
# headers before trusting the body. An unknown source or a bad signature gets the same
# response and writes nothing tenant-owned (AC-PP-03); forensics go only to a separate platform
# security ingress log with bounded metadata. Then open scoped_tx(trusted tenant), store the
# original bytes as WEBHOOK evidence with provider_event_id and provenance `provider_event`,
# and insert a `webhook_inbox` row (spec 6.3: unique (tenant_id, source_id, provider_event_id))
# before acknowledging. A repeated event ID acknowledges without a second row (AC-PP-04).
# Ignore any tenant_id in the payload, query or headers (AC-WEBHOOK-01). The migration needs
# enable_tenant_rls and an explicit GRANT; keep test_rls.py green.
# TODO(G4 4.4 Rahul): process inbox rows asynchronously, apart from receipt. Claim with
# FOR UPDATE SKIP LOCKED, persist the event record as conventions.py describes, then trigger a
# domain run so the capture reaches reconciliation (AC-PP-05). Track processing state and
# attempts; after the last retry move the row to dead-letter for human review. Replay reuses
# the original event identity and never duplicates downstream work (spec 10.4). Order events
# by provider time, not delivery order, and re-read critical state from PayPal before any
# dependent action. Expose failed, replayed and dead-lettered rows to the Sources screen (4.7).
# TODO(G4 4.8 Suprava): failure tests against this route: an invalid or missing signature
# leaves no raw_evidence, webhook_inbox or canonical row for any tenant (AC-PP-03); a duplicate
# delivery gives one inbox row and one downstream effect (AC-PP-04); an older event arriving
# after a newer one does not regress the resolved state (spec 16.7 failure injection). Trace
# them in the acceptance-criteria sheet (6.5).
@router.post("/paypal/{source_id}")
def paypal_webhook(source_id: str, _: None = public()) -> None:
    raise not_implemented("4.3")
