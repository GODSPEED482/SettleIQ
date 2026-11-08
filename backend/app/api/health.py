from fastapi import APIRouter

from app.authz.guard import public

router = APIRouter(tags=["health"])


# TODO(G1 1.1 Rishav): add a readiness check that queries the database; this ignores DB access.
@router.get("/healthz")
def healthz(_: None = public()) -> dict[str, str]:
    return {"status": "ok"}
