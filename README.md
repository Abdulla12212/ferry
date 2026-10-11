# Ferry

An open-source AI agent that migrates .NET Framework apps to .NET 10 and checks the result.

Status: Phase 0 (foundations).

## Layout

- `agent/`: the Python agent and the `ferry` command
- `roslyn-tools/`: the C# tool that reads .NET code with Roslyn
- `docs/`: decisions and the learning log
- `ferry.toml`: model names, prices and settings

## Setup

Requires uv and the .NET 10 SDK (`brew install uv dotnet`).

```bash
cp .env.example .env    # add your API keys

cd agent
uv sync
uv run ferry --version
uv run pytest

cd ../roslyn-tools
dotnet test
```
