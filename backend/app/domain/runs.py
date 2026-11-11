"""Version-pinned domain runs (spec 6.3, 7, AC-REC-08).

A run pins its versions and its source snapshot, then projects, observes, resolves and runs
the pack's engine over exactly those inputs, all in one transaction. Runs of one tenant are
serialized. If anything fails, nothing the run wrote survives and a FAILED run is recorded in
its own transaction, with the same pins, so the failure is visible.
"""

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain import findings, projection, views
from app.domain.inputs import RunInputs, load_inputs
from app.domain_packs.manifest import DomainPackManifest, load_domain_packs
from app.domain_packs.registry import resolve_entrypoint
from app.errors import ApiError, conflict, not_found, validation_failed
from app.semantics.hashing import content_hash
from app.semantics.store import active_namespaces

logger = logging.getLogger(__name__)


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")


def _pack(conn: Connection, namespace: str | None) -> DomainPackManifest:
    packs = load_domain_packs()
    active = [name for name in active_namespaces(conn) if name in packs]
    if namespace is None:
        if len(active) != 1:
            raise validation_failed("Name the domain pack to run.")
        namespace = active[0]
    if namespace not in active:
        raise conflict("PACK_NOT_ACTIVE", f"{namespace} is not active for this tenant.")
    return packs[namespace]


def _lock(conn: Connection, tenant_id: UUID) -> None:
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"domain_run:{tenant_id}"},
    )


_INSERT_RUN = text(
    """
    INSERT INTO domain_runs (id, tenant_id, pack_namespace, pack_version, ontology_id,
        ontology_version, ontology_content_hash, mapping_versions, authority_versions,
        policy_versions, rule_version, source_snapshots, inputs_hash, status, failure,
        requested_by, started_at, finished_at)
    VALUES (:id, :tenant, :pack_namespace, :pack_version, :ontology_id, :ontology_version,
        :ontology_content_hash, CAST(:mapping_versions AS jsonb),
        CAST(:authority_versions AS jsonb), CAST(:policy_versions AS jsonb), :rule_version,
        CAST(:source_snapshots AS jsonb), :inputs_hash, :status, :failure, :user, :started_at,
        CASE WHEN :status = 'RUNNING' THEN NULL ELSE now() END)
    """
)


def _insert_run(conn: Connection, ctx: TenantContext, run_id: UUID, inputs: RunInputs,
                status: str, failure: str | None = None) -> dict[str, Any]:
    pins = inputs.pins()
    conn.execute(
        _INSERT_RUN,
        {
            **{k: json.dumps(v) if isinstance(v, dict) else v for k, v in pins.items()},
            "id": run_id,
            "tenant": ctx.tenant_id,
            "inputs_hash": content_hash(pins),
            "status": status,
            "failure": failure,
            "user": ctx.principal.id,
            "started_at": inputs.as_of,
        },
    )
    return pins


def _execute(conn: Connection, ctx: TenantContext, run_id: UUID,
             inputs: RunInputs) -> list[dict[str, Any]]:
    pins = _insert_run(conn, ctx, run_id, inputs, "RUNNING")
    projection.project(conn, ctx.tenant_id, run_id, inputs)
    projection.relate(conn, ctx.tenant_id, run_id, inputs)
    observations = views.pinned_observations(conn, inputs)
    views.resolve_views(conn, ctx.tenant_id, run_id, inputs, observations)
    summary: list[dict[str, Any]] = []
    if inputs.pack.domain_engine is not None:
        state = findings.snapshot(conn, run_id, inputs, observations)
        raw = resolve_entrypoint(inputs.pack.domain_engine.entrypoint)(state)
        found = findings.validate(raw, inputs, state)
        summary = findings.open_cases(
            conn, ctx.tenant_id, run_id, inputs, pins, state, found, observations
        )
    conn.execute(
        text(
            "UPDATE domain_runs SET status = 'SUCCEEDED', findings = CAST(:f AS jsonb), "
            "finished_at = now() WHERE id = :id"
        ),
        {"f": json.dumps(summary), "id": run_id},
    )
    write_audit(
        conn, ctx.tenant_id, ctx.principal, "domain_run.completed", "domain_run",
        {"run_id": str(run_id), "findings": len(summary),
         "new_cases": sum(item["new_case"] for item in summary)},
    )
    return summary


def start_run(ctx: TenantContext, body: RunBody) -> dict[str, Any]:
    run_id = uuid4()
    try:
        with tenant_tx(ctx) as conn:
            _lock(conn, ctx.tenant_id)
            pack = _pack(conn, body.namespace)
            as_of = conn.execute(text("SELECT now()")).scalar_one()
            inputs = load_inputs(conn, pack, as_of)
            _execute(conn, ctx, run_id, inputs)
    except ApiError:
        raise
    except Exception as error:
        logger.exception("domain run %s failed", run_id)
        with tenant_tx(ctx) as conn:
            _lock(conn, ctx.tenant_id)
            as_of = conn.execute(text("SELECT now()")).scalar_one()
            inputs = load_inputs(conn, _pack(conn, body.namespace), as_of)
            _insert_run(conn, ctx, run_id, inputs, "FAILED", type(error).__name__)
            write_audit(
                conn, ctx.tenant_id, ctx.principal, "domain_run.failed", "domain_run",
                {"run_id": str(run_id), "error": type(error).__name__},
            )
    return read_run(ctx, run_id)


_RUN_COLUMNS = """
    id, pack_namespace, pack_version, ontology_id, ontology_version, ontology_content_hash,
    mapping_versions, authority_versions, policy_versions, rule_version, source_snapshots,
    inputs_hash, status, findings, failure, requested_by, started_at, finished_at
"""


def _view(row: Any) -> dict[str, Any]:
    out = dict(row._mapping)
    for key in ("id", "ontology_id", "requested_by"):
        out[key] = str(out[key])
    for key in ("started_at", "finished_at"):
        out[key] = out[key].isoformat() if out[key] else None
    return out


def read_run(ctx: TenantContext, run_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(f"SELECT {_RUN_COLUMNS} FROM domain_runs WHERE id = :id"), {"id": run_id}
        ).first()
    if row is None:
        raise not_found()
    return _view(row)


def list_runs(ctx: TenantContext) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(f"SELECT {_RUN_COLUMNS} FROM domain_runs ORDER BY started_at DESC, id LIMIT 100")
        ).all()
    return [_view(row) for row in rows]
