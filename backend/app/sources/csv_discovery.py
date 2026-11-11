"""CSV metadata discovery and aggregate profiling (task 2.6, AC-ONT-01, AC-ONT-02).

The stored raw-evidence file is read once; only the header, inferred types and aggregates are
kept. No raw rows leave the file into the database or the ontology payload.
"""

import csv
import io
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.errors import ApiError
from app.sources import conventions
from app.sources.discovery_types import (
    FieldSnapshot,
    ObjectProfile,
    ObjectResult,
    SchemaSnapshot,
)

_MAX_FIELD_SIZE = 1_000_000
_MAX_DISTINCT = 10_000
_MAX_DISTRIBUTION_VALUES = 20
_MAX_TEMPORAL_BUCKETS = 100
_DELIMITERS = ",;\t|"
_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_CHOICE = ("status", "state", "type", "kind", "category", "method", "provider", "country",
           "channel", "code")
_PRIVATE = (
    "email", "phone", "name", "address", "account", "iban", "card", "ssn", "customer",
    "postal", "zip", "birth", "dob", "tax_id", "national", "passport",
)
_MONETARY = ("amount", "total", "price", "fee", "tax", "subtotal", "discount", "shipping",
             "balance")
_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}


def discover(
    data: bytes, file_name: str, content_hash: str, max_rows: int
) -> ObjectResult:
    """Profile a CSV file and return one source-object snapshot and profile."""
    text = _decode(data)
    sample = text[:65536]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        delimiter = ","
    csv.field_size_limit(_MAX_FIELD_SIZE)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = _header(next(reader, None))
    columns = [_Accumulator(name) for name in header]
    row_count = 0
    sampled = False
    for row in reader:
        if not row:
            continue
        row_count += 1
        if row_count > max_rows:
            row_count -= 1
            sampled = True
            break
        if len(row) != len(header):
            message = (
                f"Row {row_count} has {len(row)} columns, expected {len(header)}."
            )
            raise ApiError(422, "INVALID_CSV", message)
        for column, value in zip(columns, row, strict=True):
            column.add(value.strip())

    fields, profile_fields = _finish(columns, row_count, sampled)
    safe_name = Path(file_name).name[:200] or "upload.csv"
    return ObjectResult(
        object_name=conventions.file_object_name(safe_name, content_hash),
        snapshot=SchemaSnapshot(
            object_type="CSV",
            fields=fields,
            format={
                "delimiter": delimiter,
                "encoding": "utf-8",
                "header": header,
                "file_name": safe_name,
                "content_hash": content_hash,
            },
        ),
        profile=ObjectProfile(row_count=row_count, sampled=sampled, fields=profile_fields),
    )


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(422, "INVALID_CSV", "The file is not valid UTF-8.") from None
    if not text.strip():
        raise ApiError(422, "INVALID_CSV", "The file is empty.")
    return text


def _header(row: list[str] | None) -> list[str]:
    if row is None:
        raise ApiError(422, "INVALID_CSV", "A header row is required.")
    header = [name.strip() for name in row]
    if not header or any(not name for name in header):
        raise ApiError(422, "INVALID_CSV", "Every column needs a header name.")
    if len(set(header)) != len(header):
        raise ApiError(422, "INVALID_CSV", "Column headers must be unique.")
    return header


class _Accumulator:
    def __init__(self, name: str) -> None:
        self.name = name
        self.non_null = 0
        self.distinct: set[str] = set()
        self.distinct_truncated = False
        self.counts: dict[str, int] = {}
        self.distribution_truncated = False
        self.candidates: set[str] | None = None
        self.minimum: Any = None
        self.maximum: Any = None
        self.length_min: int | None = None
        self.length_max: int | None = None
        self.true_count = 0
        self.false_count = 0
        self.temporal: dict[str, int] = {}
        self.temporal_truncated = False
        self.currency_values = True

    def add(self, value: str) -> None:
        if not value:
            return
        self.non_null += 1
        if not self.distinct_truncated:
            self.distinct.add(value)
            if len(self.distinct) > _MAX_DISTINCT:
                self.distinct_truncated = True
        if not self.distribution_truncated:
            self.counts[value] = self.counts.get(value, 0) + 1
            if len(self.counts) > _MAX_DISTRIBUTION_VALUES:
                self.distribution_truncated = True
        if not _CURRENCY.fullmatch(value):
            self.currency_values = False
        length = len(value)
        self.length_min = length if self.length_min is None else min(self.length_min, length)
        self.length_max = length if self.length_max is None else max(self.length_max, length)
        candidates = _candidates(value)
        self.candidates = candidates if self.candidates is None else self.candidates & candidates
        if "boolean" in candidates:
            if value.lower() in _TRUE:
                self.true_count += 1
            elif value.lower() in _FALSE:
                self.false_count += 1
        if "integer" in candidates or "decimal" in candidates:
            decimal = _decimal(value)
            if decimal is not None:
                self.minimum = decimal if self.minimum is None else min(self.minimum, decimal)
                self.maximum = decimal if self.maximum is None else max(self.maximum, decimal)
        if "date" in candidates or "timestamp" in candidates:
            moment = _moment(value)
            if moment is not None:
                self.minimum = moment if self.minimum is None else min(self.minimum, moment)
                self.maximum = moment if self.maximum is None else max(self.maximum, moment)
                key = moment.date().isoformat()
                if len(self.temporal) < _MAX_TEMPORAL_BUCKETS:
                    self.temporal[key] = self.temporal.get(key, 0) + 1
                else:
                    self.temporal_truncated = True


