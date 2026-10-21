"""A tiny MCP server that imitates the C# tool, so Python tests need no .NET build."""

import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("fake-roslyn")


@server.tool()
def inventory(repoPath: str, risksPath: str | None = None) -> str:  # noqa: N803
    if repoPath.endswith("broken"):
        raise ToolError("cannot read this repo")
    return json.dumps({"repo": repoPath, "projects": [], "risks": [], "risk_summary": []})


if __name__ == "__main__":
    server.run("stdio")
