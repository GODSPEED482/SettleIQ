"""The Worker runs one execution under its grant (task 5.6, spec 4.4.1, 9.3-9.5, POL-09).

AC-MERCH-01/02 end to end, through the routes and the Worker app; AC-AUTHZ-08 (single-use,
bound, expiring grant), AC-AUTHZ-15 (SHADOW), AC-AUTHZ-17 (no human reaches the Worker),
AC-PLY-06 (readback, never API success) and AC-PLY-10 (durable attempts). The merchant is a
scratch schema set up by the shipped merchant_setup.sql (test_internal_merchant.py).
"""

import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.sessions import create_session, session_cookie_name
from app.authz.catalogue import get_catalogue
from app.db import global_tx, scoped_tx
from app.errors import ApiError
from app.execution import lifecycle, merchant
from app.playbooks import gateway
from app.worker_api import create_worker_app
from tests.support import grant_set
from tests.test_internal_merchant import SECRET, Merchant
from tests.test_paypal_mappings import PayPalTenant
from tests.test_playbook_gateway import MERCHANT, Person, approve, case_status, create, put_mode
from tests.test_playbook_gateway import status_case as new_case
from tests.test_sources import create_ok

pytestmark = pytest.mark.db

REPO = Path(__file__).resolve().parents[2]
CORRECT = MERCHANT["action_capability"]
WORKER = create_worker_app()


@pytest.fixture(scope="module")
def merchant_db():
    db = Merchant()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SOURCE_SECRET_TEST_MERCHANT_WRITE", db.role_url)
        yield db
    db.drop()


@pytest.fixture(scope="module")
def tenant(merchant_db) -> PayPalTenant:
    """A Finance tenant with its PayPal and merchant read sources and one internal-merchant
    write source bound to merchant M-A."""
    tenant = PayPalTenant()
    grant_set(tenant.tenant, tenant.principal.id, "integration_admin")
    create_ok(tenant, connector_id="internal-merchant", display_name="Merchant writes",
              config={"merchant_id": "M-A"}, secret_reference=SECRET)
    return tenant


@pytest.fixture(scope="module")
def approver(tenant) -> Person:
    return Person(tenant, "approver")


@pytest.fixture(autouse=True)
def granted(monkeypatch) -> list[gateway.ExecutionDispatch]:
    sent: list[gateway.ExecutionDispatch] = []
    monkeypatch.setattr(gateway, "dispatch", sent.append)
    return sent


@pytest.fixture
def live(tenant):
    """The merchant scope LIVE, switched by the tenant."""
    assert put_mode(tenant, MERCHANT, "LIVE").status_code == 200
    yield tenant
    assert put_mode(tenant, MERCHANT, "SHADOW").status_code == 200


def add_payment(db: Merchant, payment_id: str, status: str = "PENDING") -> None:
    with db.engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {db.schema}.payments VALUES (:p, 'M-A', :s, 100, 'USD', :o, NULL)"),
            {"p": payment_id, "s": status, "o": f"ORD-{payment_id}"},
        )


def set_payment(db: Merchant, payment_id: str, status: str) -> None:
    with db.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {db.schema}.payments SET status = :s "
                 "WHERE merchant_id = 'M-A' AND payment_id = :p"),
            {"p": payment_id, "s": status},
        )


def payment_status(db: Merchant, payment_id: str) -> list[str]:
    with db.engine.connect() as conn:
        return list(conn.execute(
            text(f"SELECT status FROM {db.schema}.payments "
                 "WHERE merchant_id = 'M-A' AND payment_id = :p"),
            {"p": payment_id},
        ).scalars())


def granted_execution(tenant, approver, db, granted) -> tuple[UUID, str, gateway.ExecutionDispatch]:
    """A PB-001 request on a fresh case, approved in LIVE, with its grant handed over."""
    case_id = new_case(tenant)
    request = create(tenant, case_id).json()
    payment_id = request["target"]["payment_id"]
    add_payment(db, payment_id)
    body = approve(approver, request["id"]).json()
    assert body["execution"]["status"] == "GRANT_ISSUED", body
    [item] = [g for g in granted if str(g.execution_id) == body["execution"]["id"]]
    return case_id, payment_id, item


def run(item: gateway.ExecutionDispatch, *, token: str | None = None,
        tenant: UUID | None = None, execution: UUID | None = None, **headers):
    return TestClient(WORKER).post(
        f"/internal/executions/{execution or item.execution_id}/run",
        headers={"Authorization": f"Bearer {token or item.token}",
                 "X-Tenant-Id": str(tenant or item.tenant_id), **headers},
    )


def attempts(tenant: PayPalTenant, execution_id: UUID) -> list:
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(
            text("SELECT * FROM execution_action_attempts WHERE execution_id = :id "
                 "ORDER BY sequence"),
            {"id": execution_id},
        ).all()


def grant_row(tenant: PayPalTenant, grant_id: UUID):
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(text("SELECT * FROM execution_grants WHERE id = :id"),
                            {"id": grant_id}).one()


