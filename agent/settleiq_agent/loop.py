"""Function-calling investigation loop (spec 8.1). Owner: Swapnaneel, task 5.7."""

from uuid import UUID

from settleiq_agent.tool_client import ToolClient

# Draft limits, to be tuned under task 5.7. Evidence text is untrusted, so a run must always end.
MAX_STEPS = 20
DEADLINE_SECONDS = 120


def run_investigation(investigation_id: UUID, case_id: UUID, tools: ToolClient) -> dict:
    """Investigate one case and return output matching the spec 8.3 contract."""
    raise NotImplementedError("5.7")
