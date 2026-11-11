"""POL-14 release attestation (task 5.4; spec 9.7, AC-REL-01..10, AC-AUTHZ-16).

Envelopes are made by the real signer, .github/attest/sign.py, with a key generated per test
session; the packaged key list and suites are patched through their loaders, never through the
environment. Stored copies go in through the owner-role import CLI, as on the host.
"""

import base64
import hashlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.connectors import implementations
from app.db import scoped_tx
from app.execution.scope import ExactScope
from app.release import (
    capability_manifest,
    keys,
    payloads,
    runtime_identity,
    store,
    verifier,
)
from app.release.canonical import NotCanonical, canonical_bytes, parse_canonical
from app.release.dsse import (
    ATTESTATION_TYPE,
    REVOCATION_TYPE,
    EnvelopeError,
    parse_envelope,
    verify_signature,
)
from app.release.payloads import Suite
from tests.test_semantics_store import Member

REPO = Path(__file__).parents[2]
VECTORS = json.loads((Path(__file__).parent / "vectors" / "release.json").read_text(
    encoding="utf-8"))
_spec = importlib.util.spec_from_file_location("sign", REPO / ".github/attest/sign.py")
sign = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sign)
_spec = importlib.util.spec_from_file_location("check_layers",
                                               REPO / ".github/attest/check_layers.py")
check_layers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_layers)

KEY = Ed25519PrivateKey.generate()
KEY_ID = f"test-{uuid4().hex[:8]}"
DIGEST = "sha256:" + "a" * 64
CONNECTOR, CAPABILITY = "paypal", "refunds.create"
NODE = "tests/test_paypal_g4.py::test_refund"


def public_key(valid_from: datetime | None = None, days: int = 30) -> keys.PublicKey:
    start = valid_from or datetime.now(UTC) - timedelta(days=1)
    return keys.PublicKey(KEY_ID, KEY.public_key().public_bytes_raw(), start,
                          start + timedelta(days=days))


def filled_suite() -> Suite:
    return Suite(capability=CAPABILITY, connector_id=CONNECTOR, suite_id="paypal-refunds-create",
                 version=1, categories={c: [NODE] for c in payloads.CATEGORIES})


@pytest.fixture(autouse=True)
def packaged(monkeypatch):
    """The test key and a complete suite, as a provisioned image would package them."""
    monkeypatch.setattr(keys, "packaged_keys", lambda: {KEY_ID: public_key()})
    monkeypatch.setattr(payloads, "packaged_suites",
                        lambda: {(CONNECTOR, CAPABILITY): filled_suite()})
    monkeypatch.setattr(runtime_identity, "running_digest", lambda: DIGEST)


def junit(*cases: tuple[str, str]) -> bytes:
    body = "".join(
        f'<testcase classname="{node.split("::")[0][:-3].replace("/", ".")}" '
        f'name="{node.split("::")[1]}">{extra}</testcase>'
        for node, extra in cases
    )
    return f"<testsuites><testsuite>{body}</testsuite></testsuites>".encode()


PASSING = junit((NODE, ""))


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def attestation_payload(tenant: UUID, **changes: Any) -> dict[str, Any]:
    manifest = json.loads(capability_manifest.packaged_bytes())
    pack = {"namespace": "finops", "version": "0.2.0"}
    scope = sign.certified_scope(manifest, pack, str(tenant), CONNECTOR, CAPABILITY)
    evidence = sign.check_evidence(PASSING, filled_suite().model_dump())
    issued = now() - timedelta(minutes=1)
    payload = {
        "schema": "settleiq.release-attestation/1",
        "attestation_id": str(uuid4()),
        "subject": {"image": "registry/settleiq-api", "digest": DIGEST},
        "build": {"commit": "f" * 40, "run_id": "1", "workflow": "attest.yml@refs/heads/prod"},
        "capability_manifest_sha256": capability_manifest.packaged_sha256(),
        "certified_scopes": [scope],
        "test_suite": {**evidence, "harness_digest": "sha256:" + "b" * 64},
        "issued_at": sign.timestamp(issued),
        "expires_at": sign.timestamp(issued + timedelta(hours=23)),
        "key_id": KEY_ID,
    }
    return payload | changes


def signed(payload: dict[str, Any], payload_type: str = ATTESTATION_TYPE,
           key: Ed25519PrivateKey = KEY, key_id: str = KEY_ID) -> bytes:
    return sign.envelope(payload_type, sign.canonical(payload), key_id, key)


