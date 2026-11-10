"""Immutable raw-evidence store (spec 6.1, 11.4, AC-REC-02).

Original bytes are written before the row, under a content-addressed path, so normalized
values can never overwrite them. The row records the hash, size and retrieval context.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.config import get_settings
from app.errors import validation_failed

EvidenceType = Literal["WEBHOOK", "REPORT", "RECORD", "FILE"]


@dataclass(frozen=True)
class EvidenceRef:
    id: UUID
    content_hash: str
    storage_key: str
    byte_size: int


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else Path(get_settings().raw_evidence_dir)


def _write(root: Path, storage_key: str, data: bytes) -> None:
    target = root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def store_bytes(
    conn: Connection,
    *,
    tenant_id: UUID,
    source_id: UUID,
    data: bytes,
    evidence_type: EvidenceType,
    content_type: str,
    provider_event_id: str | None = None,
    retrieval_context: dict[str, Any] | None = None,
    source_timestamp: datetime | None = None,
    root: Path | None = None,
) -> EvidenceRef:
    """Store original bytes and the evidence row in the caller's tenant transaction."""
    if not data:
        raise validation_failed("Raw evidence must not be empty.")
    if not content_type:
        raise validation_failed("A content type is required.")
    digest = hashlib.sha256(data).hexdigest()
    storage_key = f"{tenant_id}/{source_id}/{digest}"
    _write(_root(root), storage_key, data)

    params = {
        "id": uuid4(),
        "tenant": tenant_id,
        "source": source_id,
        "type": evidence_type,
        "key": storage_key,
        "hash": digest,
        "size": len(data),
        "content_type": content_type,
        "event": provider_event_id,
        "context": json.dumps(retrieval_context or {}, default=str),
        "source_time": source_timestamp,
    }
    row = conn.execute(
        text(
            """
            INSERT INTO raw_evidence (id, tenant_id, source_id, evidence_type, storage_key,
                content_hash, byte_size, content_type, provider_event_id, retrieval_context,
                source_timestamp)
            VALUES (:id, :tenant, :source, :type, :key, :hash, :size, :content_type,
                :event, CAST(:context AS jsonb), :source_time)
            ON CONFLICT (tenant_id, source_id, content_hash) DO NOTHING
            RETURNING id, content_hash, storage_key, byte_size
            """
        ),
        params,
    ).first()
    if row is None:
        row = conn.execute(
            text(
                """
                SELECT id, content_hash, storage_key, byte_size FROM raw_evidence
                WHERE tenant_id = :tenant AND source_id = :source AND content_hash = :hash
                """
            ),
            params,
        ).one()
    return EvidenceRef(row.id, row.content_hash, row.storage_key, row.byte_size)
