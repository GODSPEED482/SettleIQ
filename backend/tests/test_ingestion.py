"""Bank CSV and Balance Report ingestion (task 3.6, AC-REC-01, AC-OPEN-02, AC-PP-11)."""

import hashlib
from uuid import UUID

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import scoped_tx
from tests.support import grant_set
from tests.test_semantics_store import Member
from tests.test_sources import create_ok

BANK_CSV = (
    "txn_id,booked_on,amount,currency,status,memo,extra\n"
    "T-1,2026-11-01,10.00,USD,BOOKED,café,one\n"
    "T-2,2026-11-02,25.50,USD,BOOKED,,two\n"
    "T-3,2026-11-03,-4.25,EUR,PENDING,third,three\n"
).encode()

BRR_CSV = (
    b"Transaction ID,Date,Type,Currency,Net,Balance,Balance Affecting,Memo\n"
    b"BRR-1,2026-11-01,Sale,USD,10.00,110.00,Y,first\n"
    b"BRR-2,2026-11-02,Refund,USD,-2.00,108.00,Y,second\n"
)

SYNTHETIC_BRR_CSV = BRR_CSV.replace(b"BRR-1", b"SYN-1").replace(b"first", b"fixture")


def operator(name: str = "T") -> Member:
    member = Member(name)
    grant_set(member.tenant, member.principal.id, "integration_admin")
    return member


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RAW_EVIDENCE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def csv_source(member: Member) -> dict:
    return create_ok(member, connector_id="tenant-csv", display_name="Bank files", config={})


def upload(
    member: Member,
    source_id: str,
    data: bytes = BANK_CSV,
    name: str = "bank.csv",
    kind: str = "BANK_CSV",
):
    return member.request(
        "POST",
        f"/api/sources/{source_id}/files",
        files={"file": (name, data, "text/csv")},
        data={"kind": kind},
    )


def stored_records(member: Member, source_id: str):
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text(
                """
                SELECT external_id, record_type, attributes, raw_evidence_id
                FROM source_records WHERE source_id = :source
                ORDER BY external_id
                """
            ),
            {"source": UUID(source_id)},
        ).all()


@pytest.mark.db
def test_bank_csv_is_stored_as_evidence_and_one_record_per_row(evidence_root):
    member = operator()
    source = csv_source(member)
    response = upload(member, source["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provenance"] == "tenant_file"
    assert body["record_count"] == 3
    assert body["checksum"] == hashlib.sha256(BANK_CSV).hexdigest()
    assert body["object_name"] == f"bank.csv@{body['checksum'][:16]}"

    records = stored_records(member, source["id"])
    assert [row.external_id for row in records] == ["row:1", "row:2", "row:3"]
    assert {row.record_type for row in records} == {"tenant-csv:file/BANK_CSV"}
    assert records[0].attributes == {
        "txn_id": "T-1",
        "booked_on": "2026-11-01",
        "amount": "10.00",
        "currency": "USD",
        "status": "BOOKED",
        "memo": "café",
        "extra": "one",
    }
    with scoped_tx(member.tenant) as conn:
        evidence = conn.execute(
            text("SELECT evidence_type, retrieval_context, content_hash FROM raw_evidence")
        ).one()
    assert evidence.evidence_type == "FILE"
    assert evidence.retrieval_context == {
        "provenance": "tenant_file",
        "file_name": "bank.csv",
        "row_count": 3,
    }
    assert evidence.content_hash == body["checksum"]


@pytest.mark.db
def test_reuploaded_file_does_not_duplicate_evidence_or_records(evidence_root):
    member = operator()
    source = csv_source(member)
    assert upload(member, source["id"]).status_code == 200
    assert upload(member, source["id"]).status_code == 200
    assert len(stored_records(member, source["id"])) == 3
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM raw_evidence")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM source_objects")).scalar_one() == 1


@pytest.mark.db
def test_brr_provider_and_synthetic_provenance_stay_distinct(evidence_root):
    member = operator()
    source = csv_source(member)
    provider = upload(member, source["id"], BRR_CSV, "balance_report.csv", "BRR")
    assert provider.status_code == 200, provider.text
    assert provider.json()["provenance"] == "provider_report"
    synthetic = upload(
        member, source["id"], SYNTHETIC_BRR_CSV, "SYNTHETIC_balance_report.csv", "BRR"
    )
    assert synthetic.status_code == 200, synthetic.text
    assert synthetic.json()["provenance"] == "synthetic_reference"
    assert provider.json()["checksum"] != synthetic.json()["checksum"]
    assert provider.json()["record_count"] == 2
    with scoped_tx(member.tenant) as conn:
        contexts = conn.execute(
            text("SELECT retrieval_context FROM raw_evidence ORDER BY received_at")
        ).scalars().all()
        records = conn.execute(
            text("SELECT record_type FROM source_records")
        ).scalars().all()
    assert [context["provenance"] for context in contexts] == [
        "provider_report",
        "synthetic_reference",
    ]
    assert set(records) == {"tenant-csv:file/BRR"}


