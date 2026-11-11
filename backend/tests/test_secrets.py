"""Opaque secret references for source connections (spec 11.3)."""

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.sources.secrets import is_resolvable, resolve_secret


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


@pytest.fixture
def managed_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_managed_reference_resolves_from_the_rendered_directory(managed_dir):
    (managed_dir / "PAYPAL_SANDBOX").write_text("client:secret\n", encoding="utf-8")
    assert resolve_secret("managed:PAYPAL_SANDBOX") == "client:secret"
    assert is_resolvable("managed:PAYPAL_SANDBOX")


@pytest.mark.parametrize(
    "reference",
    [
        "managed:MISSING",
        "managed:paypal_sandbox",
        "managed:../PAYPAL_SANDBOX",
        "managed:PAYPAL/SANDBOX",
        "managed:",
        "managed:EMPTY",
    ],
)
def test_other_managed_references_fail_closed(managed_dir, reference):
    (managed_dir / "PAYPAL_SANDBOX").write_text("client:secret", encoding="utf-8")
    (managed_dir / "EMPTY").write_text("\n", encoding="utf-8")
    assert not is_resolvable(reference)
    with pytest.raises(ApiError) as caught:
        resolve_secret(reference)
    assert caught.value.code == "SECRET_UNRESOLVED"
    assert "client" not in caught.value.message


def test_managed_scheme_is_closed_without_a_directory(monkeypatch, tmp_path):
    (tmp_path / "PAYPAL_SANDBOX").write_text("client:secret", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOURCE_SECRET_DIR", "")
    get_settings.cache_clear()
    try:
        assert not is_resolvable("managed:PAYPAL_SANDBOX")
    finally:
        get_settings.cache_clear()
