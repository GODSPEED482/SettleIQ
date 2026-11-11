"""POL-14 runtime verification (spec 9.7.6). Owner: Rishav, task 5.4.

Before every LIVE switch and every execution grant, one exact scope must be covered by a stored
attestation that passes all ten checks below, in order. Any failure is a POL-14 denial naming
the failing check. Database rows only locate candidates: the signature is checked over the
exact stored bytes, and scope is read only from those bytes.

A valid attestation certifies a build; it never authorizes execution by itself (spec 9.7.7).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Connection, text

from app.execution.scope import ExactScope
from app.release import capability_manifest, keys, payloads, runtime_identity
from app.release.canonical import NotCanonical, parse_canonical
from app.release.dsse import (
    ATTESTATION_TYPE,
    REVOCATION_TYPE,
    Envelope,
    EnvelopeError,
    parse_envelope,
    verify_signature,
)
from app.release.keys import PublicKey
from app.release.payloads import Attestation, RevocationState

MAX_WINDOW = timedelta(hours=24)
CHECKS = (
    "SIGNATURE", "KEY_STATUS", "VALIDITY", "REVOCATION", "IMAGE_DIGEST", "MANIFEST_HASH",
    "CAPABILITY_MEMBERSHIP", "CAPABILITY_VERSION", "TEST_EVIDENCE", "EXACT_SCOPE",
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    check: str | None = None
    attestation_id: UUID | None = None


class Denied(Exception):
    def __init__(self, check: str) -> None:
        super().__init__(check)
        self.check = check


def _now() -> datetime:
    return datetime.now(UTC)


def _signed(data: bytes, payload_type: str, now: datetime) -> tuple[Envelope, object]:
    """Checks 1 and 2: a packaged key, inside its window, signed these exact bytes."""
    try:
        envelope = parse_envelope(data)
    except EnvelopeError:
        raise Denied("SIGNATURE") from None
    key: PublicKey | None = keys.packaged_keys().get(envelope.key_id)
    if key is None:
        raise Denied("KEY_STATUS")
    try:
        verify_signature(envelope, payload_type, key.public_key)
        value = parse_canonical(envelope.payload)
    except (EnvelopeError, NotCanonical):
        raise Denied("SIGNATURE") from None
    if not key.valid_at(now):
        raise Denied("KEY_STATUS")
    return envelope, value


def _window_ok(issued_at: datetime, expires_at: datetime, now: datetime) -> bool:
    return issued_at <= now < expires_at and expires_at - issued_at <= MAX_WINDOW


def _revocation_state(conn: Connection, now: datetime) -> RevocationState:
    """Check 4's input: the newest stored state, signed, current and not below the high-water
    mark. Anything else denies, including no state at all."""
    row = conn.execute(
        text("SELECT sequence, envelope FROM release_revocation_states "
             "ORDER BY sequence DESC LIMIT 1")
    ).one_or_none()
    high_water = conn.execute(
        text("SELECT sequence FROM release_revocation_high_water")
    ).scalar_one_or_none() or 0
    if row is None:
        raise Denied("REVOCATION")
    try:
        envelope, value = _signed(bytes(row.envelope), REVOCATION_TYPE, now)
        state = RevocationState.model_validate(value)
    except (Denied, ValidationError):
        raise Denied("REVOCATION") from None
    if (state.sequence != row.sequence or state.sequence < high_water
            or state.key_id != envelope.key_id
            or not _window_ok(state.issued_at, state.expires_at, now)):
        raise Denied("REVOCATION")
    return state


def check_attestation(conn: Connection, data: bytes, scope: ExactScope,
                      digest: Callable[[], str | None] | None = None,
                      now: datetime | None = None) -> Attestation:
    """Run all ten checks on one stored envelope for one exact scope, or raise Denied."""
    now = now or _now()
    digest = digest or runtime_identity.running_digest
    envelope, value = _signed(data, ATTESTATION_TYPE, now)
    try:
        attestation = Attestation.model_validate(value)
    except ValidationError:
        # Zero or several scopes, a wildcard or a missing tenant (AC-REL-05).
        raise Denied("CAPABILITY_MEMBERSHIP") from None
    if attestation.key_id != envelope.key_id:
        raise Denied("SIGNATURE")
    if not _window_ok(attestation.issued_at, attestation.expires_at, now):
        raise Denied("VALIDITY")
    if attestation.attestation_id in _revocation_state(conn, now).revoked:
        raise Denied("REVOCATION")
    running = digest()
    if running is None or running != attestation.subject.digest:
        raise Denied("IMAGE_DIGEST")
    if attestation.capability_manifest_sha256 != capability_manifest.packaged_sha256():
        raise Denied("MANIFEST_HASH")
    certified = attestation.scope
    if certified.capability != scope.action_capability:
        raise Denied("CAPABILITY_MEMBERSHIP")
    entry = capability_manifest.packaged_entry(certified.connector_id, certified.capability)
    if entry is None or entry["kind"] != "action" or (
        entry["version"], entry["contract_version"], entry["implementation"]["source_sha256"],
        entry["connector_version"], entry["dependencies"],
    ) != (
        certified.capability_version, certified.contract_version,
        certified.implementation_sha256, certified.connector_version,
        [d.model_dump() for d in certified.dependencies],
    ):
        raise Denied("CAPABILITY_VERSION")
    suite = payloads.packaged_suites().get((certified.connector_id, certified.capability))
    evidence = attestation.test_suite
    if (suite is None or suite.empty_categories() or evidence.result != "PASS"
            or evidence.skipped != 0
            or (evidence.suite_id, evidence.version) != (suite.suite_id, suite.version)):
        raise Denied("TEST_EVIDENCE")
    if (str(certified.tenant_id), certified.domain_pack_id, certified.domain_pack_version,
            certified.connector_id, certified.capability) != (
            str(scope.tenant_id), scope.domain_pack_id, scope.domain_pack_version,
            scope.connector_id, scope.action_capability):
        raise Denied("EXACT_SCOPE")
    return attestation


def verify(conn: Connection, scope: ExactScope,
           digest: Callable[[], str | None] | None = None) -> Verdict:
    """The newest stored attestation for this scope that passes, or the newest one's failing
    check. With none stored the denial is at check 1."""
    rows = conn.execute(
        text(
            """
            SELECT envelope FROM release_gate_attestations
            WHERE connector_id = :connector AND action_capability = :capability
            ORDER BY imported_at DESC, id
            """
        ),
        {"connector": scope.connector_id, "capability": scope.action_capability},
    ).all()
    first: str | None = None
    for row in rows:
        try:
            attestation = check_attestation(conn, bytes(row.envelope), scope, digest)
        except Denied as denied:
            first = first or denied.check
            continue
        return Verdict(True, None, attestation.attestation_id)
    return Verdict(False, first or "SIGNATURE")
