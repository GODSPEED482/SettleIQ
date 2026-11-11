"""The version-pinned inputs of one domain run (spec 6.3: every run pins its versions).

A run reads only what is pinned here: the latest published ontology, that version's mappings
and authority assertions, the pack's identity policies and engine, and the source records
ingested up to the cut. The same pins give the same inputs hash.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain_packs.manifest import DomainPackManifest
from app.domain_packs.registry import resolve_entrypoint
from app.errors import conflict
from app.semantics.hashing import content_hash
from app.semantics.mappings import current_authority, current_mappings


@dataclass(frozen=True)
class Record:
    id: UUID
    source_id: UUID
    object_name: str
    attributes: dict[str, Any]
    raw_evidence_id: UUID
    payload_hash: str
    occurred_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class Policy:
    """Exact-ID identities, derived classes and relationship rules, merged across policies."""

    versions: dict[str, str]
    identities: dict[str, list[str]]
    derived: dict[str, list[str]]
    relationships: list[dict[str, str]]


@dataclass(frozen=True)
class RunInputs:
    pack: DomainPackManifest
    ontology_id: UUID
    ontology_version: int
    ontology_content_hash: str
    mappings: list[dict[str, Any]]
    authority: list[dict[str, Any]]
    policy: Policy
    records: list[Record]
    as_of: datetime

    @property
    def rule_version(self) -> str:
        engine = self.pack.domain_engine.id if self.pack.domain_engine else "none"
        return f"{self.pack.namespace}@{self.pack.version}/{engine}"

    def mappings_for(self, record: Record) -> list[dict[str, Any]]:
        """File objects are named `{name}@{sha16}`, so a mapping names the file by its stem."""
        return [
            m for m in self.mappings
            if m["source_id"] == str(record.source_id)
            and record.object_name.split("@", 1)[0] == m["external_object"]
        ]

    def source_snapshots(self) -> dict[str, dict[str, Any]]:
        by_source: dict[str, list[list[str]]] = defaultdict(list)
        for record in self.records:
            by_source[str(record.source_id)].append([str(record.id), record.payload_hash])
        return {
            source: {"records": len(rows), "hash": content_hash(sorted(rows))}
            for source, rows in sorted(by_source.items())
        }

    def pins(self) -> dict[str, Any]:
        return {
            "pack_namespace": self.pack.namespace,
            "pack_version": self.pack.version,
            "ontology_id": str(self.ontology_id),
            "ontology_version": self.ontology_version,
            "ontology_content_hash": self.ontology_content_hash,
            "mapping_versions": {m["id"]: m["version"] for m in self.mappings},
            "authority_versions": {a["id"]: a["version"] for a in self.authority},
            "policy_versions": self.policy.versions,
            "rule_version": self.rule_version,
            "source_snapshots": self.source_snapshots(),
        }


def load_policy(pack: DomainPackManifest) -> Policy:
    versions: dict[str, str] = {}
    identities: dict[str, list[str]] = {}
    derived: dict[str, list[str]] = {}
    relationships: list[dict[str, str]] = []
    for item in pack.entity_resolution_policies:
        policy = resolve_entrypoint(item.entrypoint)()
        versions[policy["id"]] = str(policy["version"])
        identities |= {cls: list(props) for cls, props in policy["identities"].items()}
        derived |= {cls: list(more) for cls, more in policy.get("derived", {}).items()}
        relationships += [dict(rule) for rule in policy.get("relationships", [])]
    return Policy(versions, identities, derived, relationships)


def _published(conn: Connection, namespace: str) -> Any:
    row = conn.execute(
        text(
            "SELECT id, version, content_hash, draft_id, composition FROM ontologies "
            "ORDER BY version DESC LIMIT 1"
        )
    ).first()
    if row is None:
        raise conflict("NO_PUBLISHED_ONTOLOGY", "Publish an ontology before running the domain.")
    packs = {ref["namespace"] for ref in row.composition if ref.get("layer") == "PACK"}
    if namespace not in packs:
        raise conflict(
            "PACK_NOT_PUBLISHED", f"The published ontology does not include {namespace}."
        )
    return row


def _records(conn: Connection, cut: datetime) -> list[Record]:
    rows = conn.execute(
        text(
            """
            SELECT r.id, r.source_id, o.object_name, r.attributes, r.raw_evidence_id,
                r.payload_hash, r.occurred_at, r.observed_at
            FROM source_records r
            JOIN source_objects o ON o.tenant_id = r.tenant_id AND o.id = r.source_object_id
            WHERE r.ingested_at <= :cut
            ORDER BY r.ingested_at, r.id
            """
        ),
        {"cut": cut},
    ).all()
    return [Record(**row._mapping) for row in rows]


def load_inputs(conn: Connection, pack: DomainPackManifest, as_of: datetime) -> RunInputs:
    ontology = _published(conn, pack.namespace)
    policy = load_policy(pack)
    classes = set(policy.identities)
    mappings = [m for m in current_mappings(conn, ontology.draft_id) if m["class_iri"] in classes]
    return RunInputs(
        pack=pack,
        ontology_id=ontology.id,
        ontology_version=ontology.version,
        ontology_content_hash=ontology.content_hash,
        mappings=mappings,
        authority=current_authority(conn, ontology.draft_id),
        policy=policy,
        records=_records(conn, as_of),
        as_of=as_of,
    )
