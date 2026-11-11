"""Profile aggregates for discovered fields (spec 5.7, AC-ONT-02).

One profiler serves every connector type, so generation sees the same shape whether the
fields came from a PostgreSQL table or a CSV file. It reads a bounded sample and keeps only
aggregates: counts, ranges, and value distributions for low-cardinality fields whose values
repeat. Identifier fields never get a distribution, so no record can be picked out of one.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SAMPLE_ROWS = 10_000
TOP_VALUES = 20
TEMPORAL_BUCKETS = 24

NUMERIC = {"integer", "decimal"}
TEMPORAL = {"date", "datetime"}

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_BOOLEAN = {"true": True, "false": False}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    datatype: str
    identifier: bool = False


def looks_like_identifier(name: str) -> bool:
    """`id`, `order_id` or `orderId`: a name that says it identifies a record."""
    return name.lower() == "id" or name.lower().endswith("_id") or name.endswith("Id")


def _parse_datetime(value: str) -> datetime | date | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if len(value) == 10:
        return parsed.date()
    return parsed


def infer_datatype(values: Iterable[str | None]) -> str:
    """The narrowest generic type every non-empty value fits; string when nothing narrower."""
    present = [value.strip() for value in values if value is not None and value.strip()]
    if not present:
        return "string"
    if all(_INTEGER.match(value) for value in present):
        return "integer"
    if all(_DECIMAL.match(value) for value in present):
        return "decimal"
    if all(value.lower() in _BOOLEAN for value in present):
        return "boolean"
    parsed = [_parse_datetime(value) for value in present]
    if all(isinstance(value, datetime) for value in parsed):
        return "datetime"
    if all(isinstance(value, date) and not isinstance(value, datetime) for value in parsed):
        return "date"
    return "string"


def coerce(value: str | None, datatype: str) -> Any:
    """A CSV cell as the inferred type; an empty cell is a null."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        if datatype == "integer":
            return int(value)
        if datatype == "decimal":
            return Decimal(value)
    except (ValueError, InvalidOperation):
        return value
    if datatype == "boolean":
        return _BOOLEAN.get(value.lower(), value)
    if datatype in TEMPORAL:
        return _parse_datetime(value) or value
    return value


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _column_profile(spec: ColumnSpec, values: list[Any]) -> dict[str, Any]:
    present = [value for value in values if value is not None]
    counts = Counter(present)
    nulls = len(values) - len(present)
    result: dict[str, Any] = {
        "null_count": nulls,
        "null_fraction": round(nulls / len(values), 4) if values else 0.0,
        "distinct_count": len(counts),
    }
    if spec.identifier:
        result["duplicate_identifiers"] = sum(1 for count in counts.values() if count > 1)
        return result
    if not present:
        return result
    if spec.datatype in NUMERIC:
        numbers = [value for value in present if isinstance(value, (int, Decimal, float))]
        if numbers:
            result["min"] = _json_scalar(min(numbers))
            result["max"] = _json_scalar(max(numbers))
    elif spec.datatype in TEMPORAL:
        moments = [value for value in present if isinstance(value, (date, datetime))]
        if moments and _comparable(moments):
            result["earliest"] = _json_scalar(min(moments))
            result["latest"] = _json_scalar(max(moments))
            buckets = Counter(f"{moment.year:04d}-{moment.month:02d}" for moment in moments)
            recent = sorted(buckets)[-TEMPORAL_BUCKETS:]
            result["temporal_distribution"] = {bucket: buckets[bucket] for bucket in recent}
    elif spec.datatype == "boolean":
        result["true_count"] = sum(1 for value in present if value is True)
    elif spec.datatype == "string" and _is_categorical(counts, len(present)):
        top = dict(counts.most_common(TOP_VALUES))
        if all(isinstance(value, str) and _CURRENCY.match(value) for value in counts):
            result["currency_distribution"] = top
        else:
            result["status_distribution"] = top
    return result


def _comparable(moments: list[date | datetime]) -> bool:
    """Naive and zone-aware timestamps cannot be ordered against each other."""
    return len({getattr(moment, "tzinfo", None) is None for moment in moments}) == 1


def _is_categorical(counts: Counter, present: int) -> bool:
    """Few distinct values that each repeat on average, so no value names a single record."""
    return len(counts) <= TOP_VALUES and present >= 2 * len(counts)


def profile(
    columns: Sequence[ColumnSpec], rows: Iterable[Sequence[Any]], row_count: int | None = None
) -> dict[str, Any]:
    """Aggregates over at most SAMPLE_ROWS rows. `row_count` is the exact total when known."""
    sample = [row for _, row in zip(range(SAMPLE_ROWS), rows, strict=False)]
    return {
        "row_count": len(sample) if row_count is None else row_count,
        "sampled_rows": len(sample),
        "columns": {
            spec.name: _column_profile(spec, [row[index] for row in sample])
            for index, spec in enumerate(columns)
        },
    }
