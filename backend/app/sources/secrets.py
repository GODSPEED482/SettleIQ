"""Resolve a source's opaque secret reference (spec 11.3).

The only scheme in this build is `env:SOURCE_SECRET_<NAME>`, read from the process environment,
which deployment fills from its secret store. Task 4.5 adds managed secret references; until
then a reference that is not in this form, or names an unset variable, fails closed. Neither
the reference nor the value is ever echoed into an error, log or audit row.
"""

import os
import re

from app.errors import conflict

# TODO(G4 4.5 Rishav): add the managed reference scheme the credentials route writes, resolved
# server-side from the deployment secret store. Values never reach logs, audit rows, browser
# payloads or agent tools. Keep the env: scheme for local runs and test_secrets.py green.
_ENV_REFERENCE = re.compile(r"^env:(SOURCE_SECRET_[A-Z0-9_]+)$")


def resolve_secret(reference: str | None) -> str:
    match = _ENV_REFERENCE.match(reference or "")
    value = os.environ.get(match.group(1), "") if match else ""
    if not value:
        raise conflict("SECRET_UNRESOLVED", "The source's secret reference cannot be resolved.")
    return value
