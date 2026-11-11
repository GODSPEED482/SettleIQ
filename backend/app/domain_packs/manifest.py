"""Domain-pack manifest schema. Core reads manifests as data and never imports a pack."""

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.connectors.manifest import SEMVER, load_capability_registry, load_connector_manifests

PACKS_DIR = Path(__file__).parents[2] / "packs"
RESERVED_NAMESPACES = {"core", "platform"}
PLATFORM_VERSION = "0.1.0"
TERM = r"^[a-z][a-z0-9_]*:[A-Za-z][A-Za-z0-9_]*$"
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


class HintField(_Strict):
    """One field of a binding hint, in the shape a mapping takes (app/semantics/mappings.py)."""

    field_path: str = Field(min_length=1, max_length=500)
    property_iri: str = Field(pattern=TERM)
    datatype: Literal["decimal", "string", "datetime", "boolean", "integer"]
    transform: dict[str, Any]
    value_basis: str = Field(min_length=1, max_length=100)
    temporal_context: str = Field(min_length=1, max_length=100)
    currency_field: str | None = None
    unit: str | None = None
    nullable: bool = True
    is_unique: bool = False


class BindingHint(_Strict):
    """Bindings a pack proposes for one connector object. A tenant applies them to a draft for
    its own source, then validates and publishes them like any mapping, so a hint names neither."""

    kind: Literal["binding"]
    connector_id: str
    external_object: str = Field(min_length=1, max_length=300)
    class_iri: str = Field(pattern=TERM)
    sync_strategy: Literal["batch", "event", "file"]
    fields: list[HintField] = Field(min_length=1)


class AuthorityHint(_Strict):
    """The connector a pack proposes as authoritative for one property of one entity type."""

    kind: Literal["authority"]
    connector_id: str
    property_iri: str = Field(pattern=TERM)
    entity_type_iri: str = Field(pattern=TERM)
    verification_method: str = Field(min_length=1, max_length=200)


MappingHint = Annotated[BindingHint | AuthorityHint, Field(discriminator="kind")]


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
    mapping_hints: list[MappingHint]
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
        definitions = [t.definition for t in self.playbook_templates if t.definition]
        return declared + definitions + self.test_fixtures

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

    def _check_hints(self) -> None:
        connectors = load_connector_manifests()
        for hint in self.mapping_hints:
            if hint.connector_id not in connectors:
                raise ValueError(f"mapping hint names an unknown connector: {hint.connector_id}")

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
        self._check_hints()
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
