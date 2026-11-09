"""Statements shared by hand-written migrations. 0001 keeps its own copy because it has shipped."""

from alembic import op

APP_ROLE = "settleiq_app"
TENANT_PREDICATE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def enable_tenant_rls(table: str) -> None:
    """Every tenant-owned table needs this; test_rls.py fails for any table that lacks it."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = {TENANT_PREDICATE}) "
        f"WITH CHECK (tenant_id = {TENANT_PREDICATE})"
    )
