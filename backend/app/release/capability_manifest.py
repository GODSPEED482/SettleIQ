"""The capability manifest (spec 9.7.1, AC-REL-01).

Generated deterministically from the registered implementations, the connector manifests and
the domain packs, serialized as RFC 8785 bytes and packaged in the image. Each state-changing
capability a pack uses is listed with its readback dependencies, and each entry names its
implementation by entrypoint and the SHA-256 of its packaged source, so a code change changes
the manifest hash and voids every attestation over it.

Run `python -m app.release.capability_manifest --write` after a deliberate change; the image
build does the same, and startup refuses a packaged manifest that differs from the generated
one.
"""

import hashlib
import importlib
import json
import sys
from functools import cache
from pathlib import Path
from typing import Any

from app.connectors.implementations import IMPLEMENTATIONS, PENDING_IMPLEMENTATIONS
from app.connectors.manifest import load_capability_registry, load_connector_manifests
from app.domain_packs.manifest import load_domain_packs
from app.release.canonical import canonical_bytes

MANIFEST_FILE = Path(__file__).with_name("capability-manifest.json")
SCHEMA = "settleiq.capability-manifest/1"


class ManifestError(RuntimeError):
    pass


def _resolve(entrypoint: str) -> Any:
    module_name, attribute = entrypoint.split(":", 1)
    target: Any = importlib.import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise ManifestError(f"{entrypoint} is not callable")
    return target


def source_sha256(entrypoint: str) -> str:
    """The SHA-256 of the module that implements `entrypoint`, with line endings normalized so
    a Windows checkout and the Linux image agree."""
    _resolve(entrypoint)
    module = importlib.import_module(entrypoint.split(":", 1)[0])
    source = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(source).hexdigest()


def contract_version(parameters: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(parameters)).hexdigest()


def generate() -> dict[str, Any]:
    registry = load_capability_registry()
    connectors = load_connector_manifests()
    packs = load_domain_packs()
    implemented = {(i.connector_id, i.capability): i for i in IMPLEMENTATIONS}

    for connector in connectors.values():
        for declared in connector.capabilities:
            key = (connector.connector_id, declared.key)
            if registry[declared.key].kind == "action" and key not in implemented and (
                key not in PENDING_IMPLEMENTATIONS
            ):
                raise ManifestError(
                    f"{connector.connector_id} declares {declared.key} with no implementation"
                )

    dependencies: dict[str, set[str]] = {}
    users: dict[str, set[str]] = {}
    for pack in packs.values():
        for template in pack.playbook_templates:
            for action in template.connector_action_capabilities:
                dependencies.setdefault(action, set()).update(template.capability_dependencies)
                users.setdefault(action, set()).add(pack.namespace)
                for read in template.capability_dependencies:
                    users.setdefault(read, set()).add(pack.namespace)

    entries = []
    for (connector_id, capability), implementation in sorted(implemented.items()):
        connector = connectors.get(connector_id)
        declared = {c.key: c for c in connector.capabilities} if connector else {}
        if capability not in declared:
            raise ManifestError(f"{connector_id} does not declare {capability}")
        needs = sorted(dependencies.get(capability, set()))
        missing = [d for d in needs if (connector_id, d) not in implemented]
        if missing:
            raise ManifestError(f"{capability} depends on unimplemented {missing}")
        entries.append({
            "capability": capability,
            "version": declared[capability].version,
            "contract_version": contract_version(declared[capability].parameters),
            "kind": registry[capability].kind,
            "connector_id": connector_id,
            "connector_version": connector.version,
            "domain_packs": sorted(users.get(capability, set())),
            "dependencies": [{"capability": d, "version": declared[d].version} for d in needs],
            "implementation": {
                "entrypoint": implementation.entrypoint,
                "source_sha256": source_sha256(implementation.entrypoint),
            },
        })
    return {"schema": SCHEMA, "capabilities": entries}


def generated_bytes() -> bytes:
    return canonical_bytes(generate())


@cache
def packaged_bytes() -> bytes:
    return MANIFEST_FILE.read_bytes()


def packaged_sha256() -> str:
    return hashlib.sha256(packaged_bytes()).hexdigest()


def packaged_entry(connector_id: str, capability: str) -> dict[str, Any] | None:
    for entry in json.loads(packaged_bytes())["capabilities"]:
        if (entry["connector_id"], entry["capability"]) == (connector_id, capability):
            return entry
    return None


def check_packaged() -> None:
    """Startup check: the packaged manifest is exactly what the registered code generates."""
    if not MANIFEST_FILE.is_file() or packaged_bytes() != generated_bytes():
        raise ManifestError(
            "capability-manifest.json differs from the registered implementations; run "
            "`python -m app.release.capability_manifest --write` after a deliberate change"
        )


if __name__ == "__main__":
    data = generated_bytes()
    if sys.argv[1:] == ["--write"]:
        MANIFEST_FILE.write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
