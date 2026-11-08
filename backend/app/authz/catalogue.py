"""Loads and validates the permission catalogue and permission-set seed data."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

_HERE = Path(__file__).parent
PERMISSIONS_FILE = _HERE / "permissions.yaml"
PERMISSION_SETS_FILE = _HERE / "permission_sets.yaml"

GRANT_AUTHORITY = "member.role.assign"
HUMAN_ONLY = {"playbook.approve", "platform.support.approve"}
WORKER_ONLY = {"playbook.execute", "connector.action.invoke"}
CI_ONLY = {"platform.release.attest"}


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class PermissionSpec:
    key: str
    resource: str
    action: str
    scope_type: Literal["tenant", "platform"]
    narrowable_scopes: tuple[str, ...]
    principal_types: tuple[str, ...]
    sensitivity: str
    grant_requires: tuple[str, ...]
    tenant_grantable: bool
    namespace: str
    release: str
    description: str


@dataclass(frozen=True)
class PermissionSetSpec:
    key: str
    display_name: str
    principal_type: str
    derivation: str | None
    allow: tuple[str, ...]
    deny: tuple[str, ...]


class Catalogue:
    def __init__(self, permissions: dict[str, PermissionSpec]) -> None:
        self._permissions = permissions

    def __contains__(self, key: str) -> bool:
        return key in self._permissions

    def __iter__(self):
        return iter(self._permissions.values())

    def get(self, key: str) -> PermissionSpec:
        try:
            return self._permissions[key]
        except KeyError:
            raise CatalogueError(f"unknown permission key: {key}") from None


def _parse_entry(key: str, entry: list, scope_type: str, release: str) -> PermissionSpec:
    principals, narrowable, sensitivity, description, *flags = entry
    if sensitivity not in {"normal", "sensitive", "critical"}:
        raise CatalogueError(f"{key}: invalid sensitivity {sensitivity!r}")
    grantable = scope_type == "tenant" and "reserved" not in flags
    resource, _, action = key.rpartition(".")
    return PermissionSpec(
        key=key,
        resource=resource,
        action=action,
        scope_type=scope_type,  # type: ignore[arg-type]
        narrowable_scopes=tuple(narrowable),
        principal_types=tuple(principals),
        sensitivity=sensitivity,
        grant_requires=(GRANT_AUTHORITY,) if grantable else (),
        tenant_grantable=grantable,
        namespace="core",
        release=release,
        description=description,
    )


def _check_principal_ceilings(permissions: dict[str, PermissionSpec]) -> None:
    """POL-03: human-only, worker-only and CI-only permissions."""
    ceilings = ((HUMAN_ONLY, {"human", "platform_human"}), (WORKER_ONLY, {"worker"}),
                (CI_ONLY, {"ci"}))
    for keys, allowed in ceilings:
        for key in keys:
            if not set(permissions[key].principal_types) <= allowed:
                raise CatalogueError(f"{key}: violates the POL-03 principal ceiling")


def load_catalogue(path: Path = PERMISSIONS_FILE) -> Catalogue:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    permissions: dict[str, PermissionSpec] = {}
    for scope_type in ("tenant", "platform"):
        for key, entry in raw[scope_type].items():
            if key in permissions:
                raise CatalogueError(f"duplicate permission key: {key}")
            permissions[key] = _parse_entry(key, entry, scope_type, raw["release"])
    _check_principal_ceilings(permissions)
    return Catalogue(permissions)


def load_permission_sets(
    catalogue: Catalogue, path: Path = PERMISSION_SETS_FILE
) -> list[PermissionSetSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["sets"]
    by_key = {entry["key"]: entry for entry in raw}
    result = []
    for entry in raw:
        allow = list(entry.get("allow", []))
        for included in entry.get("includes", []):
            allow.extend(by_key[included].get("allow", []))
        deny = entry.get("deny", [])
        for key in (*allow, *deny):
            spec = catalogue.get(key)
            if entry["principal_type"] not in spec.principal_types:
                raise CatalogueError(f"{entry['key']}: {key} is incompatible with its principal")
        result.append(
            PermissionSetSpec(
                key=entry["key"],
                display_name=entry["display_name"],
                principal_type=entry["principal_type"],
                derivation=entry.get("derivation"),
                allow=tuple(dict.fromkeys(allow)),
                deny=tuple(deny),
            )
        )
    return result


@lru_cache
def get_catalogue() -> Catalogue:
    return load_catalogue()
