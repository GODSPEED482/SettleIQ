"""Profile aggregates shared by PostgreSQL and CSV discovery (AC-ONT-02)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.sources import profiling
from app.sources.profiling import ColumnSpec, coerce, infer_datatype, looks_like_identifier


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "-2", "", None], "integer"),
        (["1.50", "2", "3e2"], "decimal"),
        (["true", "False"], "boolean"),
        (["2026-11-01T10:00:00", "2026-11-02 09:30:00"], "datetime"),
        (["2026-11-01", "2026-12-31"], "date"),
        (["2026-11-01", "2026-11-01T10:00:00"], "string"),
        (["USD", "1"], "string"),
        (["", None], "string"),
    ],
)
def test_csv_types_are_inferred_from_every_value(values, expected):
    assert infer_datatype(values) == expected


def test_cells_coerce_to_the_inferred_type():
    assert coerce("12", "integer") == 12
    assert coerce("12.50", "decimal") == Decimal("12.50")
    assert coerce("TRUE", "boolean") is True
    assert coerce("2026-11-01", "date") == date(2026, 11, 1)
    assert coerce("  ", "string") is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("id", True), ("order_id", True), ("orderId", True), ("paid", False), ("status", False)],
)
def test_identifier_names(name, expected):
    assert looks_like_identifier(name) is expected


COLUMNS = [
    ColumnSpec("payment_id", "string", identifier=True),
    ColumnSpec("amount", "decimal"),
    ColumnSpec("currency", "string"),
    ColumnSpec("status", "string"),
    ColumnSpec("created_at", "datetime"),
    ColumnSpec("note", "string"),
    ColumnSpec("refunded", "boolean"),
]

ROWS = [
    ("P-1", Decimal("10.00"), "USD", "COMPLETED", datetime(2026, 10, 3), "alice", False),
    ("P-2", Decimal("25.50"), "USD", "PENDING", datetime(2026, 11, 1), "bob", True),
    ("P-2", None, "EUR", "COMPLETED", datetime(2026, 11, 2), None, False),
    ("P-3", Decimal("-4.25"), "USD", "COMPLETED", None, "carol", False),
]


@pytest.fixture
def result():
    return profiling.profile(COLUMNS, iter(ROWS), row_count=40)


def test_profile_counts_nulls_and_cardinality(result):
    assert result["row_count"] == 40
    assert result["sampled_rows"] == 4
    amount = result["columns"]["amount"]
    assert amount["null_count"] == 1
    assert amount["null_fraction"] == 0.25
    assert amount["distinct_count"] == 3


def test_profile_reports_duplicate_identifiers_without_values(result):
    payment_id = result["columns"]["payment_id"]
    assert payment_id["duplicate_identifiers"] == 1
    assert "status_distribution" not in payment_id


def test_profile_reports_ranges(result):
    assert result["columns"]["amount"]["min"] == "-4.25"
    assert result["columns"]["amount"]["max"] == "25.50"
    created = result["columns"]["created_at"]
    assert created["earliest"] == "2026-10-03T00:00:00"
    assert created["latest"] == "2026-11-02T00:00:00"
    assert created["temporal_distribution"] == {"2026-10": 1, "2026-11": 2}


def test_profile_reports_currency_and_status_distributions(result):
    assert result["columns"]["currency"]["currency_distribution"] == {"USD": 3, "EUR": 1}
    assert result["columns"]["status"]["status_distribution"] == {"COMPLETED": 3, "PENDING": 1}
    assert result["columns"]["refunded"]["true_count"] == 1


def test_values_that_do_not_repeat_are_never_listed(result):
    note = result["columns"]["note"]
    assert note["distinct_count"] == 3
    assert "status_distribution" not in note
    assert "alice" not in repr(result)
    assert "P-1" not in repr(result)


def test_profile_reads_at_most_the_sample(monkeypatch):
    monkeypatch.setattr(profiling, "SAMPLE_ROWS", 2)
    rows = ((str(n),) for n in range(10))
    result = profiling.profile([ColumnSpec("n", "string")], rows)
    assert result["sampled_rows"] == 2
    assert result["row_count"] == 2


def test_mixed_timezones_skip_the_range():
    moments = [(datetime(2026, 1, 1),), (datetime(2026, 1, 2, tzinfo=UTC),)]
    column = profiling.profile([ColumnSpec("at", "datetime")], moments)["columns"]["at"]
    assert "earliest" not in column
    assert column["null_count"] == 0
