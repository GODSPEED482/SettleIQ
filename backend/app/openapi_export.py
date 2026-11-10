"""Exports the frozen public OpenAPI document: `python -m app.openapi_export > openapi.json`.

`tests/test_openapi.py` fails when the app and the committed `openapi.json` disagree, so a
contract change is always a deliberate, reviewed regeneration (task 1.10).
"""

import json
import sys
from typing import Any

from app.main import create_app


def document() -> dict[str, Any]:
    return create_app().openapi()


def main() -> None:
    sys.stdout.write(json.dumps(document(), indent=2) + "\n")


if __name__ == "__main__":
    main()
