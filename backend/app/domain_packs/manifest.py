"""Domain-pack manifest schema. Core reads manifests as data and never imports a pack."""

from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.connectors.manifest import SEMVER, load_capability_registry

PACKS_DIR = Path(__file__).parents[2] / "packs"
RESERVED_NAMESPACES = {"core", "platform"}
PLATFORM_VERSION = "0.1.0"
ENTRYPOINT = r"^[a-z_]\w*(\.[a-z_]\w*)*:[A-Za-z_]\w*$"
# Each segment must start with a word character, which excludes "..", and "/" cannot lead.
RELATIVE_PATH = r"^[\w-][\w.-]*(/[\w-][\w.-]*)*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _specifier(value: str) -> SpecifierSet:
    try:
        return SpecifierSet(value)
    except InvalidSpecifier:
        raise ValueError(f"invalid version range: {value!r}") from None


class PackDependency(_Strict):
    namespace: str
    version: str


class PackEntrypoint(_Strict):
    """Pack code named as data, in `module:function` form. Resolved by registry.py."""

    id: str
    entrypoint: str = Field(pattern=ENTRYPOINT)


class PackFile(_Strict):
    id: str
    path: str = Field(pattern=RELATIVE_PATH)


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
    definition: str | None = Field(default=None, pattern=RELATIVE_PATH)
    connector_action_capabilities: list[str] = []
    # Readbacks a certified action depends on (spec 9.7.8). They are never separate scopes.
    capability_dependencies: list[str] = []


class DomainPackManifest(_Strict):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    # The directory under packs/. It can differ from the namespace.
    package: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=SEMVER)
    compatible_platform_versions: str
    dependencies: list[PackDependency]
    conflicts: list[str]
    ontology_definitions: list[PackFile]
    shacl_shapes: list[PackFile]
    mapping_hints: list[dict[str, Any]]
    validators: list[PackEntrypoint]
    entity_resolution_policies: list[PackEntrypoint]
    domain_engine: PackEntrypoint | None
    case_types: list[CaseType]
    agent_tools: list[AgentTool]
    permissions: list[str]
    playbook_templates: list[PlaybookTemplate]
    connector_action_capabilities: list[str]
    test_fixtures: list[str]

    def entrypoints(self) -> list[PackEntrypoint]:
        engine = [self.domain_engine] if self.domain_engine else []
        return [*self.validators, *self.entity_resolution_policies, *engine]

    def files(self) -> list[str]:
        declared = [item.path for item in [*self.ontology_definitions, *self.shacl_shapes]]
        return declared + [t.definition for t in self.playbook_templates if t.definition]

    def _check_entrypoints(self) -> None:
        prefix = f"packs.{self.package}."
        for item in self.entrypoints():
            if not item.entrypoint.startswith(prefix):
                raise ValueError(f"entrypoint {item.entrypoint} is outside package {self.package}")

    def _check_versions(self) -> None:
        _specifier(self.compatible_platform_versions)
        for dependency in self.dependencies:
            _specifier(dependency.version)

    def _check_capabilities(self) -> None:
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
            for key in template.capability_dependencies:
                if key not in registry or registry[key].kind != "read":
                    raise ValueError(f"{template.id}: not a canonical read capability: {key}")

    @model_validator(mode="after")
    def _check(self) -> "DomainPackManifest":
        if self.namespace in RESERVED_NAMESPACES:
            raise ValueError(f"reserved namespace: {self.namespace}")
        for key in self.permissions:
            if not key.startswith(f"{self.namespace}."):
                raise ValueError(f"permission {key} is outside namespace {self.namespace}")
        self._check_entrypoints()
        self._check_versions()
        self._check_capabilities()
        return self

    def supports_platform(self, version: str = PLATFORM_VERSION) -> bool:
        return Version(version) in _specifier(self.compatible_platform_versions)


def _validate_location(manifest: DomainPackManifest, directory: Path) -> None:
    if manifest.package != directory.name:
        raise ValueError(f"{manifest.namespace}: package does not match {directory.name}")
    missing = [path for path in manifest.files() if not (directory / path).is_file()]
    if missing:
        raise ValueError(f"{manifest.namespace}: missing files {missing}")


def load_domain_packs(directory: Path = PACKS_DIR) -> dict[str, DomainPackManifest]:
    packs = {}
    for path in sorted(directory.glob("*/manifest.yaml")):
        manifest = DomainPackManifest(**yaml.safe_load(path.read_text(encoding="utf-8")))
        _validate_location(manifest, path.parent)
        if not manifest.supports_platform():
            raise ValueError(f"{manifest.namespace}: incompatible with platform {PLATFORM_VERSION}")
        if manifest.namespace in packs:
            raise ValueError(f"duplicate pack namespace: {manifest.namespace}")
        packs[manifest.namespace] = manifest
    return packs
