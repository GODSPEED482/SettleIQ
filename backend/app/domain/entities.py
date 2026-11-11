"""Entity reads and identity-resolution review (spec 7.2, AC-REC-06, AC-REC-11, AC-REC-12).

An entity read returns both views of the entity: every observation, authoritative or not, and
the resolved values of the latest successful run. Review settles one PENDING_REVIEW decision:
accept links the record to one of the recorded candidates, reject leaves it unlinked. Both
record the reviewer, time and reason and are audited. Observations for an accepted link are
written by the next run, under that run's pinned mappings.
"""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain.projection import link
from app.errors import conflict, not_found, validation_failed


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["ACCEPT", "REJECT"]
    candidate_entity_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _candidate_only_on_accept(self) -> "ReviewBody":
        if (self.outcome == "ACCEPT") != (self.candidate_entity_id is not None):
            raise ValueError("accept names one candidate; reject names none")
        return self


def _text(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, list):
            value = [str(v) if isinstance(v, UUID) else v for v in value]
        elif isinstance(value, Decimal):
            value = format(value, "f")
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        out[key] = value
    return out


def list_entities(ctx: TenantContext, class_iri: str | None) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, class_iri, canonical_key, ontology_id, created_run_id, created_at
                FROM domain_entities
                WHERE CAST(:class AS text) IS NULL OR class_iri = :class
                ORDER BY class_iri, canonical_key
                LIMIT 1000
                """
            ),
            {"class": class_iri},
        ).all()
    return [_text(dict(row._mapping)) for row in rows]


def read_entity(ctx: TenantContext, entity_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        entity = conn.execute(
            text(
                "SELECT id, class_iri, canonical_key, ontology_id, created_run_id, created_at "
                "FROM domain_entities WHERE id = :id"
            ),
            {"id": entity_id},
        ).first()
        if entity is None:
            raise not_found()
        links = conn.execute(
            text(
                "SELECT source_record_id, decision_id, created_at FROM entity_source_links "
                "WHERE entity_id = :id ORDER BY created_at, source_record_id"
            ),
            {"id": entity_id},
        ).all()
        observations = conn.execute(
            text(
                """
                SELECT id, property_iri, source_id, source_record_id, raw_evidence_id,
                    evidence_hash, field_path, mapping_id, mapping_version, raw_value,
                    normalized_value, numeric_value, datatype, unit, currency, value_basis,
                    temporal_context, occurred_at, observed_at, ingested_at
                FROM property_observations WHERE entity_id = :id
                ORDER BY property_iri, observed_at, id
                """
            ),
            {"id": entity_id},
        ).all()
        resolved = conn.execute(
            text(
                """
                SELECT v.run_id, v.property_iri, v.status, v.value, v.numeric_value, v.currency,
                    v.authority_assertion_id, v.authority_version, v.mapping_versions,
                    v.contributing_observation_ids, v.resolved_at
                FROM resolved_property_views v
                WHERE v.entity_id = :id AND v.run_id = (
                    SELECT r.id FROM domain_runs r
                    JOIN resolved_property_views w ON w.run_id = r.id AND w.entity_id = :id
                    WHERE r.status = 'SUCCEEDED'
                    ORDER BY r.started_at DESC, r.id DESC LIMIT 1)
                ORDER BY v.property_iri
                """
            ),
            {"id": entity_id},
        ).all()
    return _text(dict(entity._mapping)) | {
        "source_links": [_text(dict(row._mapping)) for row in links],
        "observations": [_text(dict(row._mapping)) for row in observations],
        "resolved": [_text(dict(row._mapping)) for row in resolved],
    }


_DECISION_COLUMNS = """
    id, run_id, source_record_id, class_iri, policy_id, policy_version, method, features,
    candidate_entity_ids, selected_entity_id, status, reviewer_id, reviewed_at, reason,
    created_at
"""


def list_decisions(ctx: TenantContext, status: str | None) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {_DECISION_COLUMNS} FROM entity_resolution_decisions
                WHERE status = COALESCE(CAST(:status AS text), 'PENDING_REVIEW')
                ORDER BY created_at, id
                LIMIT 500
                """
            ),
            {"status": status},
        ).all()
    return [_text(dict(row._mapping)) for row in rows]


def review(ctx: TenantContext, decision_id: UUID, body: ReviewBody) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        decision = conn.execute(
            text(f"SELECT {_DECISION_COLUMNS} FROM entity_resolution_decisions "
                 "WHERE id = :id FOR UPDATE"),
            {"id": decision_id},
        ).first()
        if decision is None:
            raise not_found()
        if decision.status != "PENDING_REVIEW":
            raise conflict("DECISION_NOT_PENDING", "This identity decision is already settled.")
        selected = body.candidate_entity_id
        if selected is not None and selected not in decision.candidate_entity_ids:
            raise validation_failed("The selected entity is not one of the candidates.")
        status = "ACCEPTED" if body.outcome == "ACCEPT" else "REJECTED"
        conn.execute(
            text(
                """
                UPDATE entity_resolution_decisions SET status = :status,
                    selected_entity_id = :selected, reviewer_id = :reviewer,
                    reviewed_at = now(), reason = :reason
                WHERE id = :id
                """
            ),
            {"status": status, "selected": selected, "reviewer": ctx.principal.id,
             "reason": body.reason, "id": decision_id},
        )
        if selected is not None:
            link(conn, ctx.tenant_id, selected, decision.source_record_id, decision_id)
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "entity_resolution.reviewed",
            "entity_resolution",
            {"decision_id": str(decision_id), "outcome": status,
             "selected_entity_id": str(selected) if selected else None, "reason": body.reason},
        )
        row = conn.execute(
            text(f"SELECT {_DECISION_COLUMNS} FROM entity_resolution_decisions WHERE id = :id"),
            {"id": decision_id},
        ).one()
    return _text(dict(row._mapping))