def scope_for(tenant: UUID, **changes: Any) -> ExactScope:
    values = {"tenant_id": tenant, "domain_pack_id": "finops", "domain_pack_version": "0.2.0",
              "connector_id": CONNECTOR, "action_capability": CAPABILITY}
    return ExactScope(**(values | changes))


# AC-REL-01: the manifest


def test_the_manifest_is_byte_identical_across_generation():
    assert capability_manifest.generated_bytes() == capability_manifest.generated_bytes()
    assert capability_manifest.packaged_bytes() == capability_manifest.generated_bytes()


def test_each_entry_names_its_implementation_and_source_hash():
    manifest = json.loads(capability_manifest.packaged_bytes())
    [refund] = [e for e in manifest["capabilities"] if e["capability"] == CAPABILITY]
    source = (Path(__file__).parents[1] / "app/connectors/paypal/refunds.py").read_bytes()
    assert refund["implementation"] == {
        "entrypoint": "app.connectors.paypal.refunds:create_refund",
        "source_sha256": hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest(),
    }
    assert [d["capability"] for d in refund["dependencies"]] == ["captures.read", "refunds.read"]
    assert refund["domain_packs"] == ["finops"]


def test_a_declared_action_with_no_implementation_fails(monkeypatch):
    monkeypatch.setattr(capability_manifest, "IMPLEMENTATIONS", tuple(
        i for i in implementations.IMPLEMENTATIONS
        if i.capability != "merchant.payment_status.correct"))
    with pytest.raises(capability_manifest.ManifestError, match="no implementation"):
        capability_manifest.generate()


def test_a_pending_capability_is_never_in_the_manifest():
    listed = {(e["connector_id"], e["capability"])
              for e in json.loads(capability_manifest.packaged_bytes())["capabilities"]}
    assert not listed & implementations.PENDING_IMPLEMENTATIONS


def test_startup_refuses_a_stale_manifest(monkeypatch):
    monkeypatch.setattr(capability_manifest, "packaged_bytes", lambda: b'{"stale":true}')
    with pytest.raises(capability_manifest.ManifestError, match="differs"):
        capability_manifest.check_packaged()


# AC-REL-02: the signer refuses


@pytest.mark.parametrize(("report", "suite_change", "reason"), [
    (PASSING, {"idempotency": []}, "empty"),
    (junit((NODE, "<failure/>")), {}, "did not pass"),
    (junit((NODE, "<error/>")), {}, "did not pass"),
    (junit((NODE, ""), ("tests/test_other.py::test_x", "<skipped/>")), {}, "did not pass"),
    (junit(("tests/test_other.py::test_x", "")), {}, "did not run"),
])
def test_the_signer_refuses_failed_skipped_missing_or_empty(report, suite_change, reason):
    suite = filled_suite().model_dump()
    suite["categories"] |= suite_change
    with pytest.raises(sign.Refused, match=reason):
        sign.check_evidence(report, suite)


def test_the_packaged_suites_fill_every_category_and_need_every_test():
    """Tasks 5.12 and 5.17: both certified capabilities declare every category, and a report
    missing any listed test still refuses issuance."""
    suites = payloads.parse_suites(payloads.SUITES_FILE.read_text("utf-8"))
    assert set(suites) == {("paypal", "refunds.create"),
                           ("internal-merchant", "merchant.payment_status.correct")}
    for suite in suites.values():
        assert suite.empty_categories() == []
        with pytest.raises(sign.Refused, match="did not run"):
            sign.check_evidence(PASSING, suite.model_dump())


def test_a_parametrized_test_counts_for_its_node():
    report = junit((NODE + "[a]", ""), (NODE + "[b]", ""))
    assert sign.check_evidence(report, filled_suite().model_dump())["skipped"] == 0


# AC-REL-03: the harness only adds layers


def test_the_harness_must_extend_the_subject_unchanged():
    check_layers.check(["l1", "l2"], ["l1", "l2", "tests"])
    for harness in (["l1", "changed", "tests"], ["l1", "l2"], ["tests"]):
        with pytest.raises(SystemExit):
            check_layers.check(["l1", "l2"], harness)


# AC-REL-04: shared vectors


@pytest.mark.parametrize("case", VECTORS["canonical"])
def test_both_sides_serialize_the_vectors_identically(case):
    expected = case["canonical"].encode()
    assert canonical_bytes(case["input"]) == expected
    assert sign.canonical(case["input"]) == expected
    assert parse_canonical(expected) == case["input"]


