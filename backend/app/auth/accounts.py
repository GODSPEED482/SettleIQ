"""Global accounts: registration, email verification and password checks (spec 3.1)."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import hash_token, new_token
from app.config import get_settings
from app.db import global_tx


@dataclass(frozen=True)
class Account:
    id: UUID
    email: str
    email_verified: bool


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _issue_verification_token(conn: Connection, user_id: UUID) -> str:
    """Retire any earlier link, so only the newest one works."""
    conn.execute(
        text(
            "UPDATE email_verification_tokens SET consumed_at = now() "
            "WHERE user_id = :user_id AND consumed_at IS NULL"
        ),
        {"user_id": user_id},
    )
    raw, token_hash = new_token()
    conn.execute(
        text(
            """
            INSERT INTO email_verification_tokens (id, user_id, token_hash, expires_at)
            VALUES (:id, :user_id, :token_hash, now() + :ttl)
            """
        ),
        {
            "id": uuid4(),
            "user_id": user_id,
            "token_hash": token_hash,
            "ttl": timedelta(hours=get_settings().email_token_ttl_hours),
        },
    )
    return raw


def register(email: str, password: str) -> str | None:
    """Create an account and return its raw verification token, or None if the email is taken.

    An existing account is left untouched. The password is hashed first either way, so both
    outcomes take the same time.
    """
    password_hash = hash_password(password)
    user_id = uuid4()
    with global_tx() as conn:
        created = conn.execute(
            text(
                "INSERT INTO users (id, email) VALUES (:id, :email) "
                "ON CONFLICT (email) DO NOTHING RETURNING id"
            ),
            {"id": user_id, "email": email},
        ).first()
        if created is None:
            return None
        conn.execute(
            text("INSERT INTO user_credentials (user_id, password_hash) VALUES (:id, :hash)"),
            {"id": user_id, "hash": password_hash},
        )
        return _issue_verification_token(conn, user_id)


def verify_email(raw: str) -> bool:
    """Consume a token atomically. An expired, used or superseded token returns False."""
    with global_tx() as conn:
        consumed = conn.execute(
            text(
                """
                UPDATE email_verification_tokens SET consumed_at = now()
                WHERE token_hash = :token_hash AND consumed_at IS NULL AND expires_at > now()
                RETURNING user_id
                """
            ),
            {"token_hash": hash_token(raw)},
        ).first()
        if consumed is None:
            return False
        conn.execute(
            text(
                "UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()) "
                "WHERE id = :id"
            ),
            {"id": consumed.user_id},
        )
    return True


def reissue_verification(email: str) -> str | None:
    """Return a fresh token for an unverified account, or None if there is nothing to verify."""
    with global_tx() as conn:
        user = conn.execute(
            text(
                """
                SELECT id FROM users
                WHERE email = :email AND email_verified_at IS NULL AND status = 'ACTIVE'
                FOR UPDATE
                """
            ),
            {"email": email},
        ).first()
        if user is None:
            return None
        return _issue_verification_token(conn, user.id)


def check_password(email: str, password: str) -> Account | None:
    """Return the account when the password matches. Unknown emails cost the same as wrong ones."""
    with global_tx() as conn:
        row = conn.execute(
            text(
                """
                SELECT u.id, u.email, u.email_verified_at, u.status, c.password_hash
                FROM users u JOIN user_credentials c ON c.user_id = u.id
                WHERE u.email = :email
                """
            ),
            {"email": email},
        ).first()
    # Argon2 is deliberately slow, so this runs outside the transaction.
    if not verify_password(row.password_hash if row else None, password):
        return None
    if row.status != "ACTIVE":
        return None
    return Account(row.id, row.email, row.email_verified_at is not None)
