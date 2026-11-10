from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.authz.guard import public
from app.db import global_tx
from app.errors import not_ready

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(_: None = public()) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(_: None = public()) -> dict[str, str]:
    """Liveness says the process runs; this says it can serve, so it checks the database."""
    try:
        with global_tx() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise not_ready() from None
    return {"status": "ready"}
