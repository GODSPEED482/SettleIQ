"""Persist the canonical source-record envelope (spec 7.1, AC-REC-02).

A record may only reference raw evidence that is already stored for the same tenant and
source, and its `payload_hash` must match that evidence.
"""

import json
from uuid import UUID

from sqlalchemy import Connection, text

from app.errors import validation_failed
from app.sources.envelope import SourceRecordEnvelope


def _check_evidence(conn: Connection, envelope: SourceRecordEnvelope) -> None:
    row = conn.execute(
        text(
            """
            SELECT content_hash FROM raw_evidence
            WHERE id = :id AND tenant_id = :tenant AND source_id = :source
            """
        ),
        {
            "id": envelope.raw_evidence_id,
            "tenant": envelope.tenant_id,
            "source": envelope.source_id,
        },
    ).first()
    if row is None:
        raise validation_failed("Raw evidence does not belong to this tenant and source.")
    if row.content_hash != envelope.payload_hash:
        raise validation_failed("payload_hash does not match the stored raw evidence.")


def _check_object(conn: Connection, envelope: SourceRecordEnvelope) -> None:
    row = conn.execute(
        text(
            """
            SELECT 1 FROM source_objects
            WHERE id = :id AND tenant_id = :tenant AND source_id = :source
            """
        ),
        {
            "id": envelope.source_object_id,
            "tenant": envelope.tenant_id,
            "source": envelope.source_id,
        },
    ).first()
    if row is None:
        raise validation_failed("Source object does not belong to this tenant and source.")


def persist_record(conn: Connection, envelope: SourceRecordEnvelope) -> UUID:
    """Insert one canonical record in the caller's tenant transaction."""
    _check_evidence(conn, envelope)
    _check_object(conn, envelope)

    params = {
        "id": envelope.id,
        "tenant": envelope.tenant_id,
        "source": envelope.source_id,
        "object": envelope.source_object_id,
        "external_id": envelope.external_id,
        "record_type": envelope.record_type,
        "attributes": json.dumps(envelope.attributes, default=str),
        "evidence": envelope.raw_evidence_id,
        "occurred_at": envelope.occurred_at,
        "observed_at": envelope.observed_at,
        "payload_hash": envelope.payload_hash,
        "connector_version": envelope.connector_version,
        "mapping_version": envelope.mapping_version,
    }
    inserted = conn.execute(
        text(
            """
            INSERT INTO source_records (id, tenant_id, source_id, source_object_id,
                external_id, record_type, attributes, raw_evidence_id, occurred_at,
                observed_at, payload_hash, connector_version, mapping_version)
            VALUES (:id, :tenant, :source, :object, :external_id, :record_type,
                CAST(:attributes AS jsonb), :evidence, :occurred_at, :observed_at,
                :payload_hash, :connector_version, :mapping_version)
            ON CONFLICT (tenant_id, source_id, source_object_id, external_id) DO NOTHING
            RETURNING id
            """
        ),
        params,
    ).first()
    if inserted is not None:
        return inserted.id
    # The only reachable conflict is a repeated non-null external_id.
    existing = conn.execute(
        text(
            """
            SELECT id FROM source_records
            WHERE tenant_id = :tenant AND source_id = :source
              AND source_object_id = :object AND external_id = :external_id
            """
        ),
        params,
    ).one()
    return existing.id