def execution_status(tenant: PayPalTenant, execution_id: UUID) -> str:
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(text("SELECT status FROM executions WHERE id = :id"),
                            {"id": execution_id}).scalar_one()


# The machine


def test_python_and_database_allow_the_same_execution_transitions():
    with global_tx() as conn:
        source = conn.execute(
            text("SELECT pg_get_functiondef('guard_execution'::regproc)")
        ).scalar_one()
    pairs = set(re.findall(r"\('([A-Z_]+)', '([A-Z_]+)'\)", source))
    assert pairs == set(lifecycle.EXECUTION_TRANSITIONS)


# AC-MERCH-01, AC-PLY-10, AC-PLY-06


def test_a_live_grant_corrects_one_payment_and_resolves_after_readback(
        live, approver, merchant_db, granted):
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    assert case_status(live, case_id) == "ACTION_PENDING"

    response = run(item)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert payment_status(merchant_db, payment_id) == ["COMPLETED"]
    assert case_status(live, case_id) == "RESOLVED"
    assert grant_row(live, item.grant_id).consumed_at is not None

    rows = attempts(live, item.execution_id)
    assert [(r.sequence, r.status) for r in rows] == [
        (1, "STARTED"), (2, "SUCCEEDED"), (3, "VERIFIED")]
    assert {r.idempotency_key for r in rows} == {body["idempotency_key"]}
    assert all(r.tenant_id == live.tenant and r.connector_id == "internal-merchant"
               for r in rows)
    assert [r.action_capability for r in rows] == [CORRECT, CORRECT, "merchant.payment.read"]
    assert rows[0].finished_at is None and rows[2].finished_at is not None
    assert rows[1].response_evidence["outcome"] == "APPLIED"
    assert rows[2].response_evidence["payment"]["status"] == "COMPLETED"
    assert body["result_ref"]["readback"] == "merchant.payment.read"

    # A human reads the same execution, with its attempts and readback (execution.read).
    read = live.request("GET", f"/api/executions/{item.execution_id}")
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "SUCCEEDED"
    assert [a["status"] for a in read.json()["attempts"]] == ["STARTED", "SUCCEEDED", "VERIFIED"]
    assert read.json()["approval"]["decision"] == "APPROVED"


def test_the_readback_alone_resolves_the_case(live, approver, merchant_db, granted,
                                              monkeypatch):
    """AC-PLY-06: an APPLIED correction whose readback disagrees is never resolution."""
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    real = merchant.read_payment

    def stale_read(tenant_id, source_id, params):
        payment = real(tenant_id, source_id, params)
        return type(payment)(**{**payment.__dict__, "status": "PENDING"})

    monkeypatch.setattr(merchant, "read_payment", stale_read)
    body = run(item).json()
    assert body["status"] == "MANUAL_REVIEW"
    assert body["result_ref"]["readback"] == "merchant.payment.read"
    assert case_status(live, case_id) == "HUMAN_REVIEW"
    assert [r.status for r in attempts(live, item.execution_id)] == [
        "STARTED", "SUCCEEDED", "FAILED"]


# AC-MERCH-02


def test_stale_state_fails_closed_and_returns_the_case_to_human_review(
        live, approver, merchant_db, granted):
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    set_payment(merchant_db, payment_id, "COMPLETED")  # changed after approval

    body = run(item).json()
    assert body["status"] == "FAILED"
    assert case_status(live, case_id) == "HUMAN_REVIEW"
    rows = attempts(live, item.execution_id)
    assert [(r.status, r.error_class) for r in rows] == [
        ("STARTED", None), ("FAILED", "STALE_STATE")]
    assert payment_status(merchant_db, payment_id) == ["COMPLETED"]


def test_an_unreachable_merchant_is_retried_under_the_same_key(
        live, approver, merchant_db, granted, monkeypatch):
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    real, failures, keys = merchant.correct_payment_status, [1, 2], []

    def flaky(tenant_id, source_id, params, key):
        keys.append(key)
        if failures:
            failures.pop()
            raise ApiError(409, "MERCHANT_UNAVAILABLE", "down")
        return real(tenant_id, source_id, params, key)

    monkeypatch.setattr(merchant, "correct_payment_status", flaky)
    body = run(item).json()
    assert body["status"] == "SUCCEEDED"
    assert len(set(keys)) == 1 and len(keys) == 3
    assert [r.status for r in attempts(live, item.execution_id)] == [
        "STARTED", "OUTCOME_UNKNOWN", "STARTED", "OUTCOME_UNKNOWN", "STARTED", "SUCCEEDED",
        "VERIFIED"]
    assert payment_status(merchant_db, payment_id) == ["COMPLETED"]


