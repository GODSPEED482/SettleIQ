"""Opaque secret references for source connections (spec 11.3, ARCH-03)."""

from uuid import uuid4

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.sources.secrets import is_resolvable, resolve_secret

TENANT = uuid4()
OTHER_TENANT = uuid4()


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "postgresql://reader@db/merchant")
    assert resolve_secret(TENANT, "env:SOURCE_SECRET_MERCHANT_DB") == "postgresql://reader@db/merchant"


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
        resolve_secret(TENANT, reference)
    assert caught.value.code == "SECRET_UNRESOLVED"
    assert "postgresql" not in caught.value.message
    assert "SOURCE_SECRET" not in caught.value.message


def test_empty_variable_fails_closed(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "")
    with pytest.raises(ApiError):
        resolve_secret(TENANT, "env:SOURCE_SECRET_MERCHANT_DB")


def test_production_refuses_the_shared_environment(monkeypatch):
    # Every tenant of a deployment shares its environment, so it cannot hold a tenant's secret.
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "postgresql://reader@db/merchant")
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        assert not is_resolvable(TENANT, "env:SOURCE_SECRET_MERCHANT_DB")
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("tenant", [None, "", str(TENANT), "../"])
def test_resolution_needs_a_tenant(monkeypatch, tenant):
    monkeypatch.setenv("SOURCE_SECRET_MERCHANT_DB", "postgresql://reader@db/merchant")
    assert not is_resolvable(tenant, "env:SOURCE_SECRET_MERCHANT_DB")


@pytest.fixture
def managed_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _render(directory, tenant, name: str, value: str) -> None:
    (directory / str(tenant)).mkdir(exist_ok=True)
    (directory / str(tenant) / name).write_text(value, encoding="utf-8")


def test_managed_reference_resolves_from_the_tenants_directory(managed_dir):
    _render(managed_dir, TENANT, "PAYPAL_SANDBOX", "client:secret\n")
    assert resolve_secret(TENANT, "managed:PAYPAL_SANDBOX") == "client:secret"
    assert is_resolvable(TENANT, "managed:PAYPAL_SANDBOX")


def test_the_same_name_is_a_different_secret_in_each_tenant(managed_dir):
    _render(managed_dir, TENANT, "PAYPAL_SANDBOX", "tenant-a")
    _render(managed_dir, OTHER_TENANT, "PAYPAL_SANDBOX", "tenant-b")
    assert resolve_secret(TENANT, "managed:PAYPAL_SANDBOX") == "tenant-a"
    assert resolve_secret(OTHER_TENANT, "managed:PAYPAL_SANDBOX") == "tenant-b"


def test_a_tenant_cannot_reach_another_tenants_secret(managed_dir):
    _render(managed_dir, TENANT, "PAYPAL_SANDBOX", "client:secret")
    # The flat layout from before namespacing is no tenant's secret.
    (managed_dir / "SHARED").write_text("platform:secret", encoding="utf-8")
    for reference in (
        "managed:PAYPAL_SANDBOX",
        f"managed:{TENANT}/PAYPAL_SANDBOX",
        "managed:../PAYPAL_SANDBOX",
        "managed:SHARED",
    ):
        assert not is_resolvable(OTHER_TENANT, reference)
    assert not is_resolvable(TENANT, "managed:SHARED")


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
    _render(managed_dir, TENANT, "PAYPAL_SANDBOX", "client:secret")
    _render(managed_dir, TENANT, "EMPTY", "\n")
    assert not is_resolvable(TENANT, reference)
    with pytest.raises(ApiError) as caught:
        resolve_secret(TENANT, reference)
    assert caught.value.code == "SECRET_UNRESOLVED"
    assert "client" not in caught.value.message


def test_managed_scheme_is_closed_without_a_directory(monkeypatch, tmp_path):
    _render(tmp_path, TENANT, "PAYPAL_SANDBOX", "client:secret")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOURCE_SECRET_DIR", "")
    get_settings.cache_clear()
    try:
        assert not is_resolvable(TENANT, "managed:PAYPAL_SANDBOX")
    finally:
        get_settings.cache_clear()
