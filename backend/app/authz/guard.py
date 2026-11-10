"""Deny-by-default route guard (spec 11.5, AUTHZ-05).

Every route declares exactly one of `public()`, `identity()` or `require("<key>")`:

    @router.get("/api/tenants/{tenant_id}/members")
    def list_members(ctx: TenantContext = require("member.read")): ...

`validate_routes` runs when the app is built, so an undeclared route stops startup.
"""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute

from app.auth.sessions import allowed_origin, session_cookie_name, session_principal
from app.authz.access import (
    AccessGateway,
    Decision,
    EffectiveAccess,
    Principal,
    PrincipalKind,
    Scope,
    get_gateway,
)
from app.authz.catalogue import Catalogue, get_catalogue
from app.errors import email_not_verified, missing_permission, not_found, unauthenticated

logger = logging.getLogger("settleiq.authz")

# TODO(G1 OPEN-14 Suprava): ratify X-Tenant-Id as the selector for routes with no tenant path.
TENANT_HEADER = "X-Tenant-Id"
_ACCESS_ATTR = "__settleiq_access__"
FRAMEWORK_ROUTES = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class RouteDeclarationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Access:
    kind: Literal["PUBLIC", "IDENTITY", "PERMISSION"]
    permission: str | None = None
    scope: tuple[str, str] | None = None


@dataclass(frozen=True)
class TenantContext:
    """Server-derived tenant context. Only `require` should build one."""

    tenant_id: UUID
    principal: Principal
    access: EffectiveAccess


def authenticate(request: Request) -> Principal:
    """Resolve the session cookie against current state (AC-IAM-07, spec 11.2).

    Only the cookie is read, so a delegated agent token can never authenticate here. Do not
    add a development bypass; tests override this dependency instead.
    """
    raw = request.cookies.get(session_cookie_name())
    if raw is None:
        raise unauthenticated()
    origin = request.headers.get("origin")
    if request.method not in SAFE_METHODS and origin is not None and origin != allowed_origin():
        raise unauthenticated()
    principal = session_principal(raw)
    if principal is None:
        raise unauthenticated()
    return principal


def _declare(call: Any, access: Access) -> DependsParam:
    setattr(call, _ACCESS_ATTR, access)
    return Depends(call)


def public() -> Any:
    def guard() -> None:
        return None

    return _declare(guard, Access("PUBLIC"))


def _verified(principal: Principal) -> None:
    if principal.kind is PrincipalKind.HUMAN and not principal.email_verified:
        raise email_not_verified()


def identity() -> Any:
    def guard(principal: Principal = Depends(authenticate)) -> Principal:
        _verified(principal)
        return principal

    return _declare(guard, Access("IDENTITY"))


def _tenant_selector(request: Request) -> UUID:
    """The browser-supplied selector. It only picks a tenant; membership proves access."""
    raw = request.path_params.get("tenant_id") or request.headers.get(TENANT_HEADER)
    try:
        return UUID(raw)
    except (TypeError, ValueError):
        raise not_found() from None


def require(permission: str, *, scope: tuple[str, str] | None = None) -> Any:
    """Require one registered permission. `scope` is (scope_type, path_param)."""

    def guard(
        request: Request,
        principal: Principal = Depends(authenticate),
        gateway: AccessGateway = Depends(get_gateway),
    ) -> TenantContext | Principal:
        _verified(principal)
        spec = get_catalogue().get(permission)
        # AUTHZ-07: enforced at decision time as well as grant time.
        if principal.kind.value not in spec.principal_types:
            raise not_found()

        if spec.scope_type == "platform":
            platform_access = gateway.resolve_platform(principal)
            if platform_access is None or not platform_access.permits(permission):
                raise not_found()
            return principal

        tenant_id = _tenant_selector(request)
        access = gateway.resolve(principal, tenant_id)
        if access is None:
            # The precise reason stays in protected logs (spec 11.4).
            logger.info("tenant access refused principal=%s", principal.id)
            raise not_found()

        target = Scope(scope[0], request.path_params[scope[1]]) if scope else None
        allowed = access.permits(permission, target)
        if spec.sensitivity != "normal":
            # Committed separately so a denial is audited even though the request fails.
            gateway.record_decision(
                Decision(tenant_id, principal, permission, spec.resource, target, allowed)
            )
        if not allowed:
            raise missing_permission(permission)
        return TenantContext(tenant_id, principal, access)

    return _declare(guard, Access("PERMISSION", permission, scope))


def _declarations(dependant: Dependant) -> list[Access]:
    found = []
    for dependency in dependant.dependencies:
        access = getattr(dependency.call, _ACCESS_ATTR, None)
        if access is not None:
            found.append(access)
        found.extend(_declarations(dependency))
    return found


def access_of(route: APIRoute) -> list[Access]:
    return _declarations(route.dependant)


def _api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """Flatten routes, refusing anything that cannot be checked.

    Newer FastAPI wraps included routers instead of copying their routes onto the app.
    An unrecognised route type stops startup, so a framework change cannot skip the guard.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif (included := getattr(route, "original_router", None)) is not None:
            # An include-level prefix would make the exported route map wrong.
            if route.include_context.prefix:
                raise RouteDeclarationError("declare prefixes on the APIRouter, not on include")
            yield from _api_routes(included.routes)
        elif getattr(route, "path", None) not in FRAMEWORK_ROUTES:
            raise RouteDeclarationError(f"route type is not guardable: {route!r}")


def validate_routes(app: FastAPI, catalogue: Catalogue) -> None:
    """Fail startup if any route is undeclared, ambiguous or names an unknown key."""
    for route in _api_routes(app.routes):
        declarations = access_of(route)
        if len(declarations) != 1:
            raise RouteDeclarationError(
                f"{route.path}: expected exactly one access declaration, "
                f"found {len(declarations)}"
            )
        access = declarations[0]
        if access.kind != "PERMISSION":
            continue
        spec = catalogue.get(access.permission)
        if access.scope:
            scope_type, param = access.scope
            if scope_type not in spec.narrowable_scopes:
                raise RouteDeclarationError(
                    f"{route.path}: {access.permission} does not declare scope {scope_type}"
                )
            if f"{{{param}}}" not in route.path:
                raise RouteDeclarationError(f"{route.path}: no path parameter named {param}")


def build_route_map(*apps: FastAPI) -> list[dict]:
    """The exported route-to-permission map (spec 12.1)."""
    entries = []
    for app in apps:
        for route in _api_routes(app.routes):
            access = access_of(route)[0]
            for method in sorted(route.methods):
                entries.append(
                    {
                        "method": method,
                        "path": route.path,
                        "access": access.kind,
                        "permission": access.permission,
                        "scope": access.scope[0] if access.scope else None,
                        "scope_param": access.scope[1] if access.scope else None,
                    }
                )
    return sorted(entries, key=lambda entry: (entry["path"], entry["method"]))
