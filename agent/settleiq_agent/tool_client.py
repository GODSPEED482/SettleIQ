"""Client for the private tool API, the agent's only route to tenant data (spec 8.2)."""

from typing import Any

import httpx

TOOL_PREFIX = "/internal/agent-tools"
TIMEOUT_SECONDS = 30


class ToolAborted(RuntimeError):
    """The tool API no longer accepts this run's token, or cannot be reached."""


class ToolClient:
    def __init__(self, base_url: str, token: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool with the delegated token. The server decides what is allowed.

        A refusal (missing permission, out-of-scope argument) comes back as an `error` the model
        can read; a rejected token or an unreachable API ends the run.
        """
        try:
            with httpx.Client(transport=self.transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.post(
                    f"{self.base_url}{TOOL_PREFIX}/{tool_name}", json=arguments,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as error:
            raise ToolAborted(type(error).__name__) from None
        if response.status_code == 401 or response.status_code >= 500:
            raise ToolAborted(f"tool API returned {response.status_code}")
        body = response.json()
        if response.status_code != 200:
            return {"error": body.get("error", {}).get("code", str(response.status_code))}
        return body