def test_the_vector_envelope_is_reproduced_and_verifies():
    vector = VECTORS["envelope"]
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(vector["seed"]))
    made = sign.envelope(vector["payload_type"], vector["payload"].encode(), vector["key_id"], key)
    assert made == vector["envelope"].encode()
    envelope = parse_envelope(made)
    verify_signature(envelope, ATTESTATION_TYPE, base64.b64decode(vector["public_key"]))
    assert envelope.payload == vector["payload"].encode()


def test_a_changed_byte_fails_verification():
    vector = VECTORS["envelope"]
    raw = json.loads(vector["envelope"])
    payload = bytearray(base64.b64decode(raw["payload"]))
    payload[-3] ^= 1
    raw["payload"] = base64.b64encode(bytes(payload)).decode()
    with pytest.raises(EnvelopeError):
        verify_signature(parse_envelope(json.dumps(raw).encode()), ATTESTATION_TYPE,
                         base64.b64decode(vector["public_key"]))


def test_an_attestation_is_not_a_revocation_state_or_the_reverse():
    public = KEY.public_key().public_bytes_raw()
    with pytest.raises(EnvelopeError, match="payload type"):
        verify_signature(parse_envelope(signed({"a": 1})), REVOCATION_TYPE, public)
    with pytest.raises(EnvelopeError, match="payload type"):
        verify_signature(parse_envelope(signed({"a": 1}, REVOCATION_TYPE)), ATTESTATION_TYPE,
                         public)


def test_floats_and_non_canonical_bytes_are_refused():
    with pytest.raises(NotCanonical):
        canonical_bytes({"amount": 1.5})
    with pytest.raises(sign.Refused):
        sign.canonical({"amount": 1.5})
    with pytest.raises(NotCanonical):
        parse_canonical(b'{"b":1, "a":2}')


# Checks over one envelope (no stored rows needed beyond a revocation state)


@pytest.fixture
def owner() -> Member:
    member = Member()
    assert member.activate("finops").status_code == 200
    return member


@pytest.fixture
def tenant(owner: Member) -> UUID:
    return owner.tenant


def owner_engine():
    return create_engine(os.environ["MIGRATION_DATABASE_URL"])


def newest_state() -> bytes | None:
    with owner_engine().begin() as conn:
        row = conn.execute(text("SELECT envelope FROM release_revocation_states "
                                "ORDER BY sequence DESC LIMIT 1")).one_or_none()
    return bytes(row.envelope) if row else None


def publish_state(revoke: list[str] = (), window: timedelta | None = None,
                  issued: datetime | None = None) -> bytes:
    state = sign.revocation_state(newest_state(), list(revoke), KEY_ID, issued or now())
    if window is not None:
        state["expires_at"] = sign.timestamp(datetime.fromisoformat(state["issued_at"]) + window)
    data = signed(state, REVOCATION_TYPE)
    with owner_engine().begin() as conn:
        store.import_revocation(conn, data)
    return data


def import_attestation(data: bytes) -> None:
    with owner_engine().begin() as conn:
        store.import_attestation(conn, data)


def denial(tenant: UUID, data: bytes, scope: ExactScope | None = None, **kwargs) -> str | None:
    with scoped_tx(tenant) as conn:
        try:
            verifier.check_attestation(conn, data, scope or scope_for(tenant), **kwargs)
        except verifier.Denied as denied:
            return denied.check
    return None


db = pytest.mark.db


@db
def test_a_valid_attestation_passes_every_check(tenant):
    publish_state()
    assert denial(tenant, signed(attestation_payload(tenant))) is None


# AC-REL-05


@db
@pytest.mark.parametrize("change", ["none", "two", "wildcard", "no_tenant"])
def test_zero_several_wildcard_or_tenantless_scopes_are_rejected(tenant, change):
    publish_state()
    payload = attestation_payload(tenant)
    scope = payload["certified_scopes"][0]
    payload["certified_scopes"] = {
        "none": [],
        "two": [scope, scope | {"tenant_id": str(uuid4())}],
        "wildcard": [scope | {"domain_pack_version": "*"}],
        "no_tenant": [{k: v for k, v in scope.items() if k != "tenant_id"}],
    }[change]
    assert denial(tenant, signed(payload)) == "CAPABILITY_MEMBERSHIP"


