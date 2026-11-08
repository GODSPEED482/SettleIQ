"""AUTHZ-05 startup checks and the AC-AUTHZ-03 table-driven route tests."""

from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.authz.access import EffectiveAccess, GrantRow, Principal, PrincipalKind, Scope, get_gateway
from app.authz.catalogue import CatalogueError, get_catalogue
from app.authz.guard import (
    TENANT_HEADER,
    RouteDeclarationError,
    TenantContext,
    authenticate,
    build_route_map,
    identity,
    public,
    require,
    validate_routes,
)
from app.main import create_app
from app.worker_api import create_worker_app

TENANT = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = UUID("22222222-2222-4222-8222-222222222222")
USER = Principal(uuid4(), PrincipalKind.HUMAN, email_verified=True)

app = create_app()
ROUTES = build_route_map(app)
PERMISSION_ROUTES = [
    r for r in ROUTES
    if r["access"] == "PERMISSION" and get_catalogue().get(r["permission"]).scope_type == "tenant"
]
AUTHENTICATED_ROUTES = [r for r in ROUTES if r["access"] != "PUBLIC"]


class FakeGateway:
    def __init__(self, grants: list[GrantRow] | None = None) -> None:
        self.access = {TENANT: EffectiveAccess(grants or [], get_catalogue())}
        self.decisions = []

    def resolve(self, principal, tenant_id):
        return self.access.get(tenant_id)

    def resolve_platform(self, principal):
        return None

    def record_decision(self, decision):
        self.decisions.append(decision)


def client_for(gateway: FakeGateway, principal: Principal = USER, target: FastAPI = app):
    target.dependency_overrides[authenticate] = lambda: principal
    target.dependency_overrides[get_gateway] = lambda: gateway
    return TestClient(target)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# TODO(G1 1.10 Suprava): add any new path parameter from the 1.10 routes to the list below.
def url(route: dict, tenant: UUID = TENANT) -> str:
    path = route["path"].replace("{tenant_id}", str(tenant))
    for name in ("token", "invitation_id", "user_id", "source_id", "case_id", "decision_id",
                 "investigation_id", "playbook_id", "request_id", "execution_id", "audit_id"):
        path = path.replace(f"{{{name}}}", str(uuid4()))
    return path


def call(client: TestClient, route: dict, tenant: UUID = TENANT):
    return client.request(route["method"], url(route, tenant), headers={TENANT_HEADER: str(tenant)})


def label(route: dict) -> str:
    return f"{route['method']} {route['path']}"


def full_grant(route: dict) -> GrantRow:
    return GrantRow(route["permission"], "ALLOW")


# Startup validation


def _app_with(router: APIRouter) -> FastAPI:
    target = FastAPI()
    target.include_router(router)
    return target


def test_undeclared_route_fails_startup():
    router = APIRouter()
    router.get("/open")(lambda: None)
    with pytest.raises(RouteDeclarationError, match="exactly one"):
        validate_routes(_app_with(router), get_catalogue())


def test_undeclared_route_on_an_included_router_fails_startup():
    inner = APIRouter()
    inner.get("/open")(lambda: None)
    outer = APIRouter()
    outer.include_router(inner)
    with pytest.raises(RouteDeclarationError, match="exactly one"):
        validate_routes(_app_with(outer), get_catalogue())


def test_include_level_prefix_is_refused():
    router = APIRouter()

    @router.get("/ok")
    def ok(_: None = public()) -> None: ...

    target = FastAPI()
    target.include_router(router, prefix="/api")
    with pytest.raises(RouteDeclarationError, match="prefixes"):
        validate_routes(target, get_catalogue())


def test_unguardable_route_type_fails_startup():
    target = FastAPI()
    target.add_route("/raw", lambda request: None)
    with pytest.raises(RouteDeclarationError, match="not guardable"):
        validate_routes(target, get_catalogue())


def test_double_declaration_fails_startup():
    router = APIRouter()

    @router.get("/two")
    def two(a: None = public(), b: Principal = identity()) -> None: ...

    with pytest.raises(RouteDeclarationError, match="exactly one"):
        validate_routes(_app_with(router), get_catalogue())


def test_unknown_permission_fails_startup():
    router = APIRouter()

    @router.get("/bad")
    def bad(ctx: TenantContext = require("nope.read")) -> None: ...

    with pytest.raises(CatalogueError):
        validate_routes(_app_with(router), get_catalogue())