def _finish(
    columns: list[_Accumulator], row_count: int, sampled: bool
) -> tuple[list[FieldSnapshot], dict[str, dict[str, Any]]]:
    datatypes = [_datatype(column) for column in columns]
    currency_columns = [
        column.name for column, datatype in zip(columns, datatypes, strict=True)
        if datatype == "currency"
    ]
    fields: list[FieldSnapshot] = []
    profiles: dict[str, dict[str, Any]] = {}
    for column, datatype in zip(columns, datatypes, strict=True):
        null_count = row_count - column.non_null
        distinct_count = len(column.distinct)
        unique = (
            row_count > 0
            and not sampled
            and not column.distinct_truncated
            and null_count == 0
            and distinct_count == row_count
        )
        fields.append(
            FieldSnapshot(
                name=column.name,
                datatype=datatype,
                nullable=null_count > 0 or row_count == 0,
                unique=unique,
            )
        )
        profile: dict[str, Any] = {
            "non_null_count": column.non_null,
            "null_count": null_count,
            "null_fraction": round(null_count / row_count, 6) if row_count else 0.0,
            "distinct_count": distinct_count,
            "duplicate_count": (
                None
                if column.distinct_truncated
                else column.non_null - distinct_count
            ),
        }
        if column.distinct_truncated:
            profile["distinct_count_truncated"] = True
            profile["duplicate_count_at_least"] = column.non_null - distinct_count
        if datatype in ("integer", "decimal") and column.minimum is not None:
            profile["min"] = format(column.minimum, "f")
            profile["max"] = format(column.maximum, "f")
        elif datatype in ("date", "timestamp") and column.minimum is not None:
            if datatype == "date":
                profile["min"] = column.minimum.date().isoformat()
                profile["max"] = column.maximum.date().isoformat()
            else:
                profile["min"] = column.minimum.isoformat()
                profile["max"] = column.maximum.isoformat()
            profile["distribution"] = {
                "bucket": "day",
                "values": [
                    {"value": key, "count": count}
                    for key, count in sorted(column.temporal.items())
                ],
                "truncated": column.temporal_truncated,
            }
        elif datatype in ("text", "currency"):
            profile["length_min"] = column.length_min
            profile["length_max"] = column.length_max
        if datatype == "boolean":
            profile["true_count"] = column.true_count
            profile["false_count"] = column.false_count
            profile["distribution"] = [
                {"value": "true", "count": column.true_count},
                {"value": "false", "count": column.false_count},
            ]
        if datatype == "currency":
            profile["currency"] = True
            if column.distribution_truncated:
                profile["distribution_truncated"] = True
            else:
                profile["distribution"] = [
                    {"value": value, "count": count}
                    for value, count in sorted(
                        column.counts.items(), key=lambda item: (-item[1], item[0])
                    )[: _MAX_DISTRIBUTION_VALUES]
                ]
        elif _choice(column.name) and datatype in ("text", "integer", "decimal"):
            if column.distribution_truncated:
                profile["distribution_truncated"] = True
            else:
                profile["distribution"] = [
                    {"value": value, "count": count}
                    for value, count in sorted(
                        column.counts.items(), key=lambda item: (-item[1], item[0])
                    )[: _MAX_DISTRIBUTION_VALUES]
                ]
        if datatype in ("integer", "decimal") and _monetary(column.name):
            profile["monetary"] = True
            if currency_columns:
                profile["currency_column"] = currency_columns[0]
        profiles[column.name] = profile
    return fields, profiles


def _datatype(column: _Accumulator) -> str:
    if column.non_null == 0 or column.candidates is None:
        return "text"
    if _is_currency(column) and column.currency_values:
        return "currency"
    for name in ("boolean", "integer", "decimal", "date", "timestamp"):
        if name in column.candidates:
            return name
    return "text"


def _candidates(value: str) -> set[str]:
    lowered = value.lower()
    if lowered in _TRUE or lowered in _FALSE:
        return {"boolean", "text"}
    if _DATE_ONLY.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return {"text"}
        return {"date", "timestamp", "text"}
    if _timestamp(value):
        return {"timestamp", "text"}
    if _INTEGER.fullmatch(value):
        digits = value.lstrip("+-")
        if digits != "0" and digits.startswith("0"):
            return {"text"}
        return {"integer", "decimal", "text"}
    if _DECIMAL.fullmatch(value):
        return {"decimal", "text"}
    return {"text"}


def _timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _moment(value: str) -> datetime | None:
    if _DATE_ONLY.fullmatch(value):
        try:
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _decimal(value: str) -> Decimal | None:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _is_currency(column: _Accumulator) -> bool:
    lowered = column.name.lower()
    return "currency" in lowered or lowered == "ccy"


def _choice(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _CHOICE) and not any(
        hint in lowered for hint in _PRIVATE
    )


def _monetary(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _MONETARY)
