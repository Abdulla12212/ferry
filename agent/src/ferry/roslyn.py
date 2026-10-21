"""Start the C# code reader as an MCP server and ask it for the facts about a repo."""

import json
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from ferry.config import ToolConfig, load_tool_config


class RoslynToolError(Exception):
    """The C# tool could not be started, or it reported an error."""


async def get_inventory(
    repo_path: Path,
    tool: ToolConfig | None = None,
    risks_path: Path | None = None,
) -> dict[str, Any]:
    """Return the parsed `inventory` JSON for `repo_path`."""
    tool = tool or load_tool_config("roslyn")
    repo = repo_path.expanduser().resolve()
    if not repo.is_dir():
        raise RoslynToolError(f"not a folder: {repo}")

    arguments: dict[str, Any] = {"repoPath": str(repo)}
    if risks_path is not None:
        arguments["risksPath"] = str(risks_path.expanduser().resolve())

    server = StdioServerParameters(command=tool.command, args=list(tool.args), cwd=tool.cwd)
    try:
        async with Client(server) as client:
            result = await client.call_tool("inventory", arguments)
    except OSError as error:
        raise RoslynToolError(f"could not start {tool.command}: {error}") from error

    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        raise RoslynToolError(text or "the inventory tool failed")
    return json.loads(text)
