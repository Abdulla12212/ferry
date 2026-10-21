import asyncio
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ferry.cli import app
from ferry.config import ConfigError, ToolConfig, load_tool_config
from ferry.roslyn import RoslynToolError, get_inventory

FAKE_SERVER = Path(__file__).parent / "support" / "fake_roslyn_server.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DLL = (
    REPO_ROOT / "roslyn-tools/src/Ferry.RoslynTools/bin/Release/net10.0/Ferry.RoslynTools.dll"
)
MULTI_FIXTURE = REPO_ROOT / "roslyn-tools/tests/Ferry.RoslynTools.Tests/Fixtures/multi"


@pytest.fixture
def fake_tool(tmp_path: Path) -> ToolConfig:
    return ToolConfig(command=sys.executable, args=(str(FAKE_SERVER),), cwd=tmp_path)


def test_get_inventory_returns_parsed_json(fake_tool: ToolConfig, tmp_path: Path) -> None:
    facts = asyncio.run(get_inventory(tmp_path, tool=fake_tool))

    assert facts["repo"] == str(tmp_path.resolve())
    assert facts["projects"] == []


def test_get_inventory_raises_on_tool_error(fake_tool: ToolConfig, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()

    with pytest.raises(RoslynToolError, match="cannot read this repo"):
        asyncio.run(get_inventory(broken, tool=fake_tool))


def test_get_inventory_rejects_missing_folder(fake_tool: ToolConfig, tmp_path: Path) -> None:
    with pytest.raises(RoslynToolError, match="not a folder"):
        asyncio.run(get_inventory(tmp_path / "missing", tool=fake_tool))


def test_get_inventory_reports_missing_command(tmp_path: Path) -> None:
    tool = ToolConfig(command="ferry-no-such-command", args=(), cwd=tmp_path)

    with pytest.raises(RoslynToolError, match="could not start"):
        asyncio.run(get_inventory(tmp_path, tool=tool))


def test_load_tool_config_reads_ferry_toml(tmp_path: Path) -> None:
    config = tmp_path / "ferry.toml"
    config.write_text('[tools.roslyn]\ncommand = "dotnet"\nargs = ["a.dll", "mcp"]\n')

    tool = load_tool_config("roslyn", config)

    assert tool == ToolConfig(command="dotnet", args=("a.dll", "mcp"), cwd=tmp_path)


def test_load_tool_config_rejects_missing_section(tmp_path: Path) -> None:
    config = tmp_path / "ferry.toml"
    config.write_text("[assess]\n")

    with pytest.raises(ConfigError, match="tools.roslyn"):
        load_tool_config("roslyn", config)


def test_repo_ferry_toml_ends_with_mcp_argument() -> None:
    tool = load_tool_config("roslyn", REPO_ROOT / "ferry.toml")

    assert tool.args[-1] == "mcp"


def test_inventory_command_reports_errors_with_exit_code_2() -> None:
    result = CliRunner().invoke(app, ["inventory", "/ferry-no-such-folder"])

    assert result.exit_code == 2


@pytest.mark.skipif(not REAL_DLL.exists(), reason="build roslyn-tools in Release first")
def test_real_tool_returns_facts() -> None:
    tool = load_tool_config("roslyn", REPO_ROOT / "ferry.toml")

    facts = asyncio.run(get_inventory(MULTI_FIXTURE, tool=tool))

    assert sorted(project["name"] for project in facts["projects"]) == ["Core", "Desktop"]
