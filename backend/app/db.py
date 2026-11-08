"""Database access with transaction-local tenant context (spec 11.1, AC-TEN-03).

Handlers use `tenant_tx`, which only accepts a TenantContext built by the route guard,
so a raw browser-supplied ID cannot open a tenant transaction.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from app.config import get_settings

if TYPE_CHECKING:
    from app.authz.guard import TenantContext


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def global_tx() -> Iterator[Connection]:
    """Transaction with no tenant context. RLS hides every tenant-owned row."""
    with get_engine().begin() as conn:
        yield conn


@contextmanager
def scoped_tx(tenant_id: UUID) -> Iterator[Connection]:
    """Set tenant context for one transaction. The caller must have established trust in
    tenant_id: a membership check, or a signature-verified source lookup (POL-15)."""
    with get_engine().begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield conn


@contextmanager
def tenant_tx(ctx: "TenantContext") -> Iterator[Connection]:
    with scoped_tx(ctx.tenant_id) as conn:
        yield conn
