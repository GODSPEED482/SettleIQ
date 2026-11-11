"""Playbook definitions (spec 9.1, 9.6). Platform schema, domain-neutral; packs supply the data.

A pack's executable template names a definition file. The definition declares its trigger case
types, the evidence it needs, its preconditions as pack code, its approval policy, the
parameters a tenant may supply, the one connector action it takes, its idempotency strategy,
its failure path and how the outcome is verified. Definitions are immutable data: a change is a
new version, and nothing at runtime, including the Agent, can alter the active one.

Preconditions are a pack entrypoint called with a case evidence snapshot (app/playbooks/
evidence.py) and the request parameters. It returns a plain dict:
`{"eligible": bool, "failures": [str], "target": {...}, "proof": {...}}`. The gateway, the
Agent's recommend and validate tools and execution-stage revalidation all call the same one.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.manifest import SEMVER, load_capability_registry, load_connector_manifests
from app.domain_packs.manifest import (
    ENTRYPOINT,
    PACKS_DIR,
    DomainPackManifest,
    PlaybookTemplate,
    load_domain_packs,
)
from app.domain_packs.registry import resolve_entrypoint

ApprovalPolicy = Literal["TENANT_POLICY", "ALWAYS_REQUIRED"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Parameter(_Strict):
    """One tenant-supplied parameter. Nothing here can name a tenant, a principal or a decision."""

    type: Literal["string", "integer", "boolean"]
    required: bool = False
    max_length: int = Field(default=200, ge=1, le=2000)


class Action(_Strict):
    connector_id: str
    capability: str


class Idempotency(_Strict):
    # The key is derived from the tenant, the playbook and these target fields, so the same
    # logical operation always maps to one key and one side effect (spec 9.4).
    strategy: Literal["per_target"]
    target_fields: list[str] = Field(min_length=1)


class FailurePath(_Strict):
    on_failure: Literal["HUMAN_REVIEW", "OPEN"]
    on_ambiguous_outcome: Literal["MANUAL_REVIEW"]
    blind_retry: Literal[False]


class Verification(_Strict):
    # Readbacks re-read authoritative state; API success is never verification (spec 9.4).
    readbacks: list[str] = Field(min_length=1)
    condition: str = Field(min_length=1, max_length=500)


class PlaybookDefinition(_Strict):
    id: str
    version: str = Field(pattern=SEMVER)
    trigger_case_types: list[str] = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)
    preconditions: str = Field(pattern=ENTRYPOINT)
    approval_policy: ApprovalPolicy
    parameters: dict[str, Parameter] = {}
    action: Action
    idempotency: Idempotency
    steps: list[str] = Field(min_length=1)
    failure_path: FailurePath
    verification: Verification
    fixtures: list[str] = []


@dataclass(frozen=True)
class Playbook:
    """One executable template of one pack, with its validated definition."""

    pack: DomainPackManifest
    template: PlaybookTemplate
    definition: PlaybookDefinition

    @property
    def qualified_id(self) -> str:
        return f"{self.pack.namespace}:{self.template.id}"


class PlaybookDefinitionError(ValueError):
    pass


def _check(pack: DomainPackManifest, template: PlaybookTemplate,
           definition: PlaybookDefinition) -> None:
    where = f"{pack.namespace}:{template.id}"
    if definition.id != template.id:
        raise PlaybookDefinitionError(f"{where}: definition is for {definition.id}")
    registered = {case.code for case in pack.case_types}
    unknown = set(definition.trigger_case_types) - registered
    if unknown:
        raise PlaybookDefinitionError(f"{where}: unregistered case types {sorted(unknown)}")
    if not definition.preconditions.startswith(f"packs.{pack.package}."):
        raise PlaybookDefinitionError(f"{where}: preconditions are outside the pack")
    if [definition.action.capability] != template.connector_action_capabilities:
        raise PlaybookDefinitionError(f"{where}: the action must be the template's capability")
    connector = load_connector_manifests().get(definition.action.connector_id)
    declared = {c.key for c in connector.capabilities} if connector else set()
    if definition.action.capability not in declared:
        raise PlaybookDefinitionError(
            f"{where}: {definition.action.connector_id} does not declare "
            f"{definition.action.capability}"
        )
    registry = load_capability_registry()
    for readback in definition.verification.readbacks:
        if readback not in template.capability_dependencies or readback not in declared:
            raise PlaybookDefinitionError(f"{where}: readback {readback} is not a dependency")
        if registry[readback].kind != "read":
            raise PlaybookDefinitionError(f"{where}: readback {readback} is not a read")
    missing = [f for f in definition.fixtures if not (PACKS_DIR / pack.package / f).is_file()]
    if missing:
        raise PlaybookDefinitionError(f"{where}: missing fixtures {missing}")


def load_playbooks(
    packs: Mapping[str, DomainPackManifest] | None = None, directory: Path = PACKS_DIR
) -> dict[str, Playbook]:
    """Every executable template with its definition, keyed by `namespace:ID`.

    Fails on an executable template without a definition, an invalid definition or
    preconditions that cannot be resolved, so a bad pack fails startup.
    """
    packs = load_domain_packs(directory) if packs is None else packs
    playbooks = {}
    for pack in packs.values():
        for template in pack.playbook_templates:
            if not template.executable:
                continue
            if template.definition is None:
                continue
            path = directory / pack.package / template.definition
            definition = PlaybookDefinition(**yaml.safe_load(path.read_text(encoding="utf-8")))
            _check(pack, template, definition)
            resolve_entrypoint(definition.preconditions)
            playbook = Playbook(pack, template, definition)
            playbooks[playbook.qualified_id] = playbook
    return playbooks


@cache
def get_playbooks() -> dict[str, Playbook]:
    return load_playbooks()
