"""How API reads, events and files map onto the source-record envelope (spec 7.1, OPEN-04).

Every adapter follows these, so 3.6 (files), 4.2 (API reads) and 4.3 (webhooks) agree:

- API read: one `RECORD` evidence per response body, provenance `provider_api`. One `API`
  source object per capability, named by its canonical key (`captures.read`). `external_id`
  is the provider resource ID. A re-read with a changed body is a new observation record.
- Event: one `WEBHOOK` evidence per verified delivery, with `provider_event_id` set and
  provenance `provider_event`. One `EVENT` source object per source, named `events`.
  `external_id` is the provider event ID, `record_type` is `<connector>:event/<EVENT.TYPE>`,
  and `occurred_at` is the provider's create time. The resource the event is about stays in
  `attributes`; linking it to an entity is projection's job (spec 7.2).
- File: one `FILE` or `REPORT` evidence per file. One `CSV` source object per distinct file,
  named `{name}@{first 16 hex of its sha256}` with `checksum` set to the full hash. Each row is
  one record with `external_id` `row:{n}` (1-based data row), `attributes` keyed by the
  original column names with unknown columns kept, and `payload_hash` equal to the file hash.
- Database read: one `RECORD` evidence per discovered table response, provenance `database`.
  Each row is one record with `external_id` derived from the table's primary key and
  `record_type` `<connector>:table/<schema>.<table>`.

Event and file records always carry an `external_id`, so a replay or re-upload collapses onto
the existing record instead of duplicating it. A database row carries its primary key when it
has one; a table without a key is still preserved as an observation.
"""

import re
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.sources.envelope import RECORD_TYPE

ObjectType = Literal["TABLE", "VIEW", "CSV", "API", "EVENT"]

EVENT_OBJECT = "events"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def event_record_type(connector_id: str, event_type: str) -> str:
    record_type = f"{connector_id}:event/{event_type}"
    if not event_type or not re.fullmatch(RECORD_TYPE, record_type):
        raise ValueError(f"not a valid event record type: {record_type!r}")
    return record_type


def database_record_type(connector_id: str, schema: str, table: str) -> str:
    """A row read from a discovered relation: the connector, schema and table stay visible."""
    record_type = f"{connector_id}:table/{schema}.{table}"
    if not schema or not table or not re.fullmatch(RECORD_TYPE, record_type):
        raise ValueError(f"not a valid database record type: {record_type!r}")
    return record_type


def file_record_type(connector_id: str, kind: str) -> str:
    """A row from an uploaded file, identified by its connector and report kind."""
    record_type = f"{connector_id}:file/{kind}"
    if not kind or not re.fullmatch(RECORD_TYPE, record_type):
        raise ValueError(f"not a valid file record type: {record_type!r}")
    return record_type


def file_object_name(name: str, content_hash: str) -> str:
    if not name or not _SHA256.fullmatch(content_hash):
        raise ValueError("a file object needs a name and a sha256 content hash")
    return f"{name}@{content_hash[:16]}"


def file_row_identity(row_number: int) -> str:
    if row_number < 1:
        raise ValueError("data rows are numbered from 1")
    return f"row:{row_number}"


def ensure_object(
    conn: Connection,
    *,
    tenant_id: UUID,
    source_id: UUID,
    object_name: str,
    object_type: ObjectType,
    checksum: str | None = None,
) -> UUID:
    """Return the source object with this name, creating it in the caller's transaction."""
    params = {
        "id": uuid4(),
        "tenant": tenant_id,
        "source": source_id,
        "name": object_name,
        "type": object_type,
        "checksum": checksum,
    }
    row = conn.execute(
        text(
            """
            INSERT INTO source_objects (id, tenant_id, source_id, object_name, object_type,
                checksum)
            VALUES (:id, :tenant, :source, :name, :type, :checksum)
            ON CONFLICT (tenant_id, source_id, object_name) DO NOTHING
            RETURNING id
            """
        ),
        params,
    ).first()
    if row is None:
        row = conn.execute(
            text(
                """
                SELECT id, object_type FROM source_objects
                WHERE tenant_id = :tenant AND source_id = :source AND object_name = :name
                """
            ),
            params,
        ).one()
        if row.object_type != object_type:
            raise ValueError(f"{object_name!r} is already registered as {row.object_type}")
    return row.id
