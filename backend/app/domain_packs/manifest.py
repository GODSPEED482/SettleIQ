"""Domain-pack manifest schema. Core reads manifests as data and never imports a pack."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.connectors.manifest import SEMVER, load_capability_registry

PACKS_DIR = Path(__file__).parents[2] / "packs"
RESERVED_NAMESPACES = {"core", "platform"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackDependency(_Strict):
    namespace: str
    version: str


class CaseType(_Strict):
    code: str
    default_severity: str


class AgentTool(_Strict):
    name: str
    permission: str


class PlaybookTemplate(_Strict):
    id: str
    title: str
    executable: bool
    connector_action_capabilities: list[str] = []


class DomainPackManifest(_Strict):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=SEMVER)
    compatible_platform_versions: str
    dependencies: list[PackDependency]
    conflicts: list[str]
    ontology_definitions: list[str]
    mapping_hints: list[dict[str, Any]]
    validators: list[str]
    entity_resolution_policies: list[str]
    case_types: list[CaseType]
    agent_tools: list[AgentTool]
    permissions: list[str]
    playbook_templates: list[PlaybookTemplate]
    connector_action_capabilities: list[str]
    test_fixtures: list[str]

    @model_validator(mode="after")
    def _check(self) -> "DomainPackManifest":
        if self.namespace in RESERVED_NAMESPACES:
            raise ValueError(f"reserved namespace: {self.namespace}")
        for key in self.permissions:
            if not key.startswith(f"{self.namespace}."):
                raise ValueError(f"permission {key} is outside namespace {self.namespace}")
        registry = load_capability_registry()
        for key in self.connector_action_capabilities:
            if key not in registry or registry[key].kind != "action":
                raise ValueError(f"not a canonical action capability: {key}")
        for template in self.playbook_templates:
            undeclared = set(template.connector_action_capabilities) - set(
                self.connector_action_capabilities
            )
            if undeclared:
                raise ValueError(f"{template.id}: undeclared capabilities {sorted(undeclared)}")
        return self


def load_domain_packs(directory: Path = PACKS_DIR) -> dict[str, DomainPackManifest]:
    packs = {}
    for path in sorted(directory.glob("*/manifest.yaml")):
        manifest = DomainPackManifest(**yaml.safe_load(path.read_text(encoding="utf-8")))
        if manifest.namespace in packs:
            raise ValueError(f"duplicate pack namespace: {manifest.namespace}")
        packs[manifest.namespace] = manifest
    return packs
