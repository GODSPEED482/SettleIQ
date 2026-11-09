"""Loads the platform core and pack ontologies as separate, hashed artifacts (spec 5.1)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.domain_packs.manifest import PACKS_DIR, PLATFORM_VERSION, DomainPackManifest
from app.semantics.hashing import content_hash

CORE_FILE = Path(__file__).with_name("core.jsonld")
CORE_NAMESPACE = "core"
BASE_PREFIXES = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


IRI_VALUED_TERMS = ("rdfs:subClassOf", "rdfs:domain", "rdfs:range", "owl:equivalentClass")


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    layer: str
    namespace: str
    version: str
    content: dict[str, Any]
    content_hash: str

    def reference(self) -> dict[str, str]:
        return {
            "layer": self.layer,
            "namespace": self.namespace,
            "version": self.version,
            "content_hash": self.content_hash,
        }


def core_iri() -> str:
    return "urn:settleiq:core#"


def pack_iri(namespace: str) -> str:
    return f"urn:settleiq:pack:{namespace}#"


def tenant_iri(tenant_id: UUID) -> str:
    return f"urn:settleiq:tenant:{tenant_id}#"


def _check_namespace(content: dict[str, Any], prefix: str, iri: str, source: str) -> None:
    if content.get("@context", {}).get(prefix) != iri:
        raise ArtifactError(f"{source}: prefix {prefix} must map to {iri}")
    for node in content.get("@graph", []):
        if not str(node.get("@id", "")).startswith(f"{prefix}:"):
            raise ArtifactError(f"{source}: {node.get('@id')} is outside namespace {prefix}")


def core_artifact() -> Artifact:
    content = json.loads(CORE_FILE.read_text(encoding="utf-8"))
    _check_namespace(content, CORE_NAMESPACE, core_iri(), CORE_NAMESPACE)
    return Artifact("CORE", CORE_NAMESPACE, PLATFORM_VERSION, content, content_hash(content))


def pack_artifacts(pack: DomainPackManifest) -> list[Artifact]:
    artifacts = []
    for definition in pack.ontology_definitions:
        path = PACKS_DIR / pack.package / definition.path
        content = json.loads(path.read_text(encoding="utf-8"))
        _check_namespace(content, pack.namespace, pack_iri(pack.namespace), definition.id)
        artifacts.append(
            Artifact("PACK", pack.namespace, pack.version, content, content_hash(content))
        )
    return artifacts


def extension_context(tenant_id: UUID, pack_namespaces: list[str]) -> dict[str, Any]:
    """The fixed prefixes a tenant extension may use. Tenants cannot redefine them."""
    context: dict[str, Any] = dict(BASE_PREFIXES) | {CORE_NAMESPACE: core_iri()}
    context |= {namespace: pack_iri(namespace) for namespace in sorted(pack_namespaces)}
    context |= {term: {"@type": "@id"} for term in IRI_VALUED_TERMS}
    return context | {"tenant": tenant_iri(tenant_id)}
