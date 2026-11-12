"""PB-002 refund execution by the Worker (task 5.15, spec 9.4, 10.3, 10.6).

AC-PP-08: the refund needs the duplicate proof and an independent approval, and the case
resolves only when readback shows the refund COMPLETED against the exact duplicate capture, with
the original untouched. An ambiguous outcome is settled by readback and repeated only under the
same PayPal-Request-Id, never a new one (no blind retry). PayPal is a stateful fake behind
httpx.MockTransport; everything else is real, as in test_worker.py.
"""

import json
from itertools import count
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.connectors.paypal import client as paypal_client_module
from app.connectors.paypal.client import PayPalClient
from app.connectors.paypal.refunds import RefundOutcomeUnknown, create_refund
from app.execution import refunds
from app.execution.executors import Refused
from app.playbooks import gateway
from app.worker_api import create_worker_app
from tests.test_pb002 import PayPalFinanceTenant
from tests.test_playbook_gateway import REFUND, Person, approve, case_status, create, put_mode

pytestmark = pytest.mark.db

REPO = Path(__file__).resolve().parents[2]
WORKER = create_worker_app()
SEQUENCE = count(1)


class FakePayPal:
    """Captures and refunds as PayPal Sandbox keeps them, with PayPal-Request-Id replay."""

    def __init__(self) -> None:
        self.captures: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self.by_request_id: dict[str, str] = {}
        self.refund_posts: list[str] = []
        self.calls: list[str] = []
        self.refund_status = "COMPLETED"
        # How many refund POSTs answer 500, and whether the first of them still makes the refund.
        self.fail_posts = 0
        self.refund_despite_failure = False

    def capture(self, capture_id: str, status: str = "COMPLETED", value: str = "50.00") -> None:
        self.captures[capture_id] = {"id": capture_id, "status": status,
                                     "amount": {"value": value, "currency_code": "USD"}}

    def _refund(self, capture_id: str, request_id: str, body: dict) -> dict:
        if request_id in self.by_request_id:
            return self.refunds[self.by_request_id[request_id]]
        refund_id = f"REF-{uuid4().hex[:8]}"
        self.refunds[refund_id] = {
            "id": refund_id, "status": self.refund_status, "amount": body["amount"],
            "links": [{"rel": "self", "href": f"https://api/v2/payments/refunds/{refund_id}"},
                      {"rel": "up", "href": f"https://api/v2/payments/captures/{capture_id}"}],
        }
        self.by_request_id[request_id] = refund_id
        if self.refund_status == "COMPLETED":
            self.captures[capture_id]["status"] = "REFUNDED"
        return self.refunds[refund_id]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.method == "GET" and path.startswith("/v2/payments/captures/"):
            capture = self.captures.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=capture) if capture else httpx.Response(404)
        if request.method == "GET" and path.startswith("/v2/payments/refunds/"):
            refund = self.refunds.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=refund) if refund else httpx.Response(404)
        if request.method == "POST" and path.endswith("/refund"):
            capture_id = path.split("/")[-2]
            request_id = request.headers["paypal-request-id"]
            self.refund_posts.append(request_id)
            if self.fail_posts:
                self.fail_posts -= 1
                if self.refund_despite_failure:
                    self.refund_despite_failure = False
                    self._refund(capture_id, request_id, json.loads(request.content))
                return httpx.Response(500)
            return httpx.Response(
                201, json=self._refund(capture_id, request_id, json.loads(request.content)))
        return httpx.Response(404)


@pytest.fixture(scope="module")
def tenant() -> PayPalFinanceTenant:
    return PayPalFinanceTenant()


@pytest.fixture(scope="module")
def approver(tenant) -> Person:
    return Person(tenant, "approver")


@pytest.fixture
def paypal(monkeypatch) -> FakePayPal:
    fake = FakePayPal()
    client = PayPalClient("id", "secret", transport=httpx.MockTransport(fake.handle))
    monkeypatch.setattr(refunds, "paypal_client", lambda tenant_id, source_id: client)
    monkeypatch.setattr(paypal_client_module.time, "sleep", lambda _: None)
    return fake


@pytest.fixture(autouse=True)
def granted(monkeypatch) -> list[gateway.ExecutionDispatch]:
    sent: list[gateway.ExecutionDispatch] = []
    monkeypatch.setattr(gateway, "dispatch", sent.append)
    return sent


