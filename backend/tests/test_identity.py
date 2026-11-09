"""Registration, verification, sessions and tenant creation through the real routes (task 1.9)."""

import re
from itertools import count
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import config
from app.auth import sessions
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import hash_token, new_token
from app.authz.access import PostgresAccessGateway, Principal, PrincipalKind
from app.authz.catalogue import get_catalogue
from app.db import global_tx, scoped_tx
from app.errors import ApiError
from app.mail import MailDeliveryError, SmtpMailSender, get_mail_sender
from app.main import create_app
from app.tenancy import creation

REPO = Path(__file__).parents[2]
PASSWORD = "correct horse battery"
_addresses = count(1)

app = create_app()


class FakeMail:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise MailDeliveryError("relay down")
        self.sent.append((to, body))

    def last_token(self) -> str:
        return re.search(r"#token=(\S+)", self.sent[-1][1]).group(1)


@pytest.fixture
def mail():
    fake = FakeMail()
    app.dependency_overrides[get_mail_sender] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def new_client() -> TestClient:
    # A distinct address per client, so rate limits persisted between runs cannot interfere.
    return TestClient(app, client=(f"10.{uuid4().int % 250}.0.{next(_addresses) % 250}", 50000))


def new_email() -> str:
    return f"{uuid4().hex}@example.com"


