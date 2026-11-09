"""Single-use tokens: random, high entropy and stored only as a hash (spec 11.2)."""

import hashlib
import secrets


def hash_token(raw: str) -> str:
    # A fast hash is enough: the input has 256 bits of entropy, so it cannot be guessed.
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """Return (raw, hash). Only the hash is stored; the raw value goes to its recipient."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
