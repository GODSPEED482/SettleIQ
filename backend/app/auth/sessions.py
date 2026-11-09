"""Opaque server-side sessions (spec 11.2).

The cookie carries a random token and nothing else. User status and email verification are
read from current state on every request, so stale claims cannot preserve access (AC-IAM-07).
"""

from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Response
from sqlalchemy import text

from app.auth.tokens import hash_token, new_token
from app.authz.access import Principal, PrincipalKind
from app.config import get_settings
from app.db import global_tx

_PRINCIPAL_SQL = text(
    """
    SELECT u.id, u.email_verified_at
    FROM auth_sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token_hash = :token_hash AND s.revoked_at IS NULL AND s.expires_at > now()
      AND u.status = 'ACTIVE'
    """
)


def _cookie_is_secure() -> bool:
    return get_settings().environment != "development"


def session_cookie_name() -> str:
    # The __Host- prefix requires Secure, which plain-HTTP local development cannot satisfy.
    return "__Host-settleiq_session" if _cookie_is_secure() else "settleiq_session"


def allowed_origin() -> str:
    parts = urlsplit(get_settings().app_base_url)
    return f"{parts.scheme}://{parts.netloc}"


def create_session(user_id: UUID) -> str:
    raw, token_hash = new_token()
    with global_tx() as conn:
        conn.execute(
            text(
                """
                INSERT INTO auth_sessions (id, user_id, token_hash, expires_at)
                VALUES (:id, :user_id, :token_hash, now() + :ttl)
                """
            ),
            {
                "id": uuid4(),
                "user_id": user_id,
                "token_hash": token_hash,
                "ttl": timedelta(hours=get_settings().session_ttl_hours),
            },
        )
    return raw


def session_principal(raw: str) -> Principal | None:
    with global_tx() as conn:
        row = conn.execute(_PRINCIPAL_SQL, {"token_hash": hash_token(raw)}).first()
    if row is None:
        return None
    return Principal(row.id, PrincipalKind.HUMAN, email_verified=row.email_verified_at is not None)


def revoke_session(raw: str) -> None:
    with global_tx() as conn:
        conn.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = now() "
                "WHERE token_hash = :token_hash AND revoked_at IS NULL"
            ),
            {"token_hash": hash_token(raw)},
        )


def set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        session_cookie_name(),
        raw,
        max_age=get_settings().session_ttl_hours * 3600,
        path="/",
        secure=_cookie_is_secure(),
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=_cookie_is_secure(),
        httponly=True,
        samesite="lax",
    )
