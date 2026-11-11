"""STUB internal MCP server (STDIO).

It stands in for your real change-management and policy tools. All data is
fictional and lives in ../data/*.json. Every tool is read-only and says so in
its annotations.
"""
import json
import re
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

DATA = Path(__file__).resolve().parent.parent / "data"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

server = MCPServer(
    "change-policy",
    version="0.1.0",
    instructions=(
        "Read-only change-management tools. For a risk assessment: call get_change_request first, "
        "then get_service_profile for its service, then search_policies for each relevant topic. "
        "Cite policy ids exactly as returned. Never invent a policy."
    ),
)


def load(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text())


@server.tool(title="Get change request", annotations=READ_ONLY)
def get_change_request(change_id: str) -> str:
    """Fetch one change request by id (for example CHG-1042)."""
    for change in load("changes.json"):
        if change["id"] == change_id:
            return json.dumps(change, indent=2)
    raise ToolError(f"No change request {change_id}")


@server.tool(title="Get service profile", annotations=READ_ONLY)
def get_service_profile(service: str) -> str:
    """Fetch tier, owning team and freeze windows for a service."""
    for profile in load("services.json"):
        if profile["service"] == service:
            return json.dumps(profile, indent=2)
    raise ToolError(f"Unknown service {service}")


@server.tool(title="Search change policies", annotations=READ_ONLY)
def search_policies(query: str) -> str:
    """Keyword search over internal change-management policies. Returns matching policies with ids."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    hits = []
    for policy in load("policies.json"):
        haystack = f"{policy['title']} {policy['rule']} {' '.join(policy['applies_to'])}".lower()
        if any(w in haystack for w in words):
            hits.append(policy)
    return json.dumps(hits, indent=2)


if __name__ == "__main__":
    server.run("stdio")
