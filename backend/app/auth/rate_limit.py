"""Fixed-window rate limits in PostgreSQL, since this build has no Redis (spec 11.2, 12.2).

Each hit commits on its own, so a request that later fails still counts.
"""

from dataclasses import dataclass

from sqlalchemy import text

from app.auth.tokens import hash_token
from app.db import global_tx
from app.errors import rate_limited


@dataclass(frozen=True)
class Limit:
    name: str
    hits: int
    window_seconds: int


REGISTER_BY_ADDRESS = Limit("register:address", 10, 3600)
REGISTER_BY_EMAIL = Limit("register:email", 5, 3600)
VERIFY_BY_ADDRESS = Limit("verify:address", 30, 3600)
RESEND_BY_ADDRESS = Limit("resend:address", 10, 3600)
RESEND_BY_EMAIL = Limit("resend:email", 5, 3600)
SIGN_IN_BY_ADDRESS = Limit("sign-in:address", 30, 900)
SIGN_IN_BY_EMAIL = Limit("sign-in:email", 10, 900)

_HIT_SQL = text(
    """
    INSERT INTO auth_rate_limits (bucket, window_start, hits)
    VALUES (:bucket, to_timestamp(floor(extract(epoch FROM now()) / :seconds) * :seconds), 1)
    ON CONFLICT (bucket, window_start) DO UPDATE SET hits = auth_rate_limits.hits + 1
    RETURNING hits
    """
)


def enforce(limit: Limit, subject: str) -> None:
    # Hashed so that email addresses are not stored in the bucket key.
    bucket = f"{limit.name}:{hash_token(subject)}"
    with global_tx() as conn:
        hits = conn.execute(_HIT_SQL, {"bucket": bucket, "seconds": limit.window_seconds}).scalar()
    if hits > limit.hits:
        raise rate_limited()