@pytest.fixture
def live(tenant):
    """The refunds.create scope LIVE, switched by the tenant."""
    assert put_mode(tenant, REFUND, "LIVE").status_code == 200
    yield tenant
    assert put_mode(tenant, REFUND, "SHADOW").status_code == 200


def duplicate(tenant: PayPalFinanceTenant, paypal: FakePayPal) -> tuple[UUID, str, str]:
    """A DUPLICATE_CAPTURE case in HUMAN_REVIEW, known to the fake PayPal as well."""
    n = next(SEQUENCE)
    order, original, later = f"ORD-RF-{n}", f"CAP-RF-{n}-A", f"CAP-RF-{n}-B"
    tenant.ingest("merchant", "orders", {"order_id": order})
    tenant.capture(original, order, "2026-03-01T10:00:00Z")
    tenant.capture(later, order, "2026-03-01T10:05:00Z")
    tenant.run()
    case_id = UUID(tenant.duplicate_case(order)["id"])
    for status in ("INVESTIGATING", "HUMAN_REVIEW"):
        response = tenant.request("PATCH", f"/api/domain-cases/{case_id}",
                                  json={"status": status})
        assert response.status_code == 200, response.text
    paypal.capture(original)
    paypal.capture(later)
    return case_id, original, later


def granted_refund(tenant, approver, paypal, granted):
    case_id, original, later = duplicate(tenant, paypal)
    request = create(tenant, case_id, playbook="finops:PB-002")
    assert request.status_code == 201, request.text
    body = request.json()
    assert body["status"] == "NEEDS_APPROVAL"
    assert body["target"]["capture_id"] == later
    assert body["decisions"][0]["conditions"]["proof"]["original"]["capture_id"] == original
    approved = approve(approver, body["id"]).json()
    assert approved["execution"]["status"] == "GRANT_ISSUED", approved
    [item] = [g for g in granted if str(g.execution_id) == approved["execution"]["id"]]
    return case_id, original, later, item


def run(item: gateway.ExecutionDispatch):
    response = TestClient(WORKER).post(
        f"/internal/executions/{item.execution_id}/run",
        headers={"Authorization": f"Bearer {item.token}", "X-Tenant-Id": str(item.tenant_id)},
    )
    assert response.status_code == 200, response.text
    return response.json()


# AC-PP-08


def test_an_approved_duplicate_is_refunded_and_resolved_only_after_readback(
        live, approver, paypal, granted):
    case_id, original, later, item = granted_refund(live, approver, paypal, granted)
    body = run(item)

    assert body["status"] == "SUCCEEDED", body
    assert case_status(live, case_id) == "RESOLVED"
    [refund] = paypal.refunds.values()
    assert refund["links"][1]["href"].endswith(f"/captures/{later}")
    assert paypal.captures[later]["status"] == "REFUNDED"
    assert paypal.captures[original]["status"] == "COMPLETED"
    assert paypal.refund_posts == [body["idempotency_key"]]
    assert [a["status"] for a in body["attempts"]] == ["STARTED", "SUCCEEDED", "VERIFIED"]
    started = body["attempts"][0]["request_evidence"]["prepared"]
    assert started["duplicate"]["status"] == "COMPLETED"
    assert started["original"]["id"] == original
    verified = body["result_ref"]["evidence"]
    assert verified["refund"]["status"] == "COMPLETED" and verified["failures"] == []
    # The readback is a separate read of the refund and both captures, after the action.
    assert paypal.calls[-3:] == [f"GET /v2/payments/refunds/{refund['id']}",
                                 f"GET /v2/payments/captures/{later}",
                                 f"GET /v2/payments/captures/{original}"]


def test_a_pending_refund_is_never_resolution(live, approver, paypal, granted):
    paypal.refund_status = "PENDING"
    case_id, _, _, item = granted_refund(live, approver, paypal, granted)
    body = run(item)
    assert body["status"] == "MANUAL_REVIEW"
    assert case_status(live, case_id) == "HUMAN_REVIEW"
    assert "refund is PENDING, not COMPLETED" in body["result_ref"]["evidence"]["failures"]


# Before the refund: nothing is sent unless the capture is still unrefunded