def test_an_outcome_left_unknown_is_settled_by_readback_only(
        live, approver, merchant_db, granted, monkeypatch):
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)

    def down(*_):
        raise ApiError(409, "MERCHANT_UNAVAILABLE", "down")

    monkeypatch.setattr(merchant, "correct_payment_status", down)
    body = run(item).json()
    # Nothing changed, so the readback cannot confirm COMPLETED: manual review, never resolved.
    assert body["status"] == "MANUAL_REVIEW"
    assert payment_status(merchant_db, payment_id) == ["PENDING"]
    assert case_status(live, case_id) == "HUMAN_REVIEW"


# AC-AUTHZ-08


def test_a_grant_runs_once(live, approver, merchant_db, granted):
    _, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    assert run(item).status_code == 200
    again = run(item)
    assert again.status_code == 404
    assert [r.status for r in attempts(live, item.execution_id)].count("STARTED") == 1


def test_concurrent_runs_consume_the_grant_once(live, approver, merchant_db, granted):
    _, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    results: list[int] = []
    threads = [threading.Thread(target=lambda: results.append(run(item).status_code))
               for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results)[0] == 200 and results.count(200) == 1
    assert [r.status for r in attempts(live, item.execution_id)].count("STARTED") == 1
    assert payment_status(merchant_db, payment_id) == ["COMPLETED"]


def test_a_grant_is_bound_to_its_execution_and_tenant(live, approver, merchant_db, granted):
    _, _, first = granted_execution(live, approver, merchant_db, granted)
    _, _, second = granted_execution(live, approver, merchant_db, granted)
    assert run(first, execution=second.execution_id).status_code == 401
    assert run(first, tenant=uuid4()).status_code == 401
    assert run(first, token="not-a-grant").status_code == 401
    for item in (first, second):
        assert attempts(live, item.execution_id) == []
        assert grant_row(live, item.grant_id).consumed_at is None


def test_an_expired_grant_runs_nothing(live, approver, merchant_db, granted, monkeypatch):
    monkeypatch.setattr(gateway, "GRANT_TTL", timedelta(seconds=1))
    _, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    time.sleep(1.2)
    assert run(item).status_code == 404
    assert attempts(live, item.execution_id) == []
    assert payment_status(merchant_db, payment_id) == ["PENDING"]


# AC-AUTHZ-15, AC-AUTHZ-16: the Worker checks the exact scope again


def test_shadow_after_the_grant_blocks_the_worker(live, approver, merchant_db, granted):
    case_id, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    assert put_mode(live, MERCHANT, "SHADOW").status_code == 200

    body = run(item).json()
    assert body["status"] == "POLICY_BLOCKED"
    assert "POL-06" in body["reason"]
    assert attempts(live, item.execution_id) == []
    assert grant_row(live, item.grant_id).consumed_at is None
    assert payment_status(merchant_db, payment_id) == ["PENDING"]
    assert case_status(live, case_id) == "HUMAN_REVIEW"


# AC-AUTHZ-06: the Worker's keys are its alone, and come only from a grant


def test_the_grant_keys_are_worker_only():
    catalogue = get_catalogue()
    for key in gateway.GRANT_PERMISSIONS:
        assert catalogue.get(key).principal_types == ("worker",), key
    # So a human holding one is refused at the gate before access is even resolved.
    assert "human" not in catalogue.get("playbook.execute").principal_types


# AC-AUTHZ-17: no human reaches the Worker


def test_a_human_session_is_refused_before_any_side_effect(live, approver, merchant_db,
                                                           granted):
    _, payment_id, item = granted_execution(live, approver, merchant_db, granted)
    cookie = {session_cookie_name(): create_session(live.principal.id)}
    response = TestClient(WORKER, cookies=cookie).post(
        f"/internal/executions/{item.execution_id}/run",
        headers={"X-Tenant-Id": str(live.tenant)},
    )
    assert response.status_code == 401
    assert attempts(live, item.execution_id) == []
    assert grant_row(live, item.grant_id).consumed_at is None
    assert payment_status(merchant_db, payment_id) == ["PENDING"]


@pytest.mark.parametrize("file", ["docker-compose.yml", "deploy/compose.yaml"])
def test_the_worker_publishes_no_port_and_shares_no_network_with_the_browser(file):
    services = yaml.safe_load((REPO / file).read_text(encoding="utf-8"))["services"]
    worker = services["worker"]
    assert "ports" not in worker
    assert "edge" not in worker["networks"]


# The hand-over


def test_the_grant_is_handed_to_the_worker_as_a_bearer_token(monkeypatch):
    sent = []
    monkeypatch.setenv("WORKER_URL", "http://worker:8001/")
    monkeypatch.setattr(httpx, "post",
                        lambda url, **kw: sent.append((url, kw)) or httpx.Response(200))
    item = gateway.ExecutionDispatch(uuid4(), uuid4(), uuid4(), "tok", None)
    gateway._to_worker(item)
    [(url, kwargs)] = sent
    assert url == f"http://worker:8001/internal/executions/{item.execution_id}/run"
    assert kwargs["headers"] == {"Authorization": "Bearer tok",
                                 "X-Tenant-Id": str(item.tenant_id)}
