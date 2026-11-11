"""Version-pinned domain runs (task 3.4, spec 6.3, AC-REC-08)."""

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.domain import views
from tests.domain_fixture import FinanceTenant
from tests.support import grant_set
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

CAPTURE = {"id": "CAP-1", "invoice_id": "ORD-1", "status": "COMPLETED",
           "amount": {"value": "100.00", "currency_code": "USD"}}


def test_a_run_pins_every_version_it_reads():
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)
    run = member.run()
    assert run["status"] == "SUCCEEDED"
    assert (run["pack_namespace"], run["pack_version"]) == ("finops", "0.2.0")
    assert run["ontology_version"] == 1
    assert len(run["ontology_content_hash"]) == 64
    assert run["policy_versions"] == {"finops.exact_id": "1"}
    assert run["rule_version"].startswith("finops@0.2.0/")
    assert len(run["mapping_versions"]) > 0 and set(run["mapping_versions"].values()) == {1}
    assert len(run["authority_versions"]) > 0
    assert run["source_snapshots"] == {
        str(member.sources["paypal"]): {"records": 1,
                                        "hash": run["source_snapshots"][
                                            str(member.sources["paypal"])]["hash"]}
    }
    assert run["finished_at"] is not None


def test_identical_inputs_give_an_identical_inputs_hash_and_new_records_change_it():
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)
    first, second = member.run(), member.run()
    assert first["inputs_hash"] == second["inputs_hash"]
    member.ingest("paypal", "captures", CAPTURE | {"id": "CAP-2"})
    assert member.run()["inputs_hash"] != first["inputs_hash"]


def test_runs_are_listed_and_read_back():
    member = FinanceTenant()
    run = member.run()
    assert [item["id"] for item in member.get("/api/domain-runs")] == [run["id"]]
    assert member.get(f"/api/domain-runs/{run['id']}")["inputs_hash"] == run["inputs_hash"]


def operator() -> Member:
    member = Member()
    grant_set(member.tenant, member.principal.id, "domain_operator")
    return member


def test_a_run_needs_an_active_pack():
    member = operator()
    response = member.request("POST", "/api/domain-runs", json={"namespace": "finops"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PACK_NOT_ACTIVE"


def test_a_run_needs_a_published_ontology():
    member = operator()
    member.activate("finops")
    response = member.request("POST", "/api/domain-runs")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_PUBLISHED_ONTOLOGY"


@pytest.mark.parametrize("field", ["tenant_id", "requested_by", "ontology_version", "status"])
def test_the_body_cannot_carry_server_derived_fields(field):
    member = FinanceTenant()
    response = member.request("POST", "/api/domain-runs", json={field: "x"})
    assert response.status_code == 422


def test_a_failed_run_is_recorded_and_leaves_nothing_behind(monkeypatch):
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "resolve_views", explode)
    run = member.run()
    assert run["status"] == "FAILED"
    assert run["failure"] == "RuntimeError"
    assert member.entities("finops:Capture") == []
    with scoped_tx(member.tenant) as conn:
        action = conn.execute(
            text("SELECT action FROM audit_logs WHERE action LIKE 'domain_run.%'")
        ).scalar_one()
    assert action == "domain_run.failed"


def test_another_tenants_run_is_404():
    member = FinanceTenant()
    run = member.run()
    other = operator()
    assert other.request("GET", f"/api/domain-runs/{run['id']}").status_code == 404
    assert other.request("GET", "/api/domain-runs").json() == []