def test_a_capture_refunded_meanwhile_is_not_refunded_again(live, approver, paypal, granted):
    case_id, _, later, item = granted_refund(live, approver, paypal, granted)
    paypal.captures[later]["status"] = "REFUNDED"  # refunded outside SettleIQ after approval
    body = run(item)
    assert body["status"] == "FAILED"
    assert body["attempts"][-1]["error_class"] == "CAPTURE_NOT_REFUNDABLE"
    assert paypal.refund_posts == []
    assert case_status(live, case_id) == "HUMAN_REVIEW"


def test_an_original_that_changed_stops_the_refund(live, approver, paypal, granted):
    _, original, _, item = granted_refund(live, approver, paypal, granted)
    paypal.captures[original]["status"] = "REFUNDED"
    body = run(item)
    assert body["status"] == "FAILED"
    assert body["attempts"][-1]["error_class"] == "ORIGINAL_CAPTURE_CHANGED"
    assert paypal.refund_posts == []


# Ambiguous outcomes: readback, and the same PayPal-Request-Id only


def test_an_ambiguous_refund_is_settled_under_the_same_request_id(
        live, approver, paypal, granted):
    paypal.fail_posts, paypal.refund_despite_failure = 3, True  # made, but every answer lost
    case_id, _, later, item = granted_refund(live, approver, paypal, granted)
    body = run(item)

    assert body["status"] == "SUCCEEDED", body
    assert len(paypal.refunds) == 1
    assert set(paypal.refund_posts) == {body["idempotency_key"]}
    assert len(paypal.refund_posts) == 4  # three lost answers, then one replay
    assert [a["status"] for a in body["attempts"]] == [
        "STARTED", "OUTCOME_UNKNOWN", "SUCCEEDED", "VERIFIED"]
    assert case_status(live, case_id) == "RESOLVED"


def test_an_ambiguous_refund_with_no_refund_visible_goes_to_manual_review(
        live, approver, paypal, granted):
    paypal.fail_posts = 3  # nothing was made, but the answer never says so
    case_id, _, later, item = granted_refund(live, approver, paypal, granted)
    body = run(item)

    assert body["status"] == "MANUAL_REVIEW"
    assert paypal.refunds == {}
    assert set(paypal.refund_posts) == {body["idempotency_key"]}
    assert len(paypal.refund_posts) == 3  # the client's own retries, then no blind retry
    assert body["attempts"][-1]["error_class"] == "NO_REFUND_OBSERVED"
    assert case_status(live, case_id) == "HUMAN_REVIEW"


def test_an_unanswered_refund_is_unknown_even_if_the_capture_reads_refunded(paypal):
    """A capture refunded by anyone reads REFUNDED, so that alone never proves this refund."""
    paypal.capture("CAP-X", status="REFUNDED")
    paypal.fail_posts = 3
    client = refunds.paypal_client(uuid4(), uuid4())
    with pytest.raises(RefundOutcomeUnknown):
        create_refund(client, capture_id="CAP-X", amount="50.00", currency="USD",
                      idempotency_key="k-1")


# SHADOW, tenant isolation and key custody


def test_shadow_after_the_grant_sends_nothing_to_paypal(live, approver, paypal, granted):
    case_id, _, _, item = granted_refund(live, approver, paypal, granted)
    assert put_mode(live, REFUND, "SHADOW").status_code == 200
    body = run(item)
    assert body["status"] == "POLICY_BLOCKED"
    assert paypal.calls == [] and body["attempts"] == []
    assert case_status(live, case_id) == "HUMAN_REVIEW"


def test_the_client_comes_only_from_the_tenants_own_paypal_source(tenant):
    other = PayPalFinanceTenant()
    with pytest.raises(Refused, match="PAYPAL_SOURCE_UNRESOLVED"):
        refunds._tenant_client(tenant.tenant, other.sources["paypal"])
    with pytest.raises(Refused, match="PAYPAL_SOURCE_UNRESOLVED"):
        refunds._tenant_client(tenant.tenant, tenant.sources["merchant"])


@pytest.mark.parametrize("file", ["docker-compose.yml", "deploy/compose.yaml"])
def test_only_the_api_and_the_worker_hold_the_source_secret_key(file):
    services = yaml.safe_load((REPO / file).read_text(encoding="utf-8"))["services"]
    holders = sorted(n for n, s in services.items()
                     if "SOURCE_SECRET_KEY" in (s.get("environment") or {}))
    assert holders == ["api", "worker"]
