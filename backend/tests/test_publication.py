"""Ontology publication through the real route (POL-12, AC-ONTO-PUB-01..05, AC-ONT-05, 06)."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from tests.test_semantics_store import Member


@pytest.fixture
def finance_draft() -> tuple[Member, dict]:
    member = Member()
    member.activate("finops")
    return member, member.create_draft().json()


def validate(member: Member, draft: dict) -> dict:
    return member.request("POST", "/api/ontology/validate", json={"draft_id": draft["id"]}).json()


def publish_body(draft: dict, run: dict, **overrides) -> dict:
    return {
        "draft_id": draft["id"],
        "validation_run_id": run["validation_run_id"],
        "confirm_publish": True,
    } | overrides


def post_publish(member: Member, body: dict):
    return member.request("POST", "/api/ontology/publish", json=body)


def published_count(member: Member) -> int:
    with scoped_tx(member.tenant) as conn:
        return conn.execute(text("SELECT count(*) FROM ontologies")).scalar_one()


@pytest.mark.db
@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_anything_but_an_explicit_true_is_refused(finance_draft, value):
    member, draft = finance_draft
    run = validate(member, draft)
    response = post_publish(member, publish_body(draft, run, confirm_publish=value))
    assert response.status_code == 422
    assert published_count(member) == 0


@pytest.mark.db
def test_a_missing_confirmation_is_refused(finance_draft):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft))
    del body["confirm_publish"]
    assert post_publish(member, body).status_code == 422
    assert published_count(member) == 0


@pytest.mark.db
@pytest.mark.parametrize("field", ["tenant_id", "published_by", "content_hash", "version"])
def test_the_body_cannot_carry_server_derived_fields(finance_draft, field):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft), **{field: str(uuid4())})
    assert post_publish(member, body).status_code == 422
