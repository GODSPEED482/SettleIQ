"""Write-only credential intake store (spec v1.7 §11.3, AC-PP-14, OPEN-PP-03).

A tenant's Integration Admin submits a provider credential once, and nothing ever reads it back
out through the API. It is sealed with AES-256-GCM under SOURCE_SECRET_KEY, which only the api
process holds and the database never sees, and the ciphertext lives in
`source_credential_values` under the tenant's RLS. The associated data names the tenant and the
source, so a ciphertext moved to another row or tenant fails to open. The source then holds the
reference `stored:<source_id>`, which app/sources/secrets.py resolves within the tenant.

The key's fingerprint is stored beside each row, so a changed key fails closed rather than
decrypting to garbage. Every failure here reads as "no value": the caller reports the reference
as unresolvable and never learns, or echoes, why.
"""

import hashlib
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import scoped_tx

REFERENCE_PREFIX = "stored:"


def reference_for(source_id: UUID) -> str:
    return f"{REFERENCE_PREFIX}{source_id}"


def _key() -> bytes | None:
    raw = get_settings().source_secret_key.strip()
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def available() -> bool:
    return _key() is not None


def _fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"settleiq-source-secret-key:" + key).hexdigest()[:16]


def _associated_data(tenant_id: UUID, source_id: UUID) -> bytes:
    return f"settleiq/source-credential/{tenant_id}/{source_id}".encode()


def store(conn: Connection, tenant_id: UUID, source_id: UUID, value: str) -> None:
    """Seal and upsert one source's credential inside the caller's tenant transaction."""
    key = _key()
    if key is None:
        raise RuntimeError("source secret key is not configured")
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, value.encode(), _associated_data(tenant_id, source_id))
    conn.execute(
        text(
            """
            INSERT INTO source_credential_values
                (tenant_id, source_id, nonce, ciphertext, key_fingerprint)
            VALUES (:tenant, :source, :nonce, :sealed, :fingerprint)
            ON CONFLICT (tenant_id, source_id) DO UPDATE
            SET nonce = EXCLUDED.nonce, ciphertext = EXCLUDED.ciphertext,
                key_fingerprint = EXCLUDED.key_fingerprint, updated_at = now()
            """
        ),
        {
            "tenant": tenant_id,
            "source": source_id,
            "nonce": nonce,
            "sealed": sealed,
            "fingerprint": _fingerprint(key),
        },
    )


def discard(conn: Connection, source_id: UUID) -> None:
    """Delete a source's stored credential, inside the caller's tenant transaction."""
    conn.execute(
        text("DELETE FROM source_credential_values WHERE source_id = :source"),
        {"source": source_id},
    )


def stored_value(tenant_id: UUID, source_id: UUID) -> str:
    key = _key()
    if key is None:
        return ""
    with scoped_tx(tenant_id) as conn:
        row = conn.execute(
            text(
                "SELECT nonce, ciphertext, key_fingerprint FROM source_credential_values "
                "WHERE source_id = :source"
            ),
            {"source": source_id},
        ).first()
    if row is None or row.key_fingerprint != _fingerprint(key):
        return ""
    try:
        opened = AESGCM(key).decrypt(
            bytes(row.nonce), bytes(row.ciphertext), _associated_data(tenant_id, source_id)
        )
    except InvalidTag:
        return ""
    return opened.decode()
