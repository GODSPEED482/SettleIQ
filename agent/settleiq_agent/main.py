"""Run endpoint called by the backend dispatcher. The service has no published port."""

from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

app = FastAPI(title="SettleIQ Agent", version="0.1.0", docs_url=None, redoc_url=None,
              openapi_url=None)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    case_id: UUID
    tool_token: str


@app.post("/run")
def run(request: RunRequest) -> dict:
    """Run one investigation and return untrusted output for the dispatcher to validate."""
    raise HTTPException(status_code=501, detail="Not implemented yet (task 5.7).")
