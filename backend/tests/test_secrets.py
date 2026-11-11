"""Opaque secret references for source connections (spec 11.3)."""

import pytest

from app.errors import ApiError
from app.sources.secrets import resolve_secret


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "postgresql://reader@db/merchant")
    assert resolve_secret("env:SOURCE_SECRET_MERCHANT_DB") == "postgresql://reader@db/merchant"


@pytest.mark.parametrize(
    "reference",
    [
        None,
        "",
        "SOURCE_SECRET_MERCHANT_DB",
        "env:DATABASE_URL",
        "env:source_secret_merchant_db",
        "vault:SOURCE_SECRET_MERCHANT_DB",
        "env:SOURCE_SECRET_UNSET",
    ],
)
def test_other_references_fail_closed(monkeypatch, reference):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@db/settleiq")
    monkeypatch.delenv("SOURCE_SECRET_UNSET", raising=False)
    with pytest.raises(ApiError) as caught:
        resolve_secret(reference)
    assert caught.value.code == "SECRET_UNRESOLVED"
    assert "postgresql" not in caught.value.message
    assert "SOURCE_SECRET" not in caught.value.message


def test_empty_variable_fails_closed(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "")
    with pytest.raises(ApiError):
        resolve_secret("env:SOURCE_SECRET_MERCHANT_DB")
