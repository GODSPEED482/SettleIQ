"""Registration, tenant creation and the caller's own permissions (spec 3.1, 12.3)."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.authz.access import AccessGateway, Principal, get_gateway
from app.authz.guard import identity, public
from app.errors import not_found, not_implemented

router = APIRouter(prefix="/api", tags=["identity"])

# TODO(G1 1.10 Suprava): add sign-in and sign-out to the contract (spec 12.2 omits them).
# TODO(G1 1.9 Swapnaneel): implement those routes alongside `authenticate`.
# TODO(G1 1.10 Suprava): add a list-my-tenants route for the tenant picker. Spec 12.2 omits it,
# and RLS hides memberships until a tenant is selected, so it needs a trusted lookup by user.


# TODO(G1 1.9 Swapnaneel): lowercase the email (users.email CHECK), keep one global account per
# email (spec 3.1), rate-limit (spec 12.2).
@router.post("/auth/register")
def register(_: None = public()) -> None:
    raise not_implemented("1.9")


# TODO(G1 1.9 Swapnaneel): send real email; hashed, single-use, expiring token; rate-limit.
@router.post("/auth/verify-email")
def verify_email(_: None = public()) -> None:
    raise not_implemented("1.9")


# TODO(G1 1.9 Swapnaneel): generate the id server-side; tenant, ACTIVE member and owner pointer
# commit in one scoped_tx (AC-IAM-01). Never store the owner set as a grant (POL-13).
@router.post("/tenants")
def create_tenant(principal: Principal = identity()) -> None:
    raise not_implemented("1.9")


# TODO(G1 1.5 Rishav): this URL has no tenant and tenant_invitations is under RLS, so a no-context
# read finds nothing. Add a narrow trusted lookup by token hash, then use scoped_tx.
# TODO(G1 1.5 Rishav): consume the token and install grants atomically; verified email must match;
# recheck POL-02; existing users join without a new account (AC-IAM-03, 04, 05).
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
