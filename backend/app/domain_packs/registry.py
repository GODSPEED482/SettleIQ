"""Resolves pack code named in manifests, and decides whether a pack may be activated.

This is the only route from core to pack code. Manifest validation has already confined each
entrypoint to its own pack's package, so core never names a pack in an import statement.
"""

from collections.abc import Callable, Iterable, Mapping
from importlib import import_module
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from app.domain_packs.manifest import DomainPackManifest


class PackRegistryError(RuntimeError):
    pass


class PackActivationError(ValueError):
    pass


def resolve_entrypoint(entrypoint: str) -> Callable[..., Any]:
    module_name, attribute = entrypoint.split(":", 1)
    try:
        target = getattr(import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise PackRegistryError(f"cannot resolve entrypoint {entrypoint}") from error
    if not callable(target):
        raise PackRegistryError(f"entrypoint {entrypoint} is not callable")
    return target


def resolve_pack_entrypoints(packs: Mapping[str, DomainPackManifest]) -> None:
    """Fail startup if any declared pack code cannot be found."""
    for pack in packs.values():
        for item in pack.entrypoints():
            resolve_entrypoint(item.entrypoint)


def assert_can_activate(
    candidate: DomainPackManifest, active: Iterable[DomainPackManifest]
) -> None:
    """Apply the OPEN-03 rules: a declared conflict either way, or an unmet dependency, refuses."""
    others = {pack.namespace: pack for pack in active if pack.namespace != candidate.namespace}
    if not candidate.supports_platform():
        raise PackActivationError(f"{candidate.namespace}: incompatible with this platform")
    for namespace, pack in others.items():
        if namespace in candidate.conflicts or candidate.namespace in pack.conflicts:
            raise PackActivationError(f"{candidate.namespace} conflicts with {namespace}")
    for dependency in candidate.dependencies:
        provider = others.get(dependency.namespace)
        if provider is None:
            raise PackActivationError(f"{candidate.namespace} needs {dependency.namespace}")
        if Version(provider.version) not in SpecifierSet(dependency.version):
            raise PackActivationError(
                f"{candidate.namespace} needs {dependency.namespace} {dependency.version}"
            )
