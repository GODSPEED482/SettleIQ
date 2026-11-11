"""Private Worker API, served as a separate process with no published port (AC-AUTHZ-17).

The only credential this app reads is an execution grant (POL-09): `authenticate` takes the
bearer token instead of a session cookie here, so a human principal is refused before any side
effect. The api hands each grant over through `WORKER_URL` right after issuing it.
"""

from uuid import UUID

from fastapi import APIRouter, FastAPI

from app.authz.guard import TenantContext, require
from app.bootstrap import finish_app
from app.execution import worker

router = APIRouter(tags=["internal"])


# TODO(G5 5.15 Rahul): the refunds.create path on the same grant and attempt rules. An
# ambiguous outcome (timeout, network drop, 5xx) goes to OUTCOME_UNKNOWN and is settled by
# refunds.read and captures.read, never by a retry under a new key; a retry reuses the same
# PayPal-Request-Id. Resolve only after readback shows the refund COMPLETED against the exact
# duplicate capture (AC-PP-08).
@router.post("/internal/executions/{execution_id}/run")
def run_execution(execution_id: UUID, ctx: TenantContext = require("playbook.execute")) -> dict:
    """Consume the grant, act once, verify by readback and return the execution."""
    return worker.run(ctx, execution_id)


def create_worker_app() -> FastAPI:
    app = FastAPI(title="SettleIQ Worker", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.grant_authentication = True
    app.include_router(router)
    return finish_app(app)


app = create_worker_app()
