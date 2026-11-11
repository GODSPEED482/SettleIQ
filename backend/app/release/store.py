"""Import signed envelopes from write-once storage into the database (spec 9.7.5).

    python -m app.release.store attestation <envelope.json>
    python -m app.release.store revocation <envelope.json>

Runs as the owner role (MIGRATION_DATABASE_URL): the runtime role can only read these tables.
An envelope is imported only if its signature verifies against a packaged key, and its lookup
columns are copied from the signed bytes. That is a convenience, not trust: the verifier checks
every envelope again on every use. A revocation state also raises the high-water mark, which
never falls, and must follow the newest stored state in its hash chain.
"""

import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, create_engine, text

from app.release import keys
from app.release.canonical import parse_canonical
from app.release.dsse import (
    ATTESTATION_TYPE,
    REVOCATION_TYPE,
    parse_envelope,
    verify_signature,
)
from app.release.payloads import Attestation, RevocationState


class ImportRefused(ValueError):
    pass


def _verified(data: bytes, payload_type: str) -> dict:
    envelope = parse_envelope(data)
    key = keys.packaged_keys().get(envelope.key_id)
    if key is None:
        raise ImportRefused(f"unknown key {envelope.key_id}")
    verify_signature(envelope, payload_type, key.public_key)
    if not key.valid_at(datetime.now(UTC)):
        raise ImportRefused(f"key {envelope.key_id} is outside its validity window")
    return parse_canonical(envelope.payload)


def import_attestation(conn: Connection, data: bytes) -> str:
    attestation = Attestation.model_validate(_verified(data, ATTESTATION_TYPE))
    scope = attestation.scope
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                 {"t": str(scope.tenant_id)})
    conn.execute(
        text(
            """
            INSERT INTO release_gate_attestations (id, tenant_id, connector_id,
                action_capability, envelope, envelope_sha256)
            VALUES (:id, :tenant, :connector, :capability, :envelope, :sha)
            ON CONFLICT (envelope_sha256) DO NOTHING
            """
        ),
        {"id": attestation.attestation_id, "tenant": scope.tenant_id,
         "connector": scope.connector_id, "capability": scope.capability, "envelope": data,
         "sha": hashlib.sha256(data).hexdigest()},
    )
    return str(attestation.attestation_id)


def import_revocation(conn: Connection, data: bytes) -> int:
    state = RevocationState.model_validate(_verified(data, REVOCATION_TYPE))
    newest = conn.execute(
        text("SELECT sequence, envelope_sha256 FROM release_revocation_states "
             "ORDER BY sequence DESC LIMIT 1 FOR UPDATE")
    ).one_or_none()
    expected = (newest.sequence + 1, newest.envelope_sha256) if newest else (1, "")
    if (state.sequence, state.previous_sha256) != expected:
        raise ImportRefused(f"state {state.sequence} does not follow the newest stored state")
    conn.execute(
        text("INSERT INTO release_revocation_states (sequence, envelope, envelope_sha256) "
             "VALUES (:sequence, :envelope, :sha)"),
        {"sequence": state.sequence, "envelope": data, "sha": hashlib.sha256(data).hexdigest()},
    )
    conn.execute(
        text("UPDATE release_revocation_high_water SET sequence = :s WHERE sequence < :s"),
        {"s": state.sequence},
    )
    return state.sequence


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] not in {"attestation", "revocation"}:
        raise SystemExit(__doc__)
    data = Path(argv[1]).read_bytes()
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.begin() as conn:
        if argv[0] == "attestation":
            result = import_attestation(conn, data)
        else:
            result = import_revocation(conn, data)
    print(f"imported {argv[0]} {result}")  # noqa: T201


if __name__ == "__main__":
    main(sys.argv[1:])
