"""Private Worker API, served as a separate process with no published port (AC-AUTHZ-17)."""

from fastapi import APIRouter, FastAPI

from app.authz.guard import TenantContext, require
from app.bootstrap import finish_app
from app.errors import not_implemented

router = APIRouter(tags=["internal"])


# TODO(G5 5.6 Rahul): run one execution under its grant (POL-09). Refuse a human principal
# before any side effect (AC-AUTHZ-17). Consume the grant and move the execution to EXECUTING
# in one transaction; reuse, expiry, or an action, target, decision or approval mismatch fails
# closed (AC-AUTHZ-08). Re-check the exact-scope mode and POL-14, including this container's
# own image digest (spec 9.7.6 check 5). Persist each attempt before the side effect, with
# tenant, sequence, idempotency key, connector, action, target, evidence, status and outcome
# (AC-PLY-10). Then VERIFYING: re-read through merchant.payment.read under outcome.verify and
# resolve the case only when it reads COMPLETED. API success is never resolution (AC-PLY-06).
# TODO(G5 5.15 Rahul): the refunds.create path on the same grant and attempt rules. An
# ambiguous outcome (timeout, network drop, 5xx) goes to OUTCOME_UNKNOWN and is settled by
# refunds.read and captures.read, never by a retry under a new key; a retry reuses the same
# PayPal-Request-Id. Resolve only after readback shows the refund COMPLETED against the exact
# duplicate capture (AC-PP-08).
@router.post("/internal/executions/{execution_id}/run")
def run_execution(execution_id: str, ctx: TenantContext = require("playbook.execute")) -> None:
    raise not_implemented("5.6")


def create_worker_app() -> FastAPI:
    app = FastAPI(title="SettleIQ Worker", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.include_router(router)
    return finish_app(app)


app = create_worker_app()
