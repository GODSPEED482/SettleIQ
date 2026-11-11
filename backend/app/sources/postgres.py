"""Read-only PostgreSQL access for merchant sources (spec 4.4.1, 10.3).

Discovery and record reads share one connection policy: the source is reached outside any
tenant transaction, with a short connect timeout, a statement timeout and a read-only
transaction, so a slow or hostile source cannot hold a platform transaction open or accept
a write.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

from app.errors import conflict

STATEMENT_TIMEOUT_MS = 30_000
CONNECT_TIMEOUT_S = 10


def read_only_engine(secret: str) -> Engine:
    """An engine pinned to a read-only PostgreSQL session, or a non-enumerating failure."""
    try:
        url = make_url(secret)
    except ArgumentError:
        raise conflict("SOURCE_UNREACHABLE", "The source could not be reached.") from None
    if url.get_backend_name() != "postgresql":
        raise conflict("SOURCE_UNREACHABLE", "The source could not be reached.")
    return create_engine(
        url.set(drivername="postgresql+psycopg"),
        poolclass=NullPool,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"
            ),
        },
    )
