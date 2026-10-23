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
cp .env.example .env    # add a free Gemini API key from https://aistudio.google.com/apikey

cd agent
uv sync
uv run ferry --version
uv run pytest

cd ../roslyn-tools
dotnet test
```

## Try the code reader

```bash
cd roslyn-tools
dotnet build -c Release
dotnet src/Ferry.RoslynTools/bin/Release/net10.0/Ferry.RoslynTools.dll inventory path/to/old-dotnet-repo
```

It prints JSON (projects, packages, risky APIs with file and line) to stdout and warnings to stderr.
The risky-API list is `roslyn-tools/src/Ferry.RoslynTools/api-risks.json`.

## Try the code reader as an MCP tool

```bash
cd roslyn-tools
dotnet build -c Release
npx @modelcontextprotocol/inspector dotnet src/Ferry.RoslynTools/bin/Release/net10.0/Ferry.RoslynTools.dll mcp
```

In MCP Inspector, open the Tools tab, pick `inventory`, set `repoPath` to an absolute folder path, and run it.
Start the tool from the built `.dll`, not `dotnet run`, because build messages would break MCP.

## Call the code reader from Python

```bash
cd roslyn-tools && dotnet build -c Release && cd ..
cd agent
uv run ferry inventory path/to/old-dotnet-repo
```

Python starts the C# tool from the command in `ferry.toml` (`[tools.roslyn]`), asks it for the facts over MCP, and prints the JSON.

## Try the model call

`ferry probe` asks every model in `ferry.toml` the same question (`2 + 2`), checks the JSON, prints tokens and list-price cost, and appends a line to `.ferry/calls.jsonl`.

```bash
cd agent
uv run ferry probe
```

Both models are Gemini on the free tier (`gemini-3.6-flash` and `gemini-3.5-flash-lite`). The printed cost is the published paid rate. The free tier does not bill it. Automated tests use a fake response and do not call Gemini.
