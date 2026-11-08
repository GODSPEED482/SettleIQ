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


def validate_manifest(
    manifest: ConnectorManifest, registry: dict[str, RegisteredCapability]
) -> None:
    """Reject non-canonical keys and flags that contradict the declared capabilities."""
    kinds = set()
    for capability in manifest.capabilities:
        if capability.key not in registry:
            raise ValueError(f"{manifest.connector_id}: non-canonical key {capability.key!r}")
        kinds.add(registry[capability.key].kind)
    if manifest.supports_events != ("event" in kinds):
        raise ValueError(f"{manifest.connector_id}: supports_events contradicts capabilities")
    if manifest.supports_file_ingestion != ("file" in kinds):
        raise ValueError(
            f"{manifest.connector_id}: supports_file_ingestion contradicts capabilities"
        )


def load_connector_manifests(directory: Path = MANIFEST_DIR) -> dict[str, ConnectorManifest]:
    registry = load_capability_registry()
    manifests = {}
    for path in sorted(directory.glob("*.yaml")):
        manifest = ConnectorManifest(**yaml.safe_load(path.read_text(encoding="utf-8")))
        validate_manifest(manifest, registry)
        manifests[manifest.connector_id] = manifest
    return manifests
