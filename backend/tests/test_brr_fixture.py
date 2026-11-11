"""The SYNTHETIC/REFERENCE BRR fixture (task 3.8, spec 10.3.1, AC-REC-07)."""

import hashlib
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.domain_packs.manifest import PACKS_DIR, load_domain_packs
from app.sources.csv_files import read_csv
from app.sources.file_ingest import BALANCE_AFFECTING_COLUMN, _validate_brr
from packs.finops.money import Money
from packs.finops.settlement import SettlementLine, category, contribution, treatment
from tests.test_ingestion import csv_source, evidence_root, operator, upload  # noqa: F401

PACK = PACKS_DIR / "finops"
MANIFEST = PACK / "fixtures" / "SYNTHETIC_REFERENCE_balance_report_v1.manifest.yaml"


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def fixture_path() -> Path:
    return MANIFEST.parent / manifest()["file"]


def fixture_rows() -> list[dict[str, str]]:
    header, rows, _ = read_csv(fixture_path().read_bytes())
    return [dict(zip(header, row, strict=True)) for row in rows]


def money(value: str) -> Money:
    return Money.parse(value, manifest()["currency"])


def test_file_matches_its_checksum():
    assert hashlib.sha256(fixture_path().read_bytes()).hexdigest() == manifest()["sha256"]


def test_fixture_is_labelled_synthetic_and_listed_by_the_pack():
    assert "SYNTHETIC" in fixture_path().name
    assert manifest()["provenance"] == "synthetic_reference"
    declared = load_domain_packs()["finops"].test_fixtures
    assert {PACK / path for path in declared} == {fixture_path(), MANIFEST}


def test_fixture_passes_the_shared_brr_parser():
    header, rows, _ = read_csv(fixture_path().read_bytes())
    _validate_brr(header, rows)


def test_balances_run_from_opening_to_closing():
    data = manifest()
    balance = Decimal(data["opening_balance"])
    for row in fixture_rows():
        assert row["Currency"] == data["currency"]
        if row[BALANCE_AFFECTING_COLUMN] == "Y":
            balance += Decimal(row["Net"])
        assert Decimal(row["Balance"]) == balance
    assert balance == Decimal(data["closing_balance"])


def test_each_row_states_its_open_10_treatment():
    data = manifest()
    assert data["treatment_version"] == treatment()["version"]
    rows = fixture_rows()
    assert [row["Transaction ID"] for row in rows] == [
        stated["transaction_id"] for stated in data["rows"]
    ]
    for row, stated in zip(rows, data["rows"], strict=True):
        assert row["Transaction Event Code"] == stated["event_code"]
        name = category(stated["event_code"])
        assert name == stated["category"]
        assert treatment()["categories"][name]["included"] is stated["included_in_net"]


def test_expected_net_counts_netted_fees_once():
    totals: dict[str, Money] = {}
    for row in fixture_rows():
        line = SettlementLine(
            reference=row["Transaction ID"],
            entry_type=row["Transaction Event Code"],
            gross=money(row["Gross"]),
            fee=money(row["Fee"]),
            net=money(row["Net"]),
        )
        amount = contribution(line)
        assert amount is not None, f"{line.reference} is unclassified"
        settlement = row["Settlement ID"]
        totals[settlement] = totals.get(settlement, money("0")) + amount
    expected = {key: money(value) for key, value in manifest()["expected_net"].items()}
    assert totals == expected
    # Subtracting the netted fees a second time would understate Expected Net (AC-REC-07).
    netted_fees = sum(
        (Decimal(row["Fee"]) for row in fixture_rows() if Decimal(row["Fee"]) < 0), Decimal(0)
    )
    assert netted_fees < 0
    assert totals["SYN-S-1"].amount + netted_fees != expected["SYN-S-1"].amount


def test_fixture_holds_no_personal_data():
    content = fixture_path().read_text(encoding="utf-8")
    assert "@" not in content
    assert not re.search(r"\b\d{12,19}\b", content), "looks like a card or account number"
    assert all(row["Transaction ID"].startswith("SYN-") for row in fixture_rows())


@pytest.mark.db
@pytest.mark.usefixtures("evidence_root")
def test_fixture_uploads_as_synthetic_reference():
    member = operator()
    source = csv_source(member)
    response = upload(
        member, source["id"], fixture_path().read_bytes(), fixture_path().name, "BRR"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provenance"] == "synthetic_reference"
    assert body["checksum"] == manifest()["sha256"]
    assert body["record_count"] == len(manifest()["rows"])
