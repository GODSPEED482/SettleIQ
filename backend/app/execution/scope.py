"""The canonical execution scope key (spec 9.5): `(tenant, domain pack, connector, action)`.

Every field is required and exact. A scope is accepted only if the tenant's active pack declares
the action capability, the connector's manifest declares it, and the capability registry calls
it an action, so there is no wildcard, alias or ambiguous form (AC-EXEC-MODE-02).
"""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from app.connectors.manifest import load_capability_registry, load_connector_manifests
from app.domain_packs.manifest import load_domain_packs
from app.domain_packs.playbooks import Playbook
from app.errors import validation_failed
from app.semantics.store import active_namespaces

PACK_ID = r"^[a-z][a-z0-9_]*$"
CONNECTOR_ID = r"^[a-z][a-z0-9-]*$"
CAPABILITY = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class ScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_pack_id: str = Field(pattern=PACK_ID)
    connector_id: str = Field(pattern=CONNECTOR_ID)
    action_capability: str = Field(pattern=CAPABILITY)


@dataclass(frozen=True)
class ExactScope:
    tenant_id: UUID
    domain_pack_id: str
    domain_pack_version: str
    connector_id: str
    action_capability: str


def exact_scope(conn: Connection, tenant_id: UUID, body: ScopeBody) -> ExactScope:
    """The scope `body` names for this tenant, or a 422 naming the field that does not fit."""
    if body.domain_pack_id not in active_namespaces(conn):
        raise validation_failed("domain_pack_id is not an active domain pack for this tenant.")
    pack = load_domain_packs()[body.domain_pack_id]
    if body.action_capability not in pack.connector_action_capabilities:
        raise validation_failed("action_capability is not an action this domain pack declares.")
    registered = load_capability_registry().get(body.action_capability)
    if registered is None or registered.kind != "action":
        raise validation_failed("action_capability is not a canonical action capability.")
    connector = load_connector_manifests().get(body.connector_id)
    if connector is None:
        raise validation_failed("connector_id is not a registered connector.")
    if body.action_capability not in {c.key for c in connector.capabilities}:
        raise validation_failed("The connector does not declare this action capability.")
    return ExactScope(tenant_id, pack.namespace, pack.version, connector.connector_id,
                      body.action_capability)


def playbook_scope(conn: Connection, tenant_id: UUID, playbook: Playbook) -> ExactScope:
    """The one exact scope a playbook's action runs under. Each playbook names its own connector
    and capability, so PB-001 and PB-002 never share a mode."""
    action = playbook.definition.action
    return exact_scope(conn, tenant_id, ScopeBody(
        domain_pack_id=playbook.pack.namespace, connector_id=action.connector_id,
        action_capability=action.capability,
    ))


def declared_scopes(conn: Connection, tenant_id: UUID) -> list[ExactScope]:
    """Every exact scope this tenant could switch: each action capability of each active pack,
    with each connector that declares it."""
    packs = load_domain_packs()
    connectors = load_connector_manifests()
    scopes = []
    for namespace in sorted(active_namespaces(conn)):
        pack = packs.get(namespace)
        if pack is None:
            continue
        for capability in sorted(pack.connector_action_capabilities):
            for connector in sorted(connectors.values(), key=lambda c: c.connector_id):
                if capability in {c.key for c in connector.capabilities}:
                    scopes.append(ExactScope(tenant_id, pack.namespace, pack.version,
                                             connector.connector_id, capability))
    return scopes
