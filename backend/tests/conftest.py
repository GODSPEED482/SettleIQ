"""Shared fixtures.

`db` tests need a migrated, seeded PostgreSQL. Locally they are skipped when none is
reachable. With REQUIRE_DB_TESTS set (CI), an unreachable database fails the run, so
tenant-isolation tests can never pass by being skipped.
"""

import os

import pytest
from sqlalchemy import text


def _database_reachable() -> bool:
    try:
        from app.db import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1 FROM permissions LIMIT 1"))
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    db_items = [item for item in items if item.get_closest_marker("db")]
    if not db_items or _database_reachable():
        return
    if os.environ.get("REQUIRE_DB_TESTS"):
        raise pytest.UsageError("REQUIRE_DB_TESTS is set but the database is not reachable")
    skip = pytest.mark.skip(reason="no migrated PostgreSQL reachable at DATABASE_URL")
    for item in db_items:
        item.add_marker(skip)


@pytest.fixture
def paypal_signer(monkeypatch):
    """Signs webhook deliveries with PayPal's algorithm; see tests/paypal_signing.py."""
    from tests.paypal_signing import install

    return install(monkeypatch)
