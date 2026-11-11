"""RFC 8785 canonical JSON for release payloads (spec 9.7.3).

Payloads are limited to strings, integers, booleans, null, arrays and objects; a float anywhere
is refused, so no value can serialize two ways. Verification checks the stored bytes are already
canonical and never re-serializes them for the signature.
"""

import json
from typing import Any

import rfc8785


class NotCanonical(ValueError):
    pass


def _check(value: Any) -> None:
    if isinstance(value, float):
        raise NotCanonical("floating-point values are not allowed")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NotCanonical("object keys must be strings")
            _check(item)
    elif isinstance(value, list):
        for item in value:
            _check(item)
    elif not (value is None or isinstance(value, str | int | bool)):
        raise NotCanonical(f"unsupported type {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    _check(value)
    return rfc8785.dumps(value)


def parse_canonical(data: bytes) -> Any:
    """The value of `data`, refused unless `data` is exactly its canonical form."""
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NotCanonical("not JSON") from None
    if canonical_bytes(value) != data:
        raise NotCanonical("not in RFC 8785 canonical form")
    return value
