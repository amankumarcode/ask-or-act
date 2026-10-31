"""Command-line interface: a thin shell over askoract.api."""

from typing import Annotated, NoReturn

import typer
import yaml
from rich.console import Console
from rich.syntax import Syntax

from askoract import __version__, api

app = typer.Typer(
    help="ask-or-act: a SQL data-analyst agent that answers, asks, or declines.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

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
def setup() -> None:
    """Download source data, inject traps, and build the databases."""
    _not_yet(2)


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
