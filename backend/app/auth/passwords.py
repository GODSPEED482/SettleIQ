"""Password hashing with Argon2id (spec 11.2)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Verified when the account does not exist, so an unknown email costs the same as a wrong password.
_DECOY = _hasher.hash("settleiq-decoy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        matched = _hasher.verify(stored_hash or _DECOY, password)
    except (VerificationError, InvalidHashError):
        return False
    return matched and stored_hash is not None
