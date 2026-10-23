"""Read ferry.toml, the settings file at the repo root."""

import os
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


@dataclass(frozen=True)
class ModelConfig:
    """One model entry from `[models.<name>]`."""

    name: str
    provider: str
    id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    supports_temperature: bool


@dataclass(frozen=True)
class AssessConfig:
    """Settings under `[assess]`."""

    default_model: str
    max_input_tokens: int


def find_config(start: Path | None = None) -> Path:
    """Look for ferry.toml in `start` (default: current folder) and its parents."""
    folder = (start or Path.cwd()).resolve()
    for candidate in (folder, *folder.parents):
        path = candidate / CONFIG_NAME
        if path.is_file():
            return path
    raise ConfigError(f"{CONFIG_NAME} not found in {folder} or any parent folder")


def read_config(config_path: Path | None = None) -> tuple[Path, dict]:
    """Return the config path and the parsed TOML."""
    path = config_path or find_config()
    with path.open("rb") as file:
        return path, tomllib.load(file)


def load_tool_config(name: str, config_path: Path | None = None) -> ToolConfig:
    """Read the `[tools.<name>]` section of ferry.toml."""
    path, data = read_config(config_path)
    try:
        section = data["tools"][name]
        return ToolConfig(
            command=section["command"],
            args=tuple(section.get("args", [])),
            cwd=path.parent,
        )
    except KeyError as error:
        raise ConfigError(f"{path} has no [tools.{name}] section with a `command`") from error


def load_models(config_path: Path | None = None) -> dict[str, ModelConfig]:
    """Read every `[models.<name>]` section, keyed by name."""
    path, data = read_config(config_path)
    sections = data.get("models") or {}
    if not sections:
        raise ConfigError(f"{path} has no [models] sections")
    models: dict[str, ModelConfig] = {}
    for name, section in sections.items():
        try:
            models[name] = ModelConfig(
                name=name,
                provider=section["provider"],
                id=section["id"],
                input_usd_per_mtok=float(section["input_usd_per_mtok"]),
                output_usd_per_mtok=float(section["output_usd_per_mtok"]),
                supports_temperature=bool(section.get("supports_temperature", False)),
            )
        except KeyError as error:
            raise ConfigError(f"{path} [models.{name}] is missing {error}") from error
    return models


def load_assess_config(config_path: Path | None = None) -> AssessConfig:
    """Read the `[assess]` section of ferry.toml."""
    path, data = read_config(config_path)
    try:
        section = data["assess"]
        return AssessConfig(
            default_model=section["default_model"],
            max_input_tokens=int(section["max_input_tokens"]),
        )
    except KeyError as error:
        raise ConfigError(f"{path} [assess] is missing {error}") from error


def load_dotenv(config_path: Path | None = None) -> None:
    """Fill empty environment variables from the `.env` next to ferry.toml.

    Existing variables win. Values are never printed.
    """
    path, _data = read_config(config_path)
    env_file = path.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if not key or os.environ.get(key):
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