def test_undeclared_scope_type_fails_startup():
    router = APIRouter()

    @router.get("/t/{x}")
    def scoped(x: str, ctx: TenantContext = require("tenant.read", scope=("source", "x"))) -> None:
        ...

    with pytest.raises(RouteDeclarationError, match="does not declare scope"):
        validate_routes(_app_with(router), get_catalogue())


# Spec 12.2 critical declarations


@pytest.mark.parametrize(
    ("method", "path", "access", "permission"),
    [
        ("POST", "/api/auth/register", "PUBLIC", None),
        ("POST", "/api/webhooks/paypal/{source_id}", "PUBLIC", None),
        ("POST", "/api/tenants", "IDENTITY", None),
        ("GET", "/api/me/permissions", "IDENTITY", None),
        ("POST", "/api/tenants/{tenant_id}/ownership-transfer", "PERMISSION",
         "tenant.ownership.transfer"),
        ("PUT", "/api/tenants/{tenant_id}/execution-mode", "PERMISSION", "execution_mode.manage"),
        ("PUT", "/api/sources/{source_id}/credentials", "PERMISSION", "source.credential.manage"),
        ("PUT", "/api/sources/{source_id}/webhooks", "PERMISSION", "source.webhook.manage"),
        ("POST", "/api/ontology/publish", "PERMISSION", "ontology.publish"),
        ("POST", "/api/playbooks/{playbook_id}/requests", "PERMISSION", "playbook.request"),
        ("POST", "/api/playbook-requests/{request_id}/approve", "PERMISSION", "playbook.approve"),
        ("POST", "/api/playbook-requests/{request_id}/reject", "PERMISSION", "playbook.reject"),
    ],
)
def test_critical_route_declarations(method, path, access, permission):
    match = [r for r in ROUTES if r["method"] == method and r["path"] == path]
    assert len(match) == 1
    assert (match[0]["access"], match[0]["permission"]) == (access, permission)


# TODO(G1 1.10 Suprava): add sign-in here once it joins the contract as a PUBLIC route.
def test_public_routes_are_only_the_expected_ones():
    assert {r["path"] for r in ROUTES if r["access"] == "PUBLIC"} == {
        "/healthz",
        "/api/auth/register",
        "/api/auth/verify-email",
        "/api/webhooks/paypal/{source_id}",
    }


# Table-driven route tests


@pytest.mark.parametrize("route", AUTHENTICATED_ROUTES, ids=label)
def test_unauthenticated_is_rejected(route):
    response = TestClient(app).request(
        route["method"],
        url(route),
        headers={TENANT_HEADER: str(TENANT)},
        params={"tenant_id": TENANT},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("route", PERMISSION_ROUTES, ids=label)
def test_missing_permission_is_403(route):
    response = call(client_for(FakeGateway()), route)
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "MISSING_PERMISSION",
        "message": "Permission required.",
        "permission": route["permission"],
    }


# TODO(G1 1.13 Suprava): this uses a fake gateway and accepts only 200 or 501, so it needs
# adjusting once 1.4, 1.5 and 1.7 handlers touch the database.
@pytest.mark.parametrize("route", PERMISSION_ROUTES, ids=label)
def test_held_permission_passes_the_gate(route):
    response = call(client_for(FakeGateway([full_grant(route)])), route)
    assert response.status_code in {200, 501}


@pytest.mark.parametrize("route", PERMISSION_ROUTES, ids=label)
def test_inaccessible_tenant_is_404(route):
    response = call(client_for(FakeGateway()), route, tenant=OTHER_TENANT)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Resource not found."}}


@pytest.mark.parametrize("route", PERMISSION_ROUTES, ids=label)
def test_explicit_deny_overrides_allow(route):
    gateway = FakeGateway([full_grant(route), GrantRow(route["permission"], "DENY")])
    assert call(client_for(gateway), route).status_code == 403


# Tenant selection and anti-enumeration


def test_missing_or_malformed_selector_is_the_same_404():
    client = client_for(FakeGateway([GrantRow("role.read", "ALLOW")]))
    expected = client.get("/api/permissions", headers={TENANT_HEADER: str(OTHER_TENANT)})
    assert expected.status_code == 404
    assert client.get("/api/permissions").content == expected.content
    malformed = client.get("/api/permissions", headers={TENANT_HEADER: "not-a-uuid"})
    assert malformed.content == expected.content
    assert malformed.headers.keys() == expected.headers.keys()


