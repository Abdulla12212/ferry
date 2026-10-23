import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from ferry import __version__
from ferry.assess import AssessError, run_assess, write_report
from ferry.config import ConfigError, find_config, load_assess_config, load_models
from ferry.gateway import GatewayError, complete
from ferry.roslyn import RoslynToolError, get_inventory

PROBE_PROMPT = "What is 2 + 2? Put the sum in answer. Put the word ok in note."
PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer", "description": "The sum 2 + 2."},
        "note": {"type": "string", "description": "The word ok."},
    },
    "required": ["answer", "note"],
}

app = typer.Typer(no_args_is_help=True)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Ferry: migrate .NET Framework apps to .NET 10."""


@app.command()
def inventory(
    repo: Annotated[Path, typer.Argument(help="Folder of the old .NET repo.")],
    risks: Annotated[
        Path | None,
        typer.Option(help="Custom api-risks.json. Default: the built-in list."),
    ] = None,
) -> None:
    """Print the facts about a repo (projects, packages, risky APIs) as JSON."""
    try:
        facts = asyncio.run(get_inventory(repo, risks_path=risks))
    except (ConfigError, RoslynToolError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    typer.echo(json.dumps(facts, indent=2))


@app.command()
def assess(
    repo: Annotated[Path, typer.Argument(help="Folder of the old .NET repo.")],
    model: Annotated[
        str | None,
        typer.Option(help="Model name from ferry.toml. Default: [assess] default_model."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Report folder. Default: .ferry/reports next to ferry.toml."),
    ] = None,
    log: Annotated[
        Path | None,
        typer.Option(help="Call log. Default: .ferry/calls.jsonl next to ferry.toml."),
    ] = None,
) -> None:
    """Read a repo, ask the model what must change for .NET 10, check it, and save the report."""
    try:
        models = load_models()
        settings = load_assess_config()
        name = model or settings.default_model
        if name not in models:
            raise ConfigError(f"no [models.{name}] in ferry.toml (have: {', '.join(models)})")
        facts = asyncio.run(get_inventory(repo))
        result = run_assess(
            facts,
            models[name],
            max_input_tokens=settings.max_input_tokens,
            complete_fn=complete,
            log_path=log,
        )
        json_path, md_path = write_report(
            result, out or find_config().parent / ".ferry" / "reports"
        )
    except (ConfigError, RoslynToolError, AssessError, GatewayError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error

    report = result.report
    typer.echo(f"size={report.get('size')} findings={len(report.get('findings', []))}")
    typer.echo(
        f"checks={'passed' if result.passed else 'FAILED'} attempts={result.attempts} "
        f"risks_shown={result.risks_shown}/{result.risks_total}"
    )
    typer.echo(
        f"{result.model} {result.model_id} tokens={result.input_tokens}/{result.output_tokens} "
        f"time={result.elapsed_s:.2f}s list_cost=${result.cost_usd:.6f}"
    )
    typer.echo(f"saved {json_path}")
    typer.echo(f"saved {md_path}")
    for error in result.errors:
        typer.echo(f"check: {error}", err=True)
    if not result.passed:
        raise typer.Exit(1)


@app.command()
def probe(
    log: Annotated[
        Path | None,
        typer.Option(help="Call log. Default: .ferry/calls.jsonl next to ferry.toml."),
    ] = None,
) -> None:
    """Ask every configured model the same question and print tokens and cost."""
    try:
        models = load_models()
    except ConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error

    failed = False
    for model in models.values():
        try:
            result = complete(model, PROBE_PROMPT, PROBE_SCHEMA, temperature=0, log_path=log)
        except GatewayError as error:
            failed = True
            typer.echo(f"error: {model.name}: {error}", err=True)
            continue
        answer = result.data.get("answer")
        if answer != 4:
            failed = True
            typer.echo(f"error: {model.name}: answer was {answer!r}, expected 4", err=True)
        usage = result.usage
        typer.echo(
            f"{result.model} {result.model_id} answer={answer} "
            f"tokens={usage.input_tokens}/{usage.output_tokens} "
            f"time={usage.elapsed_s:.2f}s list_cost=${usage.cost_usd:.6f}"
        )
    if failed:
        raise typer.Exit(1)
