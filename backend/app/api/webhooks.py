"""Provider webhook ingress (spec 10.4, POL-15).

Order is binding: verify the signature, derive the tenant from the trusted source
registration, then open `scoped_tx`. Never read a tenant ID from the payload, query or
headers, and write nothing tenant-owned for an invalid signature (AC-PP-03).
"""

from fastapi import APIRouter

from app.authz.guard import public
from app.errors import not_implemented

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/paypal/{source_id}")
def paypal_webhook(source_id: str, _: None = public()) -> None:
    raise not_implemented("4.3")
