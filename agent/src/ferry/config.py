"""Read ferry.toml, the settings file at the repo root."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "ferry.toml"


class ConfigError(Exception):
    """ferry.toml is missing or incomplete."""


@dataclass(frozen=True)
class ToolConfig:
    """How to start an MCP tool as a child process."""

    command: str
    args: tuple[str, ...]
    cwd: Path  # relative paths in args are relative to this folder


def find_config(start: Path | None = None) -> Path:
    """Look for ferry.toml in `start` (default: current folder) and its parents."""
    folder = (start or Path.cwd()).resolve()
    for candidate in (folder, *folder.parents):
        path = candidate / CONFIG_NAME
        if path.is_file():
            return path
    raise ConfigError(f"{CONFIG_NAME} not found in {folder} or any parent folder")


def load_tool_config(name: str, config_path: Path | None = None) -> ToolConfig:
    """Read the `[tools.<name>]` section of ferry.toml."""
    path = config_path or find_config()
    with path.open("rb") as file:
        data = tomllib.load(file)
    try:
        section = data["tools"][name]
        return ToolConfig(
            command=section["command"],
            args=tuple(section.get("args", [])),
            cwd=path.parent,
        )
    except KeyError as error:
        raise ConfigError(f"{path} has no [tools.{name}] section with a `command`") from error
