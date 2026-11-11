"""Reads one mapped field from a source record and normalizes it (spec 5.4, 5.5).

Field paths are the JSONPath subset the mapping editor writes: `$.a.b`, `$.a[0]` and
`$['column name']`. Transforms are the mapping allowlist; nothing here evaluates code. A value
that does not normalize keeps its raw form and gets no normalized value, so it is preserved
but never compared.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MISSING = object()
_SEGMENT = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]|\['([^']+)'\]")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SCALE = Decimal(1).scaleb(-6)
_MAX_ABS = Decimal(10) ** 14


@dataclass(frozen=True)
class Normalized:
    raw: Any
    text: str | None
    numeric: Decimal | None
    currency: str | None


def extract(document: Any, path: str) -> Any:
    """The value at `path`, or MISSING when any step does not exist."""
    rest = path[1:] if path.startswith("$") else f".{path}"
    value, position = document, 0
    while position < len(rest):
        match = _SEGMENT.match(rest, position)
        if match is None:
            return MISSING
        key, index = match.group(1) or match.group(3), match.group(2)
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return MISSING
            value = value[int(index)]
        else:
            if not isinstance(value, dict) or key not in value:
                return MISSING
            value = value[key]
        position = match.end()
    return value


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or isinstance(value, float):
        return None  # floats are never authoritative money (spec 6.3)
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not number.is_finite() or abs(number) >= _MAX_ABS or number != number.quantize(_SCALE):
        return None
    return number.quantize(_SCALE)


def _plain(number: Decimal) -> str:
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _transform(value: Any, transform: dict[str, Any]) -> str | None:
    name = transform["name"]
    if name == "identity":
        return _as_text(value)
    if name == "upper":
        return _as_text(value).upper()
    if name == "lower":
        return _as_text(value).lower()
    if name == "decimal_string":
        number = _decimal(value)
        return None if number is None else _plain(number)
    if name == "iso8601":
        try:
            moment = datetime.fromisoformat(_as_text(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return None if moment.tzinfo is None else moment.astimezone(UTC).isoformat()
    if name == "enum_map":
        return transform["values"].get(_as_text(value))
    return None


def normalize(attributes: dict[str, Any], mapping: dict[str, Any]) -> Normalized | None:
    """None when the record does not carry the mapped field at all."""
    raw = extract(attributes, mapping["field_path"])
    if raw is MISSING:
        return None
    text = None if raw is None else _transform(raw, mapping["transform"])
    numeric = None
    if mapping["datatype"] == "decimal" and text is not None:
        numeric = _decimal(text)
        text = None if numeric is None else _plain(numeric)
    currency = None
    if mapping["currency_field"]:
        found = extract(attributes, mapping["currency_field"])
        if isinstance(found, str) and _CURRENCY.match(found.strip().upper()):
            currency = found.strip().upper()
    return Normalized(raw, text, numeric, currency)
