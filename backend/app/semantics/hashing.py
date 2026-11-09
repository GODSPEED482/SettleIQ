"""Content hashes over RFC 8785 canonical JSON, so key order cannot change a hash (POL-12)."""

import hashlib
from typing import Any

import rfc8785


def content_hash(content: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(content)).hexdigest()
