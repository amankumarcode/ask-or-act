"""Command-line interface: a thin shell over askoract.api."""

from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from askoract import __version__, api

app = typer.Typer(
    help="ask-or-act: a SQL data-analyst agent that answers, asks, or declines.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
db_app = typer.Typer(help="Inspect built databases.", no_args_is_help=True)
app.add_typer(db_app, name="db")

console = Console()

SetOption = Annotated[
    list[str] | None,
    typer.Option(
        "--set",
        help="Override config: key.path=value. Prefix 'agent.' to target the agent profile.",
    ),
]
ProfileOption = Annotated[
    str | None, typer.Option("--profile", "-p", help="Agent profile name (configs/agents/).")
]
DatasetOption = Annotated[
    str | None, typer.Option("--dataset", "-d", help="Dataset name (default from config).")
]


def _print_yaml(data: dict, title: str) -> None:
    console.rule(f"[bold]{title}")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)
    console.print(Syntax(text, "yaml", background_color="default"))


def _not_yet(milestone: int) -> NoReturn:
    console.print(f"[yellow]Not implemented yet. This arrives in milestone {milestone}.[/]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the package version."""
    console.print(f"askoract {__version__}")


@config_app.command("show")
def config_show(profile: ProfileOption = None, set_: SetOption = None) -> None:
    """Print the resolved global settings and one agent profile."""
    settings = api.get_settings(set_)
    resolved = api.get_profile(profile, set_, settings=settings)
    _print_yaml(settings.model_dump(mode="json", exclude={"agent_defaults"}), "settings")
    _print_yaml(resolved.model_dump(mode="json"), f"profile: {resolved.name}")


@config_app.command("profiles")
def config_profiles() -> None:
    """List available agent profiles."""
    settings = api.get_settings()
    for name in api.get_profiles(settings):
        p = api.get_profile(name, settings=settings)
        console.print(f"[bold]{name:<12}[/] {p.llm.model:<32} {p.description}")


@app.command()
def setup(
    dataset: DatasetOption = None,
    variant: Annotated[str | None, typer.Option(help="Build one variant only.")] = None,
    force: Annotated[bool, typer.Option(help="Rebuild even if up to date.")] = False,
) -> None:
    """Download source data, inject traps, and build the databases (idempotent)."""
    settings = api.get_settings()
    ds = api.get_dataset(dataset, settings)
    with console.status(f"Building {ds.name} (first run downloads the source)..."):
        reports = api.build_dataset(ds.name, variant, force=force, settings=settings)
    table = Table("dataset", "variant", "status", "traps", "location")
    for r in reports:
        style = "green" if r.status == "built" else "dim"
        traps = ", ".join(r.details.get("traps", [])) or "-"
        location = Path(r.location).relative_to(settings.root, walk_up=True)
        table.add_row(r.dataset, r.variant, f"[{style}]{r.status}[/]", traps, str(location))
    console.print(table)


@db_app.command("info")
def db_info(
    dataset: DatasetOption = None,
    variant: Annotated[str, typer.Option(help="Variant to inspect.")] = "pristine",
) -> None:
    """List the tables of a built variant, with row counts and injected traps."""
    settings = api.get_settings()
    ds = api.get_dataset(dataset, settings)
    db = ds.open(variant)
    try:
        tables = db.list_tables()
    finally:
        db.close()
    table = Table("name", "kind", "rows", title=f"{ds.name}:{variant}  ({ds.location(variant)})")
    for t in tables:
        rows = f"{t.row_count:,}" if t.row_count is not None else "-"
        table.add_row(t.name, t.kind, rows, style="dim" if t.kind == "view" else None)
    console.print(table)
    traps = ds.traps(variant)
    console.print(f"Traps injected: {', '.join(t.id for t in traps) if traps else 'none'}")


@db_app.command("traps")
def db_traps(dataset: DatasetOption = None) -> None:
    """Document every trap the dataset can inject."""
    ds = api.get_dataset(dataset)
    for variant in ds.variants:
        for trap in ds.traps(variant):
            console.rule(f"[bold]{trap.id}[/]  (variant: {variant})")
            console.print(f"[bold]Injects:[/] {trap.summary}")
            console.print(f"[bold]Correct behavior:[/] {trap.correct_behavior}")


@app.command()
def ask(question: str, profile: ProfileOption = None, set_: SetOption = None) -> None:
    """Ask the agent one question."""
    _not_yet(4)


@app.command("eval")
def eval_(profile: Annotated[list[str] | None, typer.Option("--profile", "-p")] = None) -> None:
    """Run test suites for one or more profiles and write the results table."""
    _not_yet(6)


@app.command()
def demo() -> None:
    """Run the showcase questions: baseline vs the full agent."""
    _not_yet(8)


@app.command()
def trace(run_id: str = "latest", case_id: str | None = None) -> None:
    """Show the step-by-step trace of one case."""
    _not_yet(8)


if __name__ == "__main__":
    app()
