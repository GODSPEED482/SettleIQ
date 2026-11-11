"""Packaged verifier public keys (spec 9.7.4).

Keys come only from keys.json inside the image: no environment variable or API can add one.
Each key has a validity window of at most 90 days. An empty file means nothing verifies, so
every scope stays SHADOW until a key is provisioned (see README, "Release attestation").
"""

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path

KEYS_FILE = Path(__file__).with_name("keys.json")
MAX_KEY_LIFETIME = timedelta(days=90)


class KeyFileError(ValueError):
    pass


@dataclass(frozen=True)
class PublicKey:
    key_id: str
    public_key: bytes
    not_before: datetime
    not_after: datetime

    def valid_at(self, moment: datetime) -> bool:
        return self.not_before <= moment < self.not_after


def parse_keys(data: str) -> dict[str, PublicKey]:
    keys = {}
    for item in json.loads(data)["keys"]:
        key = PublicKey(
            item["key_id"],
            base64.b64decode(item["public_key"], validate=True),
            datetime.fromisoformat(item["not_before"]),
            datetime.fromisoformat(item["not_after"]),
        )
        if len(key.public_key) != 32:
            raise KeyFileError(f"{key.key_id}: not an Ed25519 public key")
        if key.not_before.tzinfo is None or key.not_after.tzinfo is None:
            raise KeyFileError(f"{key.key_id}: validity must be in UTC")
        if not key.not_before < key.not_after <= key.not_before + MAX_KEY_LIFETIME:
            raise KeyFileError(f"{key.key_id}: validity window over 90 days")
        if key.key_id in keys:
            raise KeyFileError(f"duplicate key {key.key_id}")
        keys[key.key_id] = key
    return keys


@cache
def packaged_keys() -> dict[str, PublicKey]:
    return parse_keys(KEYS_FILE.read_text(encoding="utf-8"))
