"""SaaS identity policy entrypoint. The policy itself is data (resolution.yaml)."""

from functools import cache
from pathlib import Path

import yaml

POLICY_FILE = Path(__file__).with_name("resolution.yaml")


@cache
def _load() -> dict:
    return yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))


def exact_id_policy() -> dict:
    return _load()