def register(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def sign_in(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/sign-in", json={"email": email, "password": password})


def verified_client(mail: FakeMail) -> tuple[TestClient, str]:
    client, email = new_client(), new_email()
    assert register(client, email).status_code == 202
    verified = client.post("/api/auth/verify-email", json={"token": mail.last_token()})
    assert verified.status_code == 200
    assert sign_in(client, email).status_code == 200
    return client, email


def user_row(email: str):
    with global_tx() as conn:
        return conn.execute(
            text("SELECT id, email_verified_at FROM users WHERE email = :e"), {"e": email}
        ).first()


def create_tenant(client: TestClient, name: str = "Acme") -> UUID:
    response = client.post("/api/tenants", json={"name": name})
    assert response.status_code == 201
    return UUID(response.json()["id"])


# No database


def test_password_round_trip():
    stored = hash_password(PASSWORD)
    assert stored != PASSWORD
    assert verify_password(stored, PASSWORD)
    assert not verify_password(stored, "wrong password here")


def test_missing_account_never_verifies():
    assert not verify_password(None, "settleiq-decoy-password")


def test_tokens_are_random_and_only_the_hash_is_kept():
    first, first_hash = new_token()
    second, _ = new_token()
    assert first != second
    assert len(first) >= 43
    assert first_hash == hash_token(first) != first


def test_mail_fails_closed_without_a_relay(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused")
    monkeypatch.setenv("SMTP_HOST", "")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(MailDeliveryError, match="not configured"):
            SmtpMailSender().send("a@example.com", "s", "b")
    finally:
        config.get_settings.cache_clear()


class _Settings:
    session_ttl_hours = 24
    app_base_url = "https://app.example.com/some/path"

    def __init__(self, environment: str) -> None:
        self.environment = environment


def test_production_cookie_is_host_prefixed_and_locked_down(monkeypatch):
    from fastapi import Response

    monkeypatch.setattr(sessions, "get_settings", lambda: _Settings("production"))
    response = Response()
    sessions.set_session_cookie(response, "raw")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-settleiq_session=raw")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert attribute in cookie
    assert "Domain" not in cookie
    assert sessions.allowed_origin() == "https://app.example.com"


@pytest.mark.parametrize("file", ["docker-compose.yml", "deploy/compose.yaml"])
def test_only_the_api_receives_smtp_credentials(file):
    services = yaml.safe_load((REPO / file).read_text(encoding="utf-8"))["services"]
    holders = [n for n, s in services.items() if "SMTP_PASSWORD" in (s.get("environment") or {})]
    assert holders == ["api"]


# Registration and verification


@pytest.mark.db
def test_registration_creates_an_unverified_account_and_sends_a_link(mail):
    email = new_email()
    assert register(new_client(), email).json() == {"status": "accepted"}
    row = user_row(email)
    assert row is not None and row.email_verified_at is None
    assert mail.sent[0][0] == email


@pytest.mark.db
def test_token_is_stored_only_as_a_hash(mail):
    email = new_email()
    register(new_client(), email)
    raw = mail.last_token()
    with global_tx() as conn:
        stored = conn.execute(
            text("SELECT token_hash FROM email_verification_tokens WHERE user_id = :u"),
            {"u": user_row(email).id},
        ).scalar_one()
    assert stored == hash_token(raw) != raw


@pytest.mark.db
def test_password_is_not_stored_in_clear(mail):
    email = new_email()
    register(new_client(), email)
    with global_tx() as conn:
        stored = conn.execute(
            text("SELECT password_hash FROM user_credentials WHERE user_id = :u"),
            {"u": user_row(email).id},
        ).scalar_one()
    assert stored.startswith("$argon2id$")


@pytest.mark.db
def test_duplicate_registration_is_indistinguishable_and_changes_nothing(mail):
    client, email = new_client(), new_email()
    first = register(client, email)
    second = register(client, email, "a different password")
    assert (first.status_code, first.content) == (second.status_code, second.content)
    assert len(mail.sent) == 1
    client.post("/api/auth/verify-email", json={"token": mail.last_token()})
    assert sign_in(client, email, "a different password").status_code == 401
    assert sign_in(client, email).status_code == 200


@pytest.mark.db
def test_email_case_does_not_create_a_second_account(mail):
    client, email = new_client(), new_email()
    register(client, email.upper().replace("EXAMPLE.COM", "example.com"))
    register(client, email)
    assert len(mail.sent) == 1
    assert user_row(email) is not None


@pytest.mark.db
def test_short_password_is_rejected(mail):
    assert register(new_client(), new_email(), "short").status_code == 422


@pytest.mark.db
def test_verification_token_is_single_use(mail):
    client, email = new_client(), new_email()
    register(client, email)
    token = mail.last_token()
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert user_row(email).email_verified_at is not None
    replay = client.post("/api/auth/verify-email", json={"token": token})
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.db
def test_expired_verification_token_is_rejected(mail):
    client, email = new_client(), new_email()
    register(client, email)
    with global_tx() as conn:
        conn.execute(
            text(
                "UPDATE email_verification_tokens SET expires_at = now() - interval '1 minute' "
                "WHERE user_id = :u"
            ),
            {"u": user_row(email).id},
        )
    response = client.post("/api/auth/verify-email", json={"token": mail.last_token()})
    assert response.status_code == 400
    assert user_row(email).email_verified_at is None


@pytest.mark.db
def test_resend_supersedes_the_earlier_link(mail):
    client, email = new_client(), new_email()
    register(client, email)
    old = mail.last_token()
    assert client.post("/api/auth/resend-verification", json={"email": email}).status_code == 202
    new = mail.last_token()
    assert new != old
    assert client.post("/api/auth/verify-email", json={"token": old}).status_code == 400
    assert client.post("/api/auth/verify-email", json={"token": new}).status_code == 200


@pytest.mark.db
def test_resend_for_an_unknown_email_looks_the_same(mail):
    client = new_client()
    response = client.post("/api/auth/resend-verification", json={"email": new_email()})
    assert (response.status_code, response.json()) == (202, {"status": "accepted"})
    assert mail.sent == []


@pytest.mark.db
def test_failed_delivery_is_retryable_and_leaves_the_account_unverified():
    failing = FakeMail(fail=True)
    app.dependency_overrides[get_mail_sender] = lambda: failing
    try:
        client, email = new_client(), new_email()
        assert register(client, email).status_code == 202
        assert user_row(email).email_verified_at is None
        failing.fail = False
        client.post("/api/auth/resend-verification", json={"email": email})
        assert failing.sent[0][0] == email
    finally:
        app.dependency_overrides.clear()


@pytest.mark.db
def test_registration_is_rate_limited(mail):
    client = new_client()
    statuses = [register(client, new_email()).status_code for _ in range(11)]
    assert statuses[:10] == [202] * 10
    assert statuses[10] == 429


# Sign-in and sessions


@pytest.mark.db
def test_wrong_password_and_unknown_email_are_indistinguishable(mail):
    client, email = verified_client(mail)
    wrong = sign_in(new_client(), email, "not the right password")
    unknown = sign_in(new_client(), new_email())
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.content == unknown.content


@pytest.mark.db
def test_session_token_is_stored_only_as_a_hash(mail):
    client, email = verified_client(mail)
    raw = client.cookies.get(sessions.session_cookie_name())
    with global_tx() as conn:
        stored = conn.execute(
            text("SELECT token_hash FROM auth_sessions WHERE user_id = :u"),
            {"u": user_row(email).id},
        ).scalar_one()
    assert stored == hash_token(raw) != raw


@pytest.mark.db
def test_unverified_user_can_sign_in_but_not_use_tenant_routes(mail):
    client, email = new_client(), new_email()
    register(client, email)
    assert sign_in(client, email).json()["user"]["email_verified"] is False
    response = client.post("/api/tenants", json={"name": "Acme"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


@pytest.mark.db
def test_verification_applies_to_an_existing_session(mail):
    client, email = new_client(), new_email()
    register(client, email)
    sign_in(client, email)
    client.post("/api/auth/verify-email", json={"token": mail.last_token()})
    assert client.post("/api/tenants", json={"name": "Acme"}).status_code == 201


@pytest.mark.db
def test_sign_out_revokes_the_session_on_the_server(mail):
    client, _ = verified_client(mail)
    raw = client.cookies.get(sessions.session_cookie_name())
    assert client.post("/api/auth/sign-out").status_code == 200
    client.cookies.set(sessions.session_cookie_name(), raw)
    assert client.get("/api/me/tenants").status_code == 401


@pytest.mark.db
def test_disabled_account_loses_its_session_on_the_next_request(mail):
    client, email = verified_client(mail)
    assert client.get("/api/me/tenants").status_code == 200
    with global_tx() as conn:
        conn.execute(text("UPDATE users SET status = 'DISABLED' WHERE email = :e"), {"e": email})
    assert client.get("/api/me/tenants").status_code == 401


@pytest.mark.db
def test_disabled_account_cannot_sign_in(mail):
    client, email = verified_client(mail)
    with global_tx() as conn:
        conn.execute(text("UPDATE users SET status = 'DISABLED' WHERE email = :e"), {"e": email})
    assert sign_in(new_client(), email).status_code == 401


@pytest.mark.db
def test_expired_session_is_refused(mail):
    client, email = verified_client(mail)
    with global_tx() as conn:
        conn.execute(
            text(
                "UPDATE auth_sessions SET expires_at = now() - interval '1 minute' "
                "WHERE user_id = :u"
            ),
            {"u": user_row(email).id},
        )
    assert client.get("/api/me/tenants").status_code == 401


@pytest.mark.db
def test_made_up_cookie_is_refused(mail):
    client = new_client()
    client.cookies.set(sessions.session_cookie_name(), "made-up")
    assert client.get("/api/me/tenants").status_code == 401


@pytest.mark.db
def test_cross_origin_write_is_refused_but_the_app_origin_is_not(mail):
    client, _ = verified_client(mail)
    evil = {"Origin": "https://evil.example.com"}
    assert client.post("/api/tenants", json={"name": "A"}, headers=evil).status_code == 401
    assert client.get("/api/me/tenants", headers=evil).status_code == 200
    own = {"Origin": sessions.allowed_origin()}
    assert client.post("/api/tenants", json={"name": "A"}, headers=own).status_code == 201


# Tenant creation


def scoped_scalar(tenant_id: UUID, sql: str, **params):
    with scoped_tx(tenant_id) as conn:
        return conn.execute(text(sql), {"t": tenant_id, **params}).scalar()


@pytest.mark.db
def test_creator_becomes_the_sole_active_owner(mail):
    client, email = verified_client(mail)
    tenant = create_tenant(client)
    user_id = user_row(email).id
    assert scoped_scalar(tenant, "SELECT owner_user_id FROM tenants WHERE id = :t") == user_id
    assert (
        scoped_scalar(
            tenant, "SELECT status FROM tenant_members WHERE tenant_id = :t AND user_id = :u",
            u=user_id,
        )
        == "ACTIVE"
    )


@pytest.mark.db
def test_owner_permissions_derive_from_the_pointer_and_are_not_a_grant(mail):
    client, email = verified_client(mail)
    tenant = create_tenant(client)
    held = {
        p["key"]
        for p in client.get("/api/me/permissions", params={"tenant_id": tenant}).json()[
            "permissions"
        ]
    }
    assert {"tenant.ownership.transfer", "execution_mode.manage", "member.invite"} <= held
    assert not {"playbook.request", "playbook.approve"} & held
    assert (
        scoped_scalar(
            tenant, "SELECT count(*) FROM member_grants WHERE user_id = :u", u=user_row(email).id
        )
        == 0
    )


@pytest.mark.db
def test_tenant_creation_is_audited(mail):
    client, email = verified_client(mail)
    tenant = create_tenant(client)
    with scoped_tx(tenant) as conn:
        row = conn.execute(
            text("SELECT principal_id, action FROM audit_logs WHERE action = 'tenant.created'")
        ).one()
    assert tuple(row) == (user_row(email).id, "tenant.created")


@pytest.mark.db
def test_service_principals_follow_their_authority_boundaries(mail):
    client, _ = verified_client(mail)
    tenant = create_tenant(client)
    with scoped_tx(tenant) as conn:
        rows = conn.execute(
            text(
                """
                SELECT p.kind, count(g.id) AS grants
                FROM service_principals p
                LEFT JOIN member_grants g ON g.service_principal_id = p.id
                GROUP BY p.kind
                """
            )
        ).all()
    assert {r.kind: r.grants for r in rows} == {"agent": 1, "worker": 0}


@pytest.mark.parametrize(
    "field", [("tenant_id", str(uuid4())), ("id", str(uuid4())), ("owner_user_id", str(uuid4()))]
)
@pytest.mark.db
def test_client_cannot_choose_tenant_or_owner(mail, field):
    client, _ = verified_client(mail)
    response = client.post("/api/tenants", json={"name": "A", field[0]: field[1]})
    assert response.status_code == 422


@pytest.mark.db
def test_failed_creation_leaves_nothing_behind(mail, monkeypatch):
    client, _ = verified_client(mail)

    def explode(conn, tenant_id):
        raise ApiError(500, "BOOM", "boom")

    monkeypatch.setattr(creation, "provision_service_principals", explode)
    assert client.post("/api/tenants", json={"name": "A"}).status_code == 500
    assert client.get("/api/me/tenants").json() == {"tenants": []}


# Listing and isolation


@pytest.mark.db
def test_my_tenants_lists_only_the_callers(mail):
    first, _ = verified_client(mail)
    second, _ = verified_client(mail)
    mine = create_tenant(first, "Mine")
    create_tenant(second, "Theirs")
    assert first.get("/api/me/tenants").json() == {"tenants": [{"id": str(mine), "name": "Mine"}]}


@pytest.mark.db
def test_my_tenants_omits_a_suspended_membership(mail):
    client, email = verified_client(mail)
    owner, _ = verified_client(mail)
    tenant = create_tenant(owner)
    user_id = user_row(email).id
    with scoped_tx(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO tenant_members (tenant_id, user_id, status) "
                "VALUES (:t, :u, 'ACTIVE')"
            ),
            {"t": tenant, "u": user_id},
        )
    assert len(client.get("/api/me/tenants").json()["tenants"]) == 1
    with scoped_tx(tenant) as conn:
        conn.execute(
            text("UPDATE tenant_members SET status = 'SUSPENDED' WHERE user_id = :u"),
            {"u": user_id},
        )
    assert client.get("/api/me/tenants").json() == {"tenants": []}


@pytest.mark.db
def test_another_users_tenant_is_a_plain_404(mail):
    first, _ = verified_client(mail)
    second, _ = verified_client(mail)
    tenant = create_tenant(first)
    theirs = second.get("/api/me/permissions", params={"tenant_id": tenant})
    missing = second.get("/api/me/permissions", params={"tenant_id": uuid4()})
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content


@pytest.mark.db
def test_a_session_resolves_as_a_human_principal(mail):
    client, email = verified_client(mail)
    tenant = create_tenant(client)
    principal = sessions.session_principal(client.cookies.get(sessions.session_cookie_name()))
    assert principal == Principal(user_row(email).id, PrincipalKind.HUMAN, email_verified=True)
    assert PostgresAccessGateway(get_catalogue()).resolve(principal, tenant) is not None
