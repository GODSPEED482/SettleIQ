"""Alembic environment.

Migrations are hand-written SQL: this schema depends on RLS, deferred composite
foreign keys and constraint triggers, none of which autogenerate can express.
"""

import os

from alembic import context
from sqlalchemy import create_engine


def run_migrations() -> None:
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
