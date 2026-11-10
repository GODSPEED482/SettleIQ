"""Tenant pack activation and ontology composition (spec 5.1, EXT-REQ-04, EXT-REQ-11).

A composition references the platform core and each active pack by content hash, and keeps
tenant extensions separate. Published versions are immutable; drafts are the only editable form.
"""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain_packs.manifest import DomainPackManifest, load_domain_packs
from app.domain_packs.registry import PackActivationError, assert_can_activate
from app.errors import conflict, not_found, validation_failed
from app.semantics import mappings
from app.semantics.artifacts import Artifact, core_artifact, extension_context, pack_artifacts

_ACTIVE_NAMESPACES_SQL = text(
    "SELECT namespace FROM tenant_domain_packs WHERE status = 'ACTIVE' ORDER BY namespace"
)


def active_namespaces(conn: Connection) -> list[str]:
    return list(conn.execute(_ACTIVE_NAMESPACES_SQL).scalars())


def lock_tenant(conn: Connection, tenant_id: UUID) -> None:
    """Serialize activation and draft creation, so concurrent changes cannot both pass checks."""
    conn.execute(text("SELECT id FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id})


def _store_artifact(conn: Connection, tenant_id: UUID, artifact: Artifact) -> None:
    conn.execute(
        text(
            """
            INSERT INTO ontology_artifacts
                (id, tenant_id, layer, namespace, version, content, content_hash)
            VALUES (:id, :tenant, :layer, :namespace, :version, CAST(:content AS jsonb), :hash)
            ON CONFLICT (tenant_id, layer, namespace, content_hash) DO NOTHING
            """
        ),
        {
            "id": uuid4(),
            "tenant": tenant_id,
            "layer": artifact.layer,
            "namespace": artifact.namespace,
            "version": artifact.version,
            "content": json.dumps(artifact.content),
            "hash": artifact.content_hash,
        },
    )


def list_packs(ctx: TenantContext) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        statuses = {
            row.namespace: row.status
            for row in conn.execute(text("SELECT namespace, status FROM tenant_domain_packs"))
        }
    return [
        {
            "namespace": pack.namespace,
            "version": pack.version,
            "status": statuses.get(pack.namespace, "AVAILABLE"),
            "case_types": [case.code for case in pack.case_types],
        }
        for pack in load_domain_packs().values()
    ]


def activate_pack(ctx: TenantContext, namespace: str) -> dict[str, str]:
    packs = load_domain_packs()
    candidate = packs.get(namespace)
    if candidate is None:
        raise not_found()
    with tenant_tx(ctx) as conn:
        lock_tenant(conn, ctx.tenant_id)
        active = [packs[name] for name in active_namespaces(conn) if name in packs]
        try:
            assert_can_activate(candidate, active)
        except PackActivationError as error:
            raise conflict("PACK_NOT_ACTIVATABLE", str(error)) from None
        conn.execute(
            text(
                """
                INSERT INTO tenant_domain_packs
                    (tenant_id, namespace, version, status, changed_by)
                VALUES (:tenant, :namespace, :version, 'ACTIVE', :user)
                ON CONFLICT (tenant_id, namespace) DO UPDATE SET
                    version = EXCLUDED.version, status = 'ACTIVE',
                    changed_by = EXCLUDED.changed_by, changed_at = now()
                """
            ),
            {
                "tenant": ctx.tenant_id,
                "namespace": candidate.namespace,
                "version": candidate.version,
                "user": ctx.principal.id,
            },
        )
        for artifact in [core_artifact(), *pack_artifacts(candidate)]:
            _store_artifact(conn, ctx.tenant_id, artifact)
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "domain_pack.activated", "domain_pack",
            {"namespace": candidate.namespace, "version": candidate.version},
        )
    return {"namespace": candidate.namespace, "version": candidate.version, "status": "ACTIVE"}


def _dependents(namespace: str, active: list[DomainPackManifest]) -> list[str]:
    return [
        pack.namespace
        for pack in active
        if any(dependency.namespace == namespace for dependency in pack.dependencies)
    ]


def disable_pack(ctx: TenantContext, namespace: str) -> dict[str, str]:
    packs = load_domain_packs()
    with tenant_tx(ctx) as conn:
        lock_tenant(conn, ctx.tenant_id)
        names = active_namespaces(conn)
        if namespace not in names:
            raise not_found()
        dependents = _dependents(namespace, [packs[name] for name in names if name in packs])
        if dependents:
            raise conflict("PACK_NOT_DISABLABLE", f"{namespace} is required by {dependents}")
        conn.execute(
            text(
                """
                UPDATE tenant_domain_packs
                SET status = 'DISABLED', changed_by = :user, changed_at = now()
                WHERE namespace = :namespace
                """
            ),
            {"namespace": namespace, "user": ctx.principal.id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "domain_pack.disabled", "domain_pack",
            {"namespace": namespace},
        )
    return {"namespace": namespace, "status": "DISABLED"}


def _artifact_contents(conn: Connection, references: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("SELECT layer, namespace, content_hash, content FROM ontology_artifacts")
    ).all()
    contents = {(r.layer, r.namespace, r.content_hash): r.content for r in rows}
    return [
        ref | {"content": contents[(ref["layer"], ref["namespace"], ref["content_hash"])]}
        for ref in references
    ]


def latest_version(conn: Connection) -> int:
    return conn.execute(text("SELECT COALESCE(max(version), 0) FROM ontologies")).scalar_one()


def read_active_ontology(ctx: TenantContext) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT version, composition, tenant_extension, content_hash, published_at
                FROM ontologies ORDER BY version DESC LIMIT 1
                """
            )
        ).first()
        if row is None:
            return {"version": None, "artifacts": [], "tenant_extension": None}
        return {
            "version": row.version,
            "content_hash": row.content_hash,
            "published_at": row.published_at.isoformat(),
            "artifacts": _artifact_contents(conn, row.composition),
            "tenant_extension": row.tenant_extension,
        }


def _composition(conn: Connection, packs: dict[str, DomainPackManifest]) -> list[Artifact]:
    artifacts = [core_artifact()]
    for namespace in active_namespaces(conn):
        if namespace in packs:
            artifacts.extend(pack_artifacts(packs[namespace]))
    return artifacts


def _extension(ctx: TenantContext, conn: Connection, graph: list[dict]) -> dict[str, Any]:
    return {"@context": extension_context(ctx.tenant_id, active_namespaces(conn)), "@graph": graph}


def _draft_view(conn: Connection, row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "revision": row.revision,
        "base_version": row.base_version,
        "status": row.status,
        "artifacts": _artifact_contents(conn, row.composition),
        "tenant_extension": row.tenant_extension,
        "proposals": row.proposals,
    }


_DRAFT_COLUMNS = "id, revision, base_version, status, composition, tenant_extension, proposals"


def create_draft(ctx: TenantContext) -> dict[str, Any]:
    packs = load_domain_packs()
    with tenant_tx(ctx) as conn:
        lock_tenant(conn, ctx.tenant_id)
        existing = conn.execute(
            text("SELECT id FROM ontology_drafts WHERE status = 'OPEN'")
        ).first()
        if existing is not None:
            raise conflict("DRAFT_EXISTS", "This tenant already has an open draft.")
        # Stored again here because a pack's content can change after it was activated.
        composition = _composition(conn, packs)
        for artifact in composition:
            _store_artifact(conn, ctx.tenant_id, artifact)
        # A draft starts from the version it is based on, or publishing it would drop that
        # version's tenant terms and bindings.
        base = conn.execute(
            text(
                "SELECT version, draft_id, tenant_extension FROM ontologies "
                "ORDER BY version DESC LIMIT 1"
            )
        ).first()
        graph = base.tenant_extension.get("@graph", []) if base else []
        row = conn.execute(
            text(
                f"""
                INSERT INTO ontology_drafts
                    (id, tenant_id, base_version, composition, tenant_extension, created_by)
                VALUES (:id, :tenant, :base, CAST(:composition AS jsonb),
                    CAST(:extension AS jsonb), :user)
                RETURNING {_DRAFT_COLUMNS}
                """
            ),
            {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "base": base.version if base else 0,
                "composition": json.dumps([artifact.reference() for artifact in composition]),
                "extension": json.dumps(_extension(ctx, conn, graph)),
                "user": ctx.principal.id,
            },
        ).one()
        if base:
            mappings.copy_bindings(ctx, conn, base.draft_id, row.id)
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.draft.created", "ontology",
            {"draft_id": str(row.id), "base_version": row.base_version},
        )
        return _draft_view(conn, row)


def read_open_draft(ctx: TenantContext) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(f"SELECT {_DRAFT_COLUMNS} FROM ontology_drafts WHERE status = 'OPEN'")
        ).first()
        if row is None:
            raise not_found()
        return _draft_view(conn, row)


def _contains_context(value: Any) -> bool:
    if isinstance(value, dict):
        return "@context" in value or any(_contains_context(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_context(item) for item in value)
    return False


def _check_extension_graph(graph: list[dict[str, Any]]) -> None:
    """Extensions add tenant terms. Redefining a core or pack term needs a versioned migration.

    A nested @context could name a remote URL that the JSON-LD parser would fetch, so none is
    accepted at any depth. The server supplies the only context.
    """
    for node in graph:
        node_id = node.get("@id")
        if not isinstance(node_id, str) or not node_id.startswith("tenant:"):
            raise validation_failed(f"extension term must use the tenant prefix: {node_id!r}")
        if _contains_context(node):
            raise validation_failed("extension terms cannot declare their own @context")


def abandon_draft(ctx: TenantContext, draft_id: UUID) -> dict[str, str]:
    """Close an open draft, for example when the active packs changed after it was created."""
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text("SELECT status FROM ontology_drafts WHERE id = :id FOR UPDATE"), {"id": draft_id}
        ).first()
        if row is None:
            raise not_found()
        if row.status != "OPEN":
            raise conflict("STALE_DRAFT", "This draft is no longer open.")
        conn.execute(
            text(
                "UPDATE ontology_drafts SET status = 'ABANDONED', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": draft_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.draft.abandoned", "ontology",
            {"draft_id": str(draft_id)},
        )
    return {"id": str(draft_id), "status": "ABANDONED"}


def edit_draft(
    ctx: TenantContext, draft_id: UUID, expected_revision: int, graph: list[dict[str, Any]]
) -> dict[str, Any]:
    _check_extension_graph(graph)
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(f"SELECT {_DRAFT_COLUMNS} FROM ontology_drafts WHERE id = :id FOR UPDATE"),
            {"id": draft_id},
        ).first()
        if row is None:
            raise not_found()
        if row.status != "OPEN" or row.revision != expected_revision:
            raise conflict("STALE_DRAFT", "The draft changed. Reload it and try again.")
        updated = conn.execute(
            text(
                f"""
                UPDATE ontology_drafts
                SET tenant_extension = CAST(:extension AS jsonb), revision = revision + 1,
                    updated_at = now()
                WHERE id = :id
                RETURNING {_DRAFT_COLUMNS}
                """
            ),
            {"id": draft_id, "extension": json.dumps(_extension(ctx, conn, graph))},
        ).one()
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.draft.edited", "ontology",
            {"draft_id": str(draft_id), "revision": updated.revision},
        )
        return _draft_view(conn, updated)
