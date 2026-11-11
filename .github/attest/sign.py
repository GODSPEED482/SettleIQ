"""Issue a POL-14 release attestation and a new signed revocation state (spec 9.7.2-9.7.5).

Runs only in the signing job of .github/workflows/attest.yml, inside the protected
`release-attestation` environment that holds the key. It imports nothing from the application
and runs no code from the image: it reads the files the suite job copied out of the image (the
capability manifest, the packaged suites and the pack manifest) and the suite's JUnit report.

It refuses to issue when a required category is empty, a listed test is missing, or any test
failed, errored or was skipped (AC-REL-02). The attestation certifies exactly one scope and is
valid for at most 24 hours. Every run, issue or revoke, publishes a new revocation state that
chains to the previous one.

The key is read from ATTESTATION_SIGNING_KEY (base64 Ed25519 seed) and ATTESTATION_KEY_ID.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import rfc8785
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ATTESTATION_TYPE = "application/vnd.settleiq.release-attestation.v1+json"
REVOCATION_TYPE = "application/vnd.settleiq.revocation-state.v1+json"
VALIDITY = timedelta(hours=24)
CATEGORIES = (
    "authorization_enforcement", "tenant_isolation", "transitions", "idempotency",
    "concurrency_and_stale_state", "failure_recovery", "shadow_side_effect_prevention",
    "post_action_verification",
)


class Refused(Exception):
    """Issuance is refused; the message says why."""


def canonical(value: Any) -> bytes:
    def check(item: Any) -> None:
        if isinstance(item, float):
            raise Refused("floating-point values cannot be signed")
        if isinstance(item, dict):
            for nested in item.values():
                check(nested)
        elif isinstance(item, list):
            for nested in item:
                check(nested)

    check(value)
    return rfc8785.dumps(value)


def pae(payload_type: str, payload: bytes) -> bytes:
    kind = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(payload), payload)


def envelope(payload_type: str, payload: bytes, key_id: str, key: Ed25519PrivateKey) -> bytes:
    signature = key.sign(pae(payload_type, payload))
    return json.dumps({
        "payloadType": payload_type,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{"keyid": key_id, "sig": base64.b64encode(signature).decode()}],
    }, sort_keys=True, separators=(",", ":")).encode()


def timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _node_id(case: ET.Element) -> str:
    return case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")


def check_evidence(junit: bytes, suite: dict[str, Any]) -> dict[str, Any]:
    """The suite's evidence, or Refused. Every category is non-empty, every listed test ran,
    and nothing in the report failed, errored or was skipped."""
    categories = suite["categories"]
    if tuple(categories) != CATEGORIES:
        raise Refused("the suite does not declare exactly the eight required categories")
    empty = [name for name, tests in categories.items() if not tests]
    if empty:
        raise Refused(f"required categories are empty: {', '.join(empty)}")
    cases = list(ET.fromstring(junit).iter("testcase"))
    outcomes: dict[str, str] = {}
    for case in cases:
        if case.find("skipped") is not None:
            outcome = "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        else:
            outcome = "passed"
        outcomes[_node_id(case)] = outcome
    bad = sorted(node for node, outcome in outcomes.items() if outcome != "passed")
    if bad:
        raise Refused(f"tests did not pass: {', '.join(bad)}")
    for name, tests in categories.items():
        for test in tests:
            if not [n for n in outcomes if n == test or n.startswith(test + "[")]:
                raise Refused(f"{name}: {test} did not run")
    return {"suite_id": suite["suite_id"], "version": suite["version"], "result": "PASS",
            "evidence_sha256": hashlib.sha256(junit).hexdigest(), "skipped": 0}


def certified_scope(manifest: dict[str, Any], pack: dict[str, Any], tenant_id: str,
                    connector_id: str, capability: str) -> dict[str, Any]:
    entries = [e for e in manifest["capabilities"]
               if (e["connector_id"], e["capability"]) == (connector_id, capability)]
    if "*" in tenant_id + connector_id + capability + pack["namespace"] + pack["version"]:
        raise Refused("wildcards are not allowed")
    if len(entries) != 1 or entries[0]["kind"] != "action":
        raise Refused(f"{connector_id} {capability} is not an action in the capability manifest")
    entry = entries[0]
    if pack["namespace"] not in entry["domain_packs"]:
        raise Refused(f"{pack['namespace']} does not use {capability}")
    return {
        "tenant_id": str(uuid.UUID(tenant_id)),
        "domain_pack_id": pack["namespace"],
        "domain_pack_version": pack["version"],
        "connector_id": connector_id,
        "connector_version": entry["connector_version"],
        "capability": capability,
        "capability_version": entry["version"],
        "contract_version": entry["contract_version"],
        "implementation_sha256": entry["implementation"]["source_sha256"],
        "dependencies": entry["dependencies"],
    }


def revocation_state(previous: bytes | None, revoke: list[str], key_id: str,
                     now: datetime) -> dict[str, Any]:
    if previous is None:
        sequence, revoked, previous_sha = 1, set(), ""
    else:
        state = json.loads(base64.b64decode(json.loads(previous)["payload"]))
        sequence = state["sequence"] + 1
        revoked = set(state["revoked"])
        previous_sha = hashlib.sha256(previous).hexdigest()
    revoked.update(str(uuid.UUID(item)) for item in revoke)
    return {"schema": "settleiq.revocation-state/1", "sequence": sequence,
            "previous_sha256": previous_sha, "issued_at": timestamp(now),
            "expires_at": timestamp(now + VALIDITY), "revoked": sorted(revoked),
            "key_id": key_id}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--action", choices=["issue", "revoke"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--previous-state", type=Path)
    parser.add_argument("--revoke", default="")
    for name in ("manifest", "suites", "pack-manifest", "junit"):
        parser.add_argument(f"--{name}", type=Path)
    for name in ("tenant-id", "connector-id", "capability", "subject-image", "subject-digest",
                 "harness-digest", "commit", "run-id", "workflow"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args(argv)

    key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(os.environ["ATTESTATION_SIGNING_KEY"]))
    key_id = os.environ["ATTESTATION_KEY_ID"]
    now = datetime.now(UTC).replace(microsecond=0)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.action == "issue":
        manifest_bytes = args.manifest.read_bytes()
        suites = yaml.safe_load(args.suites.read_text(encoding="utf-8"))["suites"]
        matching = [s for s in suites if (s["connector_id"], s["capability"]) == (
            args.connector_id, args.capability)]
        if len(matching) != 1:
            raise Refused("no packaged suite for this capability")
        evidence = check_evidence(args.junit.read_bytes(), matching[0])
        payload = {
            "schema": "settleiq.release-attestation/1",
            "attestation_id": str(uuid.uuid4()),
            "subject": {"image": args.subject_image, "digest": args.subject_digest},
            "build": {"commit": args.commit, "run_id": args.run_id, "workflow": args.workflow},
            "capability_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "certified_scopes": [certified_scope(
                json.loads(manifest_bytes),
                yaml.safe_load(args.pack_manifest.read_text(encoding="utf-8")),
                args.tenant_id, args.connector_id, args.capability)],
            "test_suite": {**evidence, "harness_digest": args.harness_digest},
            "issued_at": timestamp(now),
            "expires_at": timestamp(now + VALIDITY),
            "key_id": key_id,
        }
        signed = envelope(ATTESTATION_TYPE, canonical(payload), key_id, key)
        (args.out / "attestation.json").write_bytes(signed)

    previous = args.previous_state.read_bytes() if args.previous_state else None
    revoke = [item for item in args.revoke.split(",") if item.strip()]
    state = revocation_state(previous, revoke, key_id, now)
    (args.out / "revocation.json").write_bytes(
        envelope(REVOCATION_TYPE, canonical(state), key_id, key))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Refused as refused:
        print(f"attestation refused: {refused}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