@pytest.mark.parametrize(("version", "tenant_id", "connector"), [
    ("*", str(uuid4()), CONNECTOR), ("0.2.0", "*", CONNECTOR), ("0.2.0", str(uuid4()), "*"),
])
def test_the_signer_refuses_wildcards(version, tenant_id, connector):
    manifest = json.loads(capability_manifest.packaged_bytes())
    with pytest.raises(sign.Refused, match="wildcard"):
        sign.certified_scope(manifest, {"namespace": "finops", "version": version}, tenant_id,
                             connector, CAPABILITY)


# AC-REL-07


@db
def test_an_unknown_key_is_denied(tenant):
    publish_state()
    other = Ed25519PrivateKey.generate()
    data = signed(attestation_payload(tenant, key_id="other"), key=other, key_id="other")
    assert denial(tenant, data) == "KEY_STATUS"


@db
def test_a_wrong_key_under_a_known_id_is_a_bad_signature(tenant):
    publish_state()
    data = signed(attestation_payload(tenant), key=Ed25519PrivateKey.generate())
    assert denial(tenant, data) == "SIGNATURE"


@db
def test_a_key_outside_its_window_is_denied(tenant, monkeypatch):
    publish_state()
    data = signed(attestation_payload(tenant))
    expired = public_key(valid_from=now() - timedelta(days=40), days=30)
    monkeypatch.setattr(keys, "packaged_keys", lambda: {KEY_ID: expired})
    assert denial(tenant, data) == "KEY_STATUS"


@db
@pytest.mark.parametrize(("issued", "lifetime"), [
    (timedelta(hours=-30), timedelta(hours=24)),  # expired
    (timedelta(hours=1), timedelta(hours=10)),  # not yet valid
    (timedelta(hours=-1), timedelta(hours=25)),  # signed window over 24 hours
])
def test_the_validity_window_is_enforced(tenant, issued, lifetime):
    publish_state()
    start = now() + issued
    payload = attestation_payload(tenant, issued_at=sign.timestamp(start),
                                  expires_at=sign.timestamp(start + lifetime))
    assert denial(tenant, signed(payload)) == "VALIDITY"


def test_a_packaged_key_may_not_live_over_90_days():
    item = {"key_id": "k", "public_key": base64.b64encode(b"x" * 32).decode(),
            "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-04-02T00:00:00+00:00"}
    with pytest.raises(keys.KeyFileError, match="90 days"):
        keys.parse_keys(json.dumps({"keys": [item]}))


def test_the_packaged_key_file_parses():
    assert isinstance(keys.parse_keys(keys.KEYS_FILE.read_text("utf-8")), dict)


# AC-REL-08


@db
def test_a_listed_attestation_is_denied(tenant):
    payload = attestation_payload(tenant)
    publish_state(revoke=[payload["attestation_id"]])
    assert denial(tenant, signed(payload)) == "REVOCATION"


@db
def test_an_expired_revocation_state_is_denied(tenant):
    publish_state(issued=now() - timedelta(hours=30), window=timedelta(hours=24))
    assert denial(tenant, signed(attestation_payload(tenant))) == "REVOCATION"


@db
def test_a_state_window_over_24_hours_is_denied(tenant):
    publish_state(window=timedelta(hours=25))
    assert denial(tenant, signed(attestation_payload(tenant))) == "REVOCATION"


@db
def test_a_state_below_the_high_water_mark_is_denied(tenant):
    publish_state()
    data = signed(attestation_payload(tenant))
    with owner_engine().connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
        conn.execute(text("UPDATE release_revocation_high_water SET sequence = sequence + 5"))
        try:
            with pytest.raises(verifier.Denied) as denied:
                verifier.check_attestation(conn, data, scope_for(tenant))
            assert denied.value.check == "REVOCATION"
        finally:
            transaction.rollback()


def test_no_revocation_state_is_denied():
    class Empty:
        def execute(self, *args, **kwargs):
            class Result:
                def one_or_none(self):
                    return None

                def scalar_one_or_none(self):
                    return None

            return Result()

    with pytest.raises(verifier.Denied, match="REVOCATION"):
        verifier._revocation_state(Empty(), now())


@db
@pytest.mark.parametrize("statement", [
    "DELETE FROM release_revocation_states",
    "UPDATE release_revocation_states SET imported_at = now()",
    "DELETE FROM release_revocation_high_water",
    "UPDATE release_revocation_high_water SET sequence = 0",
])
def test_removing_or_rewinding_revocation_is_impossible_even_for_the_owner(statement):
    publish_state()
    with pytest.raises(DBAPIError), owner_engine().begin() as conn:
        conn.execute(text(statement))


