"""The public OpenAPI contract is frozen in backend/openapi.json (task 1.10, spec 12.1)."""

import json
from pathlib import Path

from app.openapi_export import document

FROZEN = Path(__file__).resolve().parents[1] / "openapi.json"


def test_openapi_matches_the_frozen_contract():
    assert document() == json.loads(FROZEN.read_text(encoding="utf-8")), (
        "The OpenAPI contract changed. If that is intended, regenerate it with "
        "`uv run python -m app.openapi_export > openapi.json` and commit the result."
    )


def test_frozen_contract_is_version_1():
    assert json.loads(FROZEN.read_text(encoding="utf-8"))["info"]["version"] == "1"
