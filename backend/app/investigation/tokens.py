"""Delegated tool tokens (POL-08). Owner: Swapnaneel, task 5.7.

A token carries identity only, so permissions are resolved again on every tool call and
revocation applies mid-run. It is minted when a job starts, so it cannot expire in the queue.

The dispatcher mints it and the tool API verifies it, both with AGENT_TOKEN_SECRET, which the
agent never receives: the agent can only pass the token on. The form is
`v1.<payload>.<HMAC-SHA256>`, both base64url; an empty secret refuses every token.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.config import get_settings

VERSION = "v1"


class TokenError(ValueError):
    pass


@dataclass(frozen=True)
class DelegatedToken:
    investigation_id: UUID
    tenant_id: UUID
    case_id: UUID
    invoker_id: UUID
    expires_at: datetime


def _secret() -> bytes:
    secret = get_settings().agent_token_secret
    if len(secret) < 32:
        raise TokenError("AGENT_TOKEN_SECRET is not configured")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(message: str) -> str:
    return _b64(hmac.new(_secret(), message.encode(), hashlib.sha256).digest())


def mint_token(token: DelegatedToken) -> str:
    payload = json.dumps(
        {
            "inv": str(token.investigation_id),
            "ten": str(token.tenant_id),
            "case": str(token.case_id),
            "sub": str(token.invoker_id),
            "exp": int(token.expires_at.timestamp()),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    message = f"{VERSION}.{_b64(payload.encode())}"
    return f"{message}.{_sign(message)}"


def verify_token(raw: str) -> DelegatedToken:
    try:
        version, payload, signature = raw.split(".")
    except ValueError:
        raise TokenError("malformed token") from None
    if version != VERSION:
        raise TokenError("unknown token version")
    if not hmac.compare_digest(signature, _sign(f"{version}.{payload}")):
        raise TokenError("bad signature")
    try:
        claims = json.loads(_unb64(payload))
        token = DelegatedToken(
            UUID(claims["inv"]), UUID(claims["ten"]), UUID(claims["case"]), UUID(claims["sub"]),
            datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    except (ValueError, KeyError, TypeError):
        raise TokenError("malformed claims") from None
    if token.expires_at <= datetime.now(UTC):
        raise TokenError("expired")
    return token
