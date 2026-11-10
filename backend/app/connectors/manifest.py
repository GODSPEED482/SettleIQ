"""Connector manifest schema and validation. Unsupported capabilities are explicit."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_HERE = Path(__file__).parent
REGISTRY_FILE = _HERE / "capability_registry.yaml"
MANIFEST_DIR = _HERE / "manifests"

SEMVER = r"^\d+\.\d+\.\d+$"
CapabilityKind = Literal["read", "action", "event", "file"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegisteredCapability(_Strict):
    key: str
    kind: CapabilityKind


class DeclaredCapability(_Strict):
    key: str
    version: str = Field(pattern=SEMVER)
    parameters: dict[str, Any]


class CapabilityAlias(_Strict):
    alias: str
    canonical: str


# Frozen under task 1.6 (spec 4.4, OPEN-01 and OPEN-CAP-01). Unsupported capabilities are
# stated explicitly, and every capability key is canonical or a registered versioned alias.
class ConnectorManifest(_Strict):
    connector_id: str
    version: str = Field(pattern=SEMVER)
    protocols: list[str] = Field(min_length=1)
    source_types: list[str] = Field(min_length=1)
    auth_mode: str
    secret_reference_required: bool

    # Required booleans, so an unsupported capability is stated rather than inferred.
    supports_discovery: bool
    supports_metadata: bool
    supports_batch_extraction: bool
    supports_incremental: bool
    supports_events: bool
    supports_file_ingestion: bool
    supports_detail_lookup: bool

    extraction_modes: list[str]
    capabilities: list[DeclaredCapability]
    rate_limit: dict[str, Any]
    retry: dict[str, Any]
    supported_schema_versions: list[str]
    health_signals: list[str] = Field(min_length=1)
    limits: dict[str, Any]

    @model_validator(mode="after")
    def _unique_capabilities(self) -> "ConnectorManifest":
        keys = [capability.key for capability in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate capability key")
        return self


@lru_cache
def load_capability_registry() -> dict[str, RegisteredCapability]:
    raw = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    return {item["key"]: RegisteredCapability(**item) for item in raw["capabilities"]}


@lru_cache
def load_capability_aliases() -> dict[str, str]:
    """Registered legacy spellings, mapped to their canonical key. Empty unless added."""
    raw = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    aliases = raw.get("aliases", {}).get("mappings", [])
    return {item["alias"]: item["canonical"] for item in (CapabilityAlias(**a) for a in aliases)}


def canonical_key(
    key: str, registry: dict[str, RegisteredCapability], aliases: dict[str, str]
) -> str:
    """Resolve one key to its canonical spelling, or raise if it is not registered."""
    if key in registry:
        return key
    canonical = aliases.get(key)
    if canonical is None or canonical not in registry:
        raise ValueError(f"non-canonical key {key!r}")
    return canonical


def validate_manifest(
    manifest: ConnectorManifest,
    registry: dict[str, RegisteredCapability],
    aliases: dict[str, str] | None = None,
) -> None:
    """Reject non-canonical keys and flags that contradict the declared capabilities."""
    aliases = load_capability_aliases() if aliases is None else aliases
    kinds = set()
    for capability in manifest.capabilities:
        try:
            canonical = canonical_key(capability.key, registry, aliases)
        except ValueError:
            raise ValueError(
                f"{manifest.connector_id}: non-canonical key {capability.key!r}"
            ) from None
        kinds.add(registry[canonical].kind)
    if manifest.supports_events != ("event" in kinds):
        raise ValueError(f"{manifest.connector_id}: supports_events contradicts capabilities")
    if manifest.supports_file_ingestion != ("file" in kinds):
        raise ValueError(
            f"{manifest.connector_id}: supports_file_ingestion contradicts capabilities"
        )


def load_connector_manifests(directory: Path = MANIFEST_DIR) -> dict[str, ConnectorManifest]:
    registry = load_capability_registry()
    aliases = load_capability_aliases()
    manifests = {}
    for path in sorted(directory.glob("*.yaml")):
        manifest = ConnectorManifest(**yaml.safe_load(path.read_text(encoding="utf-8")))
        validate_manifest(manifest, registry, aliases)
        manifests[manifest.connector_id] = manifest
    return manifests
