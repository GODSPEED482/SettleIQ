"""Connector manifest schema, negotiation and lifecycle (spec 4.4, OPEN-01, OPEN-CAP-01).

Negotiation: a source binds one declared protocol and source type at registration and keeps
a snapshot of the manifest's capabilities. Callers check that snapshot for the keys they
need; packs reference keys without versions, so negotiation is by presence.

Lifecycle: a source is pinned to the manifest version it was registered with and is never
silently upgraded. Minor and patch versions are additive only; a different major version, or
a manifest older than the source, fails closed. This build has no upgrade route: disable the
source and register it again. Any manifest change also voids the POL-14 attestation (9.7).
"""

from collections.abc import Iterable
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


class RateLimit(_Strict):
    """Provider rate limits as verified, never assumed (Appendix C)."""

    status: Literal["documented", "undisclosed", "not_applicable"]
    requests_per_minute: int | None = Field(default=None, ge=1)
    guidance: list[str] = []

    @model_validator(mode="after")
    def _figure_only_when_documented(self) -> "RateLimit":
        if (self.status == "documented") != (self.requests_per_minute is not None):
            raise ValueError("requests_per_minute is required exactly when status is documented")
        return self


class RetryPolicy(_Strict):
    """Transport retries. An action is retried only under its original idempotency key, and an
    ambiguous outcome is settled by readback before any retry (spec 9.4)."""

    retry_on: list[Literal["429", "5xx", "network"]]
    honour_retry_after: bool
    backoff: Literal["exponential", "none"]
    max_attempts: int = Field(ge=1)
    actions: Literal["idempotency_key_only", "never"]


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
    rate_limit: RateLimit
    retry: RetryPolicy
    supported_schema_versions: list[str]
    health_signals: list[str] = Field(min_length=1)
    # Provider limits, keyed by a capability this manifest declares.
    limits: dict[str, dict[str, Any]]

    @model_validator(mode="after")
    def _unique_capabilities(self) -> "ConnectorManifest":
        keys = [capability.key for capability in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate capability key")
        undeclared = set(self.limits) - set(keys)
        if undeclared:
            raise ValueError(f"limits for undeclared capabilities {sorted(undeclared)}")
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
        if manifest.connector_id in manifests:
            raise ValueError(f"duplicate connector_id {manifest.connector_id!r} in {path.name}")
        manifests[manifest.connector_id] = manifest
    return manifests


def select_binding(
    manifest: ConnectorManifest, protocol: str | None = None, source_type: str | None = None
) -> tuple[str, str]:
    """Pick the protocol and source type a new source binds. Defaults to the first declared."""
    protocol = manifest.protocols[0] if protocol is None else protocol
    source_type = manifest.source_types[0] if source_type is None else source_type
    if protocol not in manifest.protocols:
        raise ValueError(f"{manifest.connector_id} does not support protocol {protocol!r}")
    if source_type not in manifest.source_types:
        raise ValueError(f"{manifest.connector_id} does not support source type {source_type!r}")
    return protocol, source_type


def require_capabilities(
    source_capabilities: list[dict[str, Any]], keys: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Return the source's declared entry for each canonical key, or fail naming the missing."""
    registry = load_capability_registry()
    aliases = load_capability_aliases()
    declared = {
        canonical_key(entry["key"], registry, aliases): entry for entry in source_capabilities
    }
    wanted = list(keys)
    missing = sorted(set(wanted) - set(declared))
    if missing:
        raise ValueError(f"source does not declare {missing}")
    return {key: declared[key] for key in wanted}


def _semver(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def check_source_compatible(source_version: str, manifest: ConnectorManifest) -> None:
    """Fail closed when a source's pinned version cannot be served by the current manifest."""
    pinned, current = _semver(source_version), _semver(manifest.version)
    if pinned[0] != current[0] or current < pinned:
        raise ValueError(
            f"{manifest.connector_id} {manifest.version} cannot serve a source registered "
            f"at {source_version}"
        )
