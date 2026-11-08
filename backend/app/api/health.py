from fastapi import APIRouter

from app.authz.guard import public

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(_: None = public()) -> dict[str, str]:
    return {"status": "ok"}
