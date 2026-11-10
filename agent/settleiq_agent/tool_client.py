"""Client for the private tool API, the agent's only route to tenant data (spec 8.2)."""

from typing import Any


class ToolClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool with the delegated token. The server decides what is allowed."""
        raise NotImplementedError("5.7")
