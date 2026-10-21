import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from ferry import __version__
from ferry.config import ConfigError
from ferry.roslyn import RoslynToolError, get_inventory

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