@pytest.mark.db
def test_brr_balances_are_checked_per_currency(evidence_root):
    member = operator()
    source = csv_source(member)
    mixed = (
        b"Transaction ID,Date,Type,Currency,Net,Balance,Balance Affecting\n"
        b"BRR-1,2026-11-01,Sale,USD,10.00,110.00,Y\n"
        b"BRR-2,2026-11-01,Sale,EUR,5.00,55.00,Y\n"
        b"BRR-3,2026-11-02,Refund,USD,-2.00,108.00,Y\n"
        b"BRR-4,2026-11-02,Fee,EUR,1.00,56.00,Y\n"
    )
    response = upload(member, source["id"], mixed, "balance_report.csv", "BRR")
    assert response.status_code == 200, response.text
    assert response.json()["record_count"] == 4


@pytest.mark.db
def test_provenance_is_reachable_from_every_record(evidence_root):
    member = operator()
    source = csv_source(member)
    upload(member, source["id"], BRR_CSV, "SYNTHETIC_balance_report.csv", "BRR")
    with scoped_tx(member.tenant) as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.retrieval_context
                FROM source_records r JOIN raw_evidence e ON e.id = r.raw_evidence_id
                """
            )
        ).scalars().all()
    assert len(rows) == 2
    assert all(row["provenance"] == "synthetic_reference" for row in rows)


@pytest.mark.db
def test_brr_requires_named_columns_and_consistent_totals(evidence_root):
    member = operator()
    source = csv_source(member)
    missing = b"Transaction ID,Date,Type,Currency,Net\nBRR-1,2026-11-01,Sale,USD,10.00\n"
    response = upload(member, source["id"], missing, "balance_report.csv", "BRR")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BRR_HEADER_INVALID"

    inconsistent = (
        b"Transaction ID,Date,Type,Currency,Net,Balance\n"
        b"BRR-1,2026-11-01,Sale,USD,10.00,110.00\n"
        b"BRR-2,2026-11-02,Refund,USD,-2.00,120.00\n"
    )
    response = upload(member, source["id"], inconsistent, "balance_report.csv", "BRR")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BRR_TOTALS_INCONSISTENT"


@pytest.mark.db
def test_malformed_csv_and_unknown_source_are_refused(evidence_root):
    member = operator()
    source = csv_source(member)
    duplicate_header = upload(member, source["id"], b"id,id\n1,2\n")
    assert duplicate_header.status_code == 409
    assert duplicate_header.json()["error"]["code"] == "CSV_HEADER_INVALID"
    not_utf8 = upload(member, source["id"], b"\xff\xfeid\n1\n")
    assert not_utf8.status_code == 409
    assert not_utf8.json()["error"]["code"] == "CSV_UNREADABLE"


@pytest.mark.db
def test_source_must_accept_files(evidence_root):
    member = operator()
    source = create_ok(
        member,
        connector_id="merchant-postgres",
        display_name="Merchant DB",
        config={"schema": "public"},
    )
    response = upload(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FILE_INGESTION_UNSUPPORTED"


@pytest.mark.db
def test_file_upload_is_audited_without_the_contents(evidence_root):
    member = operator()
    source = csv_source(member)
    upload(member, source["id"])
    with scoped_tx(member.tenant) as conn:
        detail = conn.execute(
            text(
                "SELECT detail::text AS detail FROM audit_logs "
                "WHERE action = 'source.file.uploaded'"
            )
        ).scalar_one()
    assert source["id"] in detail
    assert "café" not in detail
    assert "txn_id" not in detail


@pytest.mark.db
def test_other_tenant_cannot_upload(evidence_root):
    member = operator()
    source = csv_source(member)
    assert upload(operator("B"), source["id"]).status_code == 404


@pytest.mark.db
def test_a_different_file_is_a_new_object_and_new_records(evidence_root):
    member = operator()
    source = csv_source(member)
    upload(member, source["id"], BANK_CSV, "bank.csv")
    changed = BANK_CSV.replace(b"T-1", b"T-9")
    upload(member, source["id"], changed, "bank.csv")
    assert len(stored_records(member, source["id"])) == 6
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_objects")).scalar_one() == 2
