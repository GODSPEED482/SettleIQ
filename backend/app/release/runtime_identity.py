"""The running image digest, from the trusted runtime-identity service (spec 9.7.6, check 5).

Only the runtime-identity container can reach the container runtime. It answers with the
repository digest of the container at the caller's network address, so the caller cannot name
its own digest: nothing from a request header, body or environment variable is consulted
(AC-REL-09). Unreachable, or no digest (a locally built image has none), is a denial.
"""

import re

import httpx

from app.config import get_settings

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TIMEOUT_SECONDS = 3


def running_digest() -> str | None:
    try:
        response = httpx.get(f"{get_settings().runtime_identity_url}/identity",
                             timeout=TIMEOUT_SECONDS)
        digest = response.json().get("digest") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return digest if isinstance(digest, str) and DIGEST.match(digest) else None
