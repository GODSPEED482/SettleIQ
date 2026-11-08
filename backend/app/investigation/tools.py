"""Agent tool registry (spec 8.2). Every tool declares one permission from the outset.

Domain-specific tools such as settlement calculations come from pack manifests.
"""

from collections.abc import Mapping

from app.authz.catalogue import Catalogue
from app.domain_packs.manifest import AgentTool, DomainPackManifest

CORE_TOOLS = (
    AgentTool(name="get_ontology_context", permission="ontology.read"),
    AgentTool(name="get_source_authority", permission="authority.read"),
    AgentTool(name="get_source_records", permission="source_record.read"),
    AgentTool(name="trace_domain_relationships", permission="entity.read"),
    AgentTool(name="get_case_evidence", permission="case.read"),
    AgentTool(name="get_property_observations", permission="entity.read"),
    AgentTool(name="get_entity_resolution_trace", permission="entity_resolution.read"),
    AgentTool(name="classify_domain_case", permission="case.classify"),
    AgentTool(name="recommend_playbook", permission="playbook.recommend"),
    AgentTool(name="validate_playbook", permission="playbook.validate"),
)

# Matched per word, so "create_playbook_request" and "approve_refund" both fail.
PROHIBITED_WORDS = {"request", "approval", "approve", "reject", "execute"}


class ToolRegistryError(ValueError):
    pass


def validate_tools(tools: list[AgentTool], catalogue: Catalogue) -> None:
    seen = set()
    for tool in tools:
        if tool.name in seen:
            raise ToolRegistryError(f"duplicate agent tool: {tool.name}")
        seen.add(tool.name)
        if PROHIBITED_WORDS & set(tool.name.split("_")):
            raise ToolRegistryError(f"prohibited agent tool: {tool.name}")
        if "agent" not in catalogue.get(tool.permission).principal_types:
            raise ToolRegistryError(f"{tool.name}: {tool.permission} is not agent-permitted")


def build_tool_registry(
    packs: Mapping[str, DomainPackManifest], catalogue: Catalogue
) -> list[AgentTool]:
    tools = list(CORE_TOOLS)
    for pack in packs.values():
        tools.extend(pack.agent_tools)
    validate_tools(tools, catalogue)
    return tools
