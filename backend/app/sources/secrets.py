"""Resolve a source's opaque secret reference within its tenant (spec 11.3, ARCH-03).

Two schemes, both resolved server-side, never by the browser, a model or an agent tool:

- `managed:<NAME>`, read from `<SOURCE_SECRET_DIR>/<tenant_id>/<NAME>`. In production deploy.sh
  renders that read-only directory on the host from SSM Parameter Store
  (`<PARAM_PATH>/source/<tenant_id>/<NAME>`), because containers are deliberately unable to reach
  the instance's AWS credentials. No directory configured means the scheme fails closed.
- `env:SOURCE_SECRET_<NAME>`, read from the process environment. Local runs and CI use it. The
  environment is shared by every tenant of a deployment, so production refuses the scheme.

The tenant comes from the caller's trusted context, never from the reference, so a reference can
only name a secret in its own tenant's namespace: the same `managed:PAYPAL` is a different secret
in every tenant, and a reference cannot be written to reach another tenant's.

A reference that is in neither form, or names an unset or empty secret, fails closed. Neither
the reference nor the value is ever echoed into an error, log or audit row.
"""

import os
import re
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.errors import conflict

_ENV_REFERENCE = re.compile(r"^env:(SOURCE_SECRET_[A-Z0-9_]+)$")
# Upper case and underscores only, so a name can never be a path.
_MANAGED_REFERENCE = re.compile(r"^managed:([A-Z][A-Z0-9_]{0,99})$")


def _managed_value(tenant_id: UUID, name: str) -> str:
    directory = get_settings().source_secret_dir
    if not directory:
        return ""
    try:
        return (Path(directory) / str(tenant_id) / name).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError:
        return ""


def _value(tenant_id: UUID, reference: str | None) -> str:
    if not isinstance(tenant_id, UUID):
        return ""
    if match := _MANAGED_REFERENCE.match(reference or ""):
        return _managed_value(tenant_id, match.group(1))
    if match := _ENV_REFERENCE.match(reference or ""):
        if get_settings().environment == "production":
            return ""
        return os.environ.get(match.group(1), "")
    return ""


def resolve_secret(tenant_id: UUID, reference: str | None) -> str:
    value = _value(tenant_id, reference)
    if not value:
        raise conflict("SECRET_UNRESOLVED", "The source's secret reference cannot be resolved.")
    return value


def is_resolvable(tenant_id: UUID, reference: str | None) -> bool:
    return bool(_value(tenant_id, reference))
