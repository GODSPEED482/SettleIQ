"""What an active pack needs from a tenant's bindings, read from what the pack already declares.

A pack resolves only the classes its exact-ID policy names, through the properties listed for
each, and links entities through the policy's `via` properties (spec 7.2). Its SHACL shapes name
the properties every instance of a class must carry (spec 5.8). Bindings that miss these still
publish, but the pack's run silently skips what they describe, so the Studio warns about them.
"""

from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import SH

from app.domain.inputs import load_policy
from app.domain_packs.manifest import PACKS_DIR, DomainPackManifest
from app.semantics.artifacts import pack_iri


def _curie(iri: URIRef, namespace: str) -> str | None:
    prefix = pack_iri(namespace)
    return f"{namespace}:{iri[len(prefix):]}" if str(iri).startswith(prefix) else None


def _required_properties(pack: DomainPackManifest, linked: set[str]) -> list[dict[str, str]]:
    required = []
    for item in pack.shacl_shapes:
        graph = Graph().parse(PACKS_DIR / pack.package / item.path, format="turtle")
        for shape, target in graph.subject_objects(SH.targetClass):
            for constraint in graph.objects(shape, SH.property):
                path = graph.value(constraint, SH.path)
                minimum = graph.value(constraint, SH.minCount)
                if not isinstance(path, URIRef) or not isinstance(minimum, Literal):
                    continue
                cls, prop = _curie(target, pack.namespace), _curie(path, pack.namespace)
                # A relationship comes from the resolution policy, never from a binding.
                if cls and prop and prop not in linked and int(minimum) >= 1:
                    message = graph.value(constraint, SH.message)
                    required.append({"class_iri": cls, "property_iri": prop,
                                     "message": str(message) if message else ""})
    return sorted(required, key=lambda row: (row["class_iri"], row["property_iri"]))


# Pack files do not change while the process runs, so each version is read once.
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def pack_requirements(pack: DomainPackManifest) -> dict[str, Any]:
    key = (pack.namespace, pack.version)
    if key not in _CACHE:
        policy = load_policy(pack)
        linked = {rule["predicate"] for rule in policy.relationships}
        _CACHE[key] = {
            "identities": policy.identities,
            "relationships": policy.relationships,
            "required_properties": _required_properties(pack, linked),
        }
    return _CACHE[key]