def test_me_permissions_non_member_matches_nonexistent():
    client = client_for(FakeGateway())
    first = client.get("/api/me/permissions", params={"tenant_id": OTHER_TENANT})
    second = client.get("/api/me/permissions", params={"tenant_id": uuid4()})
    assert first.status_code == second.status_code == 404
    assert first.content == second.content


def test_me_permissions_reports_effective_keys_and_scopes():
    grants = [
        GrantRow("tenant.read", "ALLOW"),
        GrantRow("source.sync", "ALLOW", Scope("source", "a")),
        GrantRow("case.read", "ALLOW"),
        GrantRow("case.read", "DENY"),
    ]
    body = client_for(FakeGateway(grants)).get(
        "/api/me/permissions", params={"tenant_id": TENANT}
    ).json()
    assert body["permissions"] == [
        {
            "key": "source.sync",
            "tenant_wide": False,
            "scopes": [{"type": "source", "id": "a"}],
            "denied_scopes": [],
        },
        {"key": "tenant.read", "tenant_wide": True, "scopes": [], "denied_scopes": []},
    ]


def test_unknown_route_uses_the_canonical_404():
    response = TestClient(app).get("/api/does-not-exist")
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Resource not found."}}


def test_unverified_identity_is_rejected():
    unverified = Principal(uuid4(), PrincipalKind.HUMAN, email_verified=False)
    response = client_for(FakeGateway(), unverified).post("/api/tenants")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


# Scope, audit and principal type


def test_scope_narrowed_grant_only_reaches_its_scope():
    client = client_for(FakeGateway([GrantRow("source.sync", "ALLOW", Scope("source", "a"))]))
    headers = {TENANT_HEADER: str(TENANT)}
    assert client.post("/api/sources/a/sync", headers=headers).status_code == 501
    assert client.post("/api/sources/b/sync", headers=headers).status_code == 403


def test_grant_with_undeclared_scope_type_grants_nothing():
    access = EffectiveAccess(
        [GrantRow("tenant.read", "ALLOW", Scope("source", "a"))], get_catalogue()
    )
    assert not access.permits("tenant.read")
    assert not access.permits("tenant.read", Scope("source", "a"))


def test_sensitive_decisions_are_audited():
    route = {"method": "POST", "path": "/api/ontology/publish", "permission": "ontology.publish"}
    allowed = FakeGateway([full_grant(route)])
    call(client_for(allowed), route)
    denied = FakeGateway()
    call(client_for(denied), route)
    assert [d.allowed for d in allowed.decisions] == [True]
    assert [(d.allowed, d.permission) for d in denied.decisions] == [(False, "ontology.publish")]


def test_normal_decisions_are_not_audited():
    gateway = FakeGateway([GrantRow("tenant.read", "ALLOW"), GrantRow("role.read", "ALLOW")])
    client_for(gateway).get("/api/permissions", headers={TENANT_HEADER: str(TENANT)})
    assert gateway.decisions == []


# Private Worker route


def test_worker_route_is_not_on_the_public_app():
    assert not [r for r in ROUTES if r["path"].startswith("/internal")]
    response = TestClient(app).post(f"/internal/executions/{uuid4()}/run")
    assert response.status_code == 404


def test_human_is_rejected_by_the_worker_route():
    worker_app = create_worker_app()
    gateway = FakeGateway([GrantRow("playbook.execute", "ALLOW")])
    client = client_for(gateway, target=worker_app)
    response = client.post(
        f"/internal/executions/{uuid4()}/run", headers={TENANT_HEADER: str(TENANT)}
    )
    assert response.status_code == 404
    assert gateway.decisions == []


def test_worker_with_single_permission_passes_the_gate():
    worker_app = create_worker_app()
    worker = Principal(uuid4(), PrincipalKind.WORKER)
    client = client_for(FakeGateway([GrantRow("playbook.execute", "ALLOW")]), worker, worker_app)
    response = client.post(
        f"/internal/executions/{uuid4()}/run", headers={TENANT_HEADER: str(TENANT)}
    )
    assert response.status_code == 501
