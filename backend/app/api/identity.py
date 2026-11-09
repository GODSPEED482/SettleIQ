"""Registration, sign-in, tenant creation and the caller's own permissions (spec 3.1, 12.3)."""

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text

from app.auth import accounts, rate_limit, sessions
from app.authz.access import AccessGateway, Principal, get_gateway
from app.authz.guard import identity, public
from app.config import get_settings
from app.db import global_tx
from app.errors import invalid_credentials, invalid_token, not_found
from app.mail import MailDeliveryError, MailSender, get_mail_sender
from app.tenancy import invitations
from app.tenancy.creation import create_tenant as create_tenant_atomically

logger = logging.getLogger("settleiq.auth")

router = APIRouter(prefix="/api", tags=["identity"])

ACCEPTED = {"status": "accepted"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterBody(_Body):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class VerifyEmailBody(_Body):
    token: str = Field(min_length=1, max_length=256)


class ResendVerificationBody(_Body):
    email: EmailStr


class SignInBody(_Body):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class CreateTenantBody(_Body):
    name: str = Field(min_length=1, max_length=200)
    industry_labels: list[str] = []


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _deliver_verification(mail: MailSender, email: str, raw_token: str) -> None:
    # In the URL fragment, so the token stays out of server logs and Referer headers.
    link = f"{get_settings().app_base_url}/verify-email#token={raw_token}"
    try:
        mail.send(email, "Verify your SettleIQ email", f"Confirm your email address:\n\n{link}\n")
    except MailDeliveryError as error:
        # The account stays unverified, and resend-verification can retry.
        logger.warning("verification email not delivered: %s", error)


# Runs after the response, so timing cannot reveal whether an email address is registered.
@router.post("/auth/register", status_code=202)
def register(
    body: RegisterBody,
    request: Request,
    background: BackgroundTasks,
    mail: MailSender = Depends(get_mail_sender),
    _: None = public(),
) -> dict:
    email = accounts.normalize_email(body.email)
    rate_limit.enforce(rate_limit.REGISTER_BY_ADDRESS, _client_address(request))
    rate_limit.enforce(rate_limit.REGISTER_BY_EMAIL, email)
    raw_token = accounts.register(email, body.password)
    if raw_token is not None:
        background.add_task(_deliver_verification, mail, email, raw_token)
    return ACCEPTED


@router.post("/auth/verify-email")
def verify_email(body: VerifyEmailBody, request: Request, _: None = public()) -> dict:
    rate_limit.enforce(rate_limit.VERIFY_BY_ADDRESS, _client_address(request))
    if not accounts.verify_email(body.token):
        raise invalid_token()
    return {"status": "verified"}


@router.post("/auth/resend-verification", status_code=202)
def resend_verification(
    body: ResendVerificationBody,
    request: Request,
    background: BackgroundTasks,
    mail: MailSender = Depends(get_mail_sender),
    _: None = public(),
) -> dict:
    email = accounts.normalize_email(body.email)
    rate_limit.enforce(rate_limit.RESEND_BY_ADDRESS, _client_address(request))
    rate_limit.enforce(rate_limit.RESEND_BY_EMAIL, email)
    raw_token = accounts.reissue_verification(email)
    if raw_token is not None:
        background.add_task(_deliver_verification, mail, email, raw_token)
    return ACCEPTED


@router.post("/auth/sign-in")
def sign_in(body: SignInBody, request: Request, response: Response, _: None = public()) -> dict:
    email = accounts.normalize_email(body.email)
    rate_limit.enforce(rate_limit.SIGN_IN_BY_ADDRESS, _client_address(request))
    rate_limit.enforce(rate_limit.SIGN_IN_BY_EMAIL, email)
    account = accounts.check_password(email, body.password)
    if account is None:
        raise invalid_credentials()
    sessions.set_session_cookie(response, sessions.create_session(account.id))
    return {
        "user": {
            "id": str(account.id),
            "email": account.email,
            "email_verified": account.email_verified,
        }
    }


# Public, so a member whose email is unverified can still end a session.
@router.post("/auth/sign-out")
def sign_out(request: Request, response: Response, _: None = public()) -> dict:
    raw = request.cookies.get(sessions.session_cookie_name())
    if raw:
        sessions.revoke_session(raw)
    sessions.clear_session_cookie(response)
    return {"status": "signed_out"}


@router.post("/tenants", status_code=201)
def create_tenant(body: CreateTenantBody, principal: Principal = identity()) -> dict:
    tenant_id = create_tenant_atomically(principal, body.name, body.industry_labels)
    return {"id": str(tenant_id), "name": body.name}


@router.post("/invitations/{token}/accept")
def accept_invitation(
    token: str,
    principal: Principal = identity(),
    gateway: AccessGateway = Depends(get_gateway),
) -> dict:
    return invitations.accept_invitation(principal, token, gateway)


# RLS hides memberships until a tenant is selected, so this reads through a trusted lookup.
@router.get("/me/tenants")
def my_tenants(principal: Principal = identity()) -> dict:
    with global_tx() as conn:
        rows = conn.execute(
            text("SELECT tenant_id, name FROM tenants_for_user(:user_id)"),
            {"user_id": principal.id},
        ).all()
    return {"tenants": [{"id": str(row.tenant_id), "name": row.name} for row in rows]}


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
