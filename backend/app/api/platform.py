"""Break-glass support (POL-10). Deferred by the 3-day plan, so access resolves to nothing."""

from fastapi import APIRouter

from app.authz.access import Principal
from app.authz.guard import require
from app.errors import not_implemented

router = APIRouter(prefix="/api/platform", tags=["platform"])


@router.post("/tenants/{tenant_id}/support-requests")
def request_support_access(
    tenant_id: str, principal: Principal = require("platform.support.request")
) -> None:
    raise not_implemented("break-glass")


@router.post("/support-requests/{request_id}/approve")
def approve_support_access(
    request_id: str, principal: Principal = require("platform.support.approve")
) -> None:
    raise not_implemented("break-glass")