@db
def test_the_runtime_role_can_only_read_release_tables(tenant):
    for statement in ("INSERT INTO release_revocation_high_water VALUES (false, 1)",
                      "UPDATE release_revocation_high_water SET sequence = sequence + 1",
                      "DELETE FROM release_gate_attestations"):
        with pytest.raises(DBAPIError, match="permission denied"), scoped_tx(tenant) as conn:
            conn.execute(text(statement))


@db
def test_an_unchained_state_cannot_be_imported():
    publish_state()
    state = sign.revocation_state(None, [], KEY_ID, now())
    with pytest.raises(store.ImportRefused), owner_engine().begin() as conn:
        store.import_revocation(conn, signed(state, REVOCATION_TYPE))


# AC-REL-09


@db
def test_a_different_running_digest_is_denied(tenant):
    publish_state()
    data = signed(attestation_payload(tenant))
    assert denial(tenant, data, digest=lambda: "sha256:" + "c" * 64) == "IMAGE_DIGEST"
    assert denial(tenant, data, digest=lambda: None) == "IMAGE_DIGEST"


def test_the_digest_comes_only_from_the_runtime_identity_service(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("IMAGE_DIGEST", DIGEST)
    monkeypatch.setenv("RUNNING_DIGEST", DIGEST)
    seen = []

    def get(url, timeout):
        seen.append(url)
        return httpx.Response(200, json={"digest": "sha256:" + "d" * 64})

    monkeypatch.setattr(httpx, "get", get)
    assert runtime_identity.running_digest() == "sha256:" + "d" * 64
    assert seen == ["http://runtime-identity:8010/identity"]

    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(404, json={}))
    assert runtime_identity.running_digest() is None

    def unreachable(url, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", unreachable)
    assert runtime_identity.running_digest() is None


@db
def test_a_header_cannot_supply_the_digest(owner, tenant, monkeypatch):
    monkeypatch.setattr(runtime_identity, "running_digest", lambda: None)
    publish_state()
    import_attestation(signed(attestation_payload(tenant)))
    response = owner.request("GET", f"/api/tenants/{tenant}/execution-readiness",
                             headers={"X-Image-Digest": DIGEST, "X-Running-Digest": DIGEST})
    refund = _row(response.json(), CAPABILITY)
    assert refund["release_gate"] == {"passes": False, "failing_check": "IMAGE_DIGEST",
                                      "attestation_id": None}


# AC-REL-10 and checks 9-10


@db
@pytest.mark.parametrize(("change", "check"), [
    ({"capability_manifest_sha256": "0" * 64}, "MANIFEST_HASH"),
    ({"scope": {"capability_version": "9.9.9"}}, "CAPABILITY_VERSION"),
    ({"scope": {"contract_version": "sha256:" + "0" * 64}}, "CAPABILITY_VERSION"),
    ({"scope": {"implementation_sha256": "0" * 64}}, "CAPABILITY_VERSION"),
    ({"scope": {"connector_version": "0.0.1"}}, "CAPABILITY_VERSION"),
    ({"suite": {"suite_id": "another-suite"}}, "TEST_EVIDENCE"),
    ({"suite": {"result": "FAIL"}}, "TEST_EVIDENCE"),
    ({"suite": {"skipped": 1}}, "TEST_EVIDENCE"),
    ({"suite": {"version": 2}}, "TEST_EVIDENCE"),
    ({"scope": {"domain_pack_version": "0.1.0"}}, "EXACT_SCOPE"),
    ({"scope": {"tenant_id": "00000000-0000-4000-8000-000000000000"}}, "EXACT_SCOPE"),
])
def test_each_mismatch_names_its_check(tenant, change, check):
    publish_state()
    payload = attestation_payload(tenant)
    payload["certified_scopes"][0] |= change.get("scope", {})
    payload["test_suite"] |= change.get("suite", {})
    payload |= {k: v for k, v in change.items() if k not in {"scope", "suite"}}
    assert denial(tenant, signed(payload)) == check


@db
def test_a_capability_outside_the_certified_scope_is_denied(tenant):
    publish_state()
    data = signed(attestation_payload(tenant))
    other = scope_for(tenant, connector_id="internal-merchant",
                      action_capability="merchant.payment_status.correct")
    assert denial(tenant, data, other) == "CAPABILITY_MEMBERSHIP"


@db
def test_neither_capabilitys_attestation_authorizes_the_other_or_another_tenant(tenant):
    """AC-REL-12, both directions, through the stored-row path the LIVE switch uses."""
    publish_state()
    refund = attestation_payload(tenant)
    merchant = attestation_payload(tenant)
    merchant["certified_scopes"][0] |= {"connector_id": "internal-merchant",
                                        "capability": "merchant.payment_status.correct"}
    merchant_scope = scope_for(tenant, connector_id="internal-merchant",
                               action_capability="merchant.payment_status.correct")
    assert denial(tenant, signed(refund), merchant_scope) == "CAPABILITY_MEMBERSHIP"
    assert denial(tenant, signed(merchant)) == "CAPABILITY_MEMBERSHIP"
    import_attestation(signed(refund))
    with scoped_tx(tenant) as conn:
        assert verifier.verify(conn, scope_for(tenant)).allowed is True
        assert verifier.verify(conn, merchant_scope).allowed is False
    other = Member()
    other.activate("finops")
    with scoped_tx(other.tenant) as conn:
        assert verifier.verify(conn, scope_for(other.tenant)).allowed is False


@db
def test_an_empty_packaged_category_denies(tenant, monkeypatch):
    publish_state()
    data = signed(attestation_payload(tenant))
    suite = filled_suite().model_copy(
        update={"categories": filled_suite().categories | {"failure_recovery": []}})
    monkeypatch.setattr(payloads, "packaged_suites", lambda: {(CONNECTOR, CAPABILITY): suite})
    assert denial(tenant, data) == "TEST_EVIDENCE"


@db
def test_an_attestation_for_an_older_suite_version_denies(tenant, monkeypatch):
    publish_state()
    data = signed(attestation_payload(tenant))
    suite = filled_suite().model_copy(update={"version": 2})
    monkeypatch.setattr(payloads, "packaged_suites", lambda: {(CONNECTOR, CAPABILITY): suite})
    assert denial(tenant, data) == "TEST_EVIDENCE"


# AC-AUTHZ-16: the LIVE switch and readiness use the same verifier


def _row(rows: list[dict], capability: str) -> dict:
    [row] = [r for r in rows if r["action_capability"] == capability]
    return row


@db
def test_live_needs_and_records_a_valid_attestation(owner, tenant):
    body = {"domain_pack_id": "finops", "connector_id": CONNECTOR,
            "action_capability": CAPABILITY, "mode": "LIVE"}
    path = f"/api/tenants/{tenant}/execution-mode"
    publish_state()
    denied = owner.request("PUT", path, json=body)
    assert denied.status_code == 403 and denied.json()["error"]["check"] == "SIGNATURE"

    payload = attestation_payload(tenant)
    import_attestation(signed(payload))
    readiness = owner.request("GET", f"/api/tenants/{tenant}/execution-readiness").json()
    assert _row(readiness, CAPABILITY)["release_gate"] == {
        "passes": True, "failing_check": None, "attestation_id": payload["attestation_id"]}
    assert _row(readiness, "merchant.payment_status.correct")["release_gate"]["passes"] is False

    live = owner.request("PUT", path, json=body)
    assert live.status_code == 200, live.text
    assert live.json()["attestation_id"] == payload["attestation_id"]

    publish_state(revoke=[payload["attestation_id"]])
    readiness = owner.request("GET", f"/api/tenants/{tenant}/execution-readiness").json()
    assert _row(readiness, CAPABILITY)["release_gate"]["failing_check"] == "REVOCATION"
    # Back to SHADOW never needs an attestation, even after revocation.
    shadow = owner.request("PUT", path, json=body | {"mode": "SHADOW"})
    assert shadow.status_code == 200


@db
def test_an_attestation_for_another_tenant_is_invisible(tenant):
    publish_state()
    other = Member()
    other.activate("finops")
    import_attestation(signed(attestation_payload(other.tenant)))
    with scoped_tx(tenant) as conn:
        assert verifier.verify(conn, scope_for(tenant)).allowed is False
    with scoped_tx(other.tenant) as conn:
        assert verifier.verify(conn, scope_for(other.tenant)).allowed is True


@db
def test_only_the_release_attestor_principal_exists():
    with owner_engine().begin() as conn:
        rows = conn.execute(text("SELECT kind, permissions FROM platform_service_principals")).all()
    assert [(r.kind, list(r.permissions)) for r in rows] == [
        ("release_attestor", ["platform.release.attest"])]
