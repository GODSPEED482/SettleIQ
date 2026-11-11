"""Resolve a source's opaque secret reference (spec 11.3).

Two schemes, both resolved server-side, never by the browser, a model or an agent tool:

- `env:SOURCE_SECRET_<NAME>`, read from the process environment. Local runs and CI use it.
- `managed:<NAME>`, read from `<SOURCE_SECRET_DIR>/<NAME>`. In production deploy.sh renders
  that read-only directory on the host from SSM Parameter Store (`<PARAM_PATH>/source/<NAME>`),
  because containers are deliberately unable to reach the instance's AWS credentials.
  No directory configured means the scheme fails closed.

A reference that is in neither form, or names an unset or empty secret, fails closed. Neither
the reference nor the value is ever echoed into an error, log or audit row.
"""

import os
import re
from pathlib import Path

from app.config import get_settings
from app.errors import conflict

_ENV_REFERENCE = re.compile(r"^env:(SOURCE_SECRET_[A-Z0-9_]+)$")
# Upper case and underscores only, so a name can never be a path.
_MANAGED_REFERENCE = re.compile(r"^managed:([A-Z][A-Z0-9_]{0,99})$")


def _managed_value(name: str) -> str:
    directory = get_settings().source_secret_dir
    if not directory:
        return ""
    try:
        return (Path(directory) / name).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError:
        return ""


def _value(reference: str | None) -> str:
    if match := _ENV_REFERENCE.match(reference or ""):
        return os.environ.get(match.group(1), "")
    if match := _MANAGED_REFERENCE.match(reference or ""):
        return _managed_value(match.group(1))
    return ""


def resolve_secret(reference: str | None) -> str:
    value = _value(reference)
    if not value:
        raise conflict("SECRET_UNRESOLVED", "The source's secret reference cannot be resolved.")
    return value


def is_resolvable(reference: str | None) -> bool:
    return bool(_value(reference))
