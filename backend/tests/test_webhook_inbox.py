"""Webhook inbox reads and replay for the Sources screen (task 4.7, spec 10.4).

`GET /api/sources/{id}/webhook-events` (source.read) lists one source's deliveries with their
processing state, never their payloads. `POST .../{event_id}/replay` (source.webhook.manage)
reprocesses a FAILED or DEAD_LETTER event under its original provider event ID.
"""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from tests.support import Actor, add_member, clear_overrides, grant_set
from tests.test_ingestion import evidence_root  # noqa: F401
from tests.test_semantics_store import Member
from tests.test_webhook_failures import (
    WEBHOOK_ID,
    client,
    event,
    inbox,
    paypal_source,
    process,
    records_for,
)

pytestmark = pytest.mark.db


@pytest.fixture
def member(evidence_root, paypal_signer):  # noqa: F811
    yield Member("Webhook inbox")
    clear_overrides()  # Actor leaves its sign-in in place


def events_url(source: UUID | str) -> str:
    return f"/api/sources/{source}/webhook-events"


def replay(actor, source: UUID | str, inbox_id: UUID | str, tenant: UUID | None = None):
    path = f"{events_url(source)}/{inbox_id}/replay"
    if isinstance(actor, Actor):
        return actor.request("POST", path, tenant=tenant)
    return actor.request("POST", path)


def force_status(tenant: UUID, inbox_id: UUID, status: str) -> None:
    with scoped_tx(tenant) as conn:
        conn.execute(
            text(
                "UPDATE webhook_inbox SET status = :s, attempts = max_attempts, "
                "last_error = 'boom' WHERE id = :id"
            ),
            {"s": status, "id": inbox_id},
        )


def test_lists_one_sources_deliveries_without_payloads(member, paypal_signer):
    source, other = paypal_source(member), paypal_source(member, webhook_id="WH-OTHER")
    paypal_signer.deliver(client, source, event("EVT-LIST-1"), WEBHOOK_ID)
    paypal_signer.deliver(client, source, event("EVT-LIST-2"), WEBHOOK_ID)
    paypal_signer.deliver(client, other, event("EVT-LIST-3"), "WH-OTHER")

    response = member.request("GET", events_url(source))
    assert response.status_code == 200, response.text
    rows = response.json()
    assert {r["provider_event_id"] for r in rows} == {"EVT-LIST-1", "EVT-LIST-2"}
    assert set(rows[0]) == {
        "id", "provider_event_id", "event_type", "status", "attempts", "max_attempts",
        "last_error", "provider_timestamp", "received_at", "processed_at", "next_attempt_at",
    }
    assert "resource" not in json.dumps(rows)
    assert rows[0]["status"] == "RECEIVED"


def test_another_tenant_sees_not_found(member, paypal_signer):
    source = paypal_source(member)
    outsider = Member("Outsider")
    assert outsider.request("GET", events_url(source)).status_code == 404
    assert member.request("GET", events_url(uuid4())).status_code == 404
    assert member.request("GET", events_url("not-a-uuid")).status_code == 404


def test_replay_reprocesses_a_dead_letter_under_its_event_id(member, paypal_signer):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event("EVT-REPLAY"), WEBHOOK_ID)
    [row] = inbox(member.tenant, "EVT-REPLAY")
    force_status(member.tenant, row.id, "DEAD_LETTER")

    response = replay(member, source, row.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["provider_event_id"]) == (str(row.id), "EVT-REPLAY")
    assert (body["status"], body["attempts"], body["last_error"]) == ("PROCESSED", 1, None)
    assert len(inbox(member.tenant, "EVT-REPLAY")) == 1
    assert records_for(member.tenant, "EVT-REPLAY") == 1
    with scoped_tx(member.tenant) as conn:
        audit = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'source.webhook.replayed'")
        ).scalar_one()
    assert audit["provider_event_id"] == "EVT-REPLAY"
    assert audit["previous_status"] == "DEAD_LETTER"


def test_replaying_an_already_recorded_event_adds_no_record(member, paypal_signer):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event("EVT-REPLAY-TWICE"), WEBHOOK_ID)
    process()
    [row] = inbox(member.tenant, "EVT-REPLAY-TWICE")
    force_status(member.tenant, row.id, "FAILED")

    assert replay(member, source, row.id).json()["status"] == "PROCESSED"
    assert records_for(member.tenant, "EVT-REPLAY-TWICE") == 1


@pytest.mark.parametrize("status", ["RECEIVED", "PROCESSED"])
def test_only_failed_or_dead_lettered_events_replay(member, paypal_signer, status):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event(f"EVT-NOT-{status}"), WEBHOOK_ID)
    [row] = inbox(member.tenant, f"EVT-NOT-{status}")
    if status == "PROCESSED":
        process()

    response = replay(member, source, row.id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WEBHOOK_NOT_REPLAYABLE"


def test_replay_through_another_source_is_not_found(member, paypal_signer):
    source, other = paypal_source(member), paypal_source(member, webhook_id="WH-OTHER")
    paypal_signer.deliver(client, source, event("EVT-WRONG-SOURCE"), WEBHOOK_ID)
    [row] = inbox(member.tenant, "EVT-WRONG-SOURCE")
    force_status(member.tenant, row.id, "DEAD_LETTER")

    assert replay(member, other, row.id).status_code == 404
    assert replay(member, source, uuid4()).status_code == 404
    assert inbox(member.tenant, "EVT-WRONG-SOURCE")[0].status == "DEAD_LETTER"


def test_replay_needs_the_webhook_key(member, paypal_signer):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event("EVT-NO-KEY"), WEBHOOK_ID)
    [row] = inbox(member.tenant, "EVT-NO-KEY")
    force_status(member.tenant, row.id, "DEAD_LETTER")

    operator = Actor()
    add_member(member.tenant, operator.id)
    grant_set(member.tenant, operator.id, "domain_operator")

    listed = operator.request("GET", events_url(source), tenant=member.tenant)
    assert listed.status_code == 200  # source.read is enough to see the state
    refused = replay(operator, source, row.id, tenant=member.tenant)
    assert refused.status_code == 403
    assert refused.json()["error"]["permission"] == "source.webhook.manage"
    assert inbox(member.tenant, "EVT-NO-KEY")[0].status == "DEAD_LETTER"
