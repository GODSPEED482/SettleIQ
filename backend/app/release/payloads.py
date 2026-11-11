"""Attestation and revocation-state payloads (spec 9.7.3, 9.7.5) and the packaged suites.

The models only give shape to bytes that were already verified and parsed canonically; nothing
here is ever re-serialized for a signature. A V1 attestation certifies exactly one fully named
scope: zero or several scopes, a wildcard or a missing tenant is refused (AC-REL-05).
"""

from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ATTESTATION_SCHEMA = "settleiq.release-attestation/1"
REVOCATION_SCHEMA = "settleiq.revocation-state/1"
SHA256 = r"^[0-9a-f]{64}$"
DIGEST = r"^sha256:[0-9a-f]{64}$"
NAME = r"^[a-z][a-z0-9_.-]*$"
SUITES_FILE = Path(__file__).with_name("suites.yaml")
CATEGORIES = (
    "authorization_enforcement", "tenant_isolation", "transitions", "idempotency",
    "concurrency_and_stale_state", "failure_recovery", "shadow_side_effect_prevention",
    "post_action_verification",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must be UTC")
    return value


class Subject(_Strict):
    image: str = Field(min_length=1)
    digest: str = Field(pattern=DIGEST)


class Build(_Strict):
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    run_id: str = Field(min_length=1)
    workflow: str = Field(min_length=1)


class Dependency(_Strict):
    capability: str = Field(pattern=NAME)
    version: str = Field(min_length=1)


class CertifiedScope(_Strict):
    tenant_id: UUID
    domain_pack_id: str = Field(pattern=NAME)
    domain_pack_version: str = Field(min_length=1)
    connector_id: str = Field(pattern=NAME)
    connector_version: str = Field(min_length=1)
    capability: str = Field(pattern=NAME)
    capability_version: str = Field(min_length=1)
    contract_version: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    implementation_sha256: str = Field(pattern=SHA256)
    dependencies: list[Dependency]

    @field_validator("domain_pack_version", "connector_version", "capability_version")
    @classmethod
    def _no_wildcard(cls, value: str) -> str:
        if "*" in value or value.strip() != value:
            raise ValueError("wildcards are not allowed")
        return value


class TestSuite(_Strict):
    suite_id: str = Field(pattern=NAME)
    version: int = Field(ge=1)
    result: Literal["PASS", "FAIL"]
    harness_digest: str = Field(pattern=DIGEST)
    evidence_sha256: str = Field(pattern=SHA256)
    skipped: int = Field(ge=0)


class Attestation(_Strict):
    schema_: Literal["settleiq.release-attestation/1"] = Field(alias="schema")
    attestation_id: UUID
    subject: Subject
    build: Build
    capability_manifest_sha256: str = Field(pattern=SHA256)
    certified_scopes: list[CertifiedScope] = Field(min_length=1, max_length=1)
    test_suite: TestSuite
    issued_at: datetime
    expires_at: datetime
    key_id: str = Field(min_length=1)

    _issued = field_validator("issued_at", "expires_at")(_utc)

    @property
    def scope(self) -> CertifiedScope:
        return self.certified_scopes[0]


class RevocationState(_Strict):
    schema_: Literal["settleiq.revocation-state/1"] = Field(alias="schema")
    sequence: int = Field(ge=1)
    previous_sha256: str = Field(pattern=r"^([0-9a-f]{64})?$")
    issued_at: datetime
    expires_at: datetime
    revoked: list[UUID]
    key_id: str = Field(min_length=1)

    _issued = field_validator("issued_at", "expires_at")(_utc)


class Suite(_Strict):
    capability: str
    connector_id: str
    suite_id: str
    version: int
    categories: dict[str, list[str]]

    @field_validator("categories")
    @classmethod
    def _all_categories(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if tuple(value) != CATEGORIES:
            raise ValueError(f"categories must be exactly {CATEGORIES}")
        return value

    def empty_categories(self) -> list[str]:
        return [name for name, tests in self.categories.items() if not tests]


def parse_suites(text: str) -> dict[tuple[str, str], Suite]:
    suites = [Suite(**item) for item in yaml.safe_load(text)["suites"]]
    return {(s.connector_id, s.capability): s for s in suites}


@cache
def packaged_suites() -> dict[tuple[str, str], Suite]:
    return parse_suites(SUITES_FILE.read_text(encoding="utf-8"))
