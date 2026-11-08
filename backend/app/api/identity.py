"""Registration, tenant creation and the caller's own permissions (spec 3.1, 12.3)."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.authz.access import AccessGateway, Principal, get_gateway
from app.authz.guard import identity, public
from app.errors import not_found, not_implemented

router = APIRouter(prefix="/api", tags=["identity"])


@router.post("/auth/register")
def register(_: None = public()) -> None:
    raise not_implemented("1.9")


@router.post("/auth/verify-email")
def verify_email(_: None = public()) -> None:
    raise not_implemented("1.9")


@router.post("/tenants")
def create_tenant(principal: Principal = identity()) -> None:
    raise not_implemented("1.9")


@router.post("/invitations/{token}/accept")
def accept_invitation(token: str, principal: Principal = identity()) -> None:
    raise not_implemented("1.5")


@router.get("/me/permissions")
def my_permissions(
    tenant_id: str,
    principal: Principal = identity(),
    gateway: AccessGateway = Depends(get_gateway),
) -> dict:
    try:
        selected = UUID(tenant_id)
    except ValueError:
        raise not_found() from None
    access = gateway.resolve(principal, selected)
    if access is None:
        raise not_found()
    return {"tenant_id": str(selected), "permissions": access.summary()}
