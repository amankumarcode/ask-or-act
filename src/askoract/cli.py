"""Command-line interface: a thin shell over askoract.api."""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from askoract import __version__, api
from askoract.agent.types import Outcome
from askoract.datasets.sqlite_file import DatasetBuildError
from askoract.eval.scorer import CaseScore
from askoract.llm.client import LLMError
from askoract.tracing.console import ConsoleSink
from askoract.users import TerminalUser, leak_signals

app = typer.Typer(
    help="ask-or-act: a SQL data-analyst agent that answers, asks, or declines.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
db_app = typer.Typer(help="Inspect built databases.", no_args_is_help=True)
app.add_typer(db_app, name="db")
tool_app = typer.Typer(help="List agent tools or call one by hand.", no_args_is_help=True)
app.add_typer(tool_app, name="tool")
suite_app = typer.Typer(help="Inspect and validate test suites.", no_args_is_help=True)
app.add_typer(suite_app, name="suite")

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


@tool_app.command("list")
def tool_list(profile: ProfileOption = None) -> None:
    """Show the tools a profile gets and the descriptions the model sees."""
    settings = api.get_settings()
    prof = api.get_profile(profile, settings=settings)
    toolset = api.build_toolset(prof, dialect="sqlite", settings=settings)
    console.print(f"[bold]profile {prof.name}[/]  (descriptions: {prof.tool_descriptions})")
    for spec in toolset.specs():
        args = ", ".join(spec.parameters.get("properties", {})) or "-"
        console.rule(f"[bold]{spec.name}[/]({args})")
        console.print(spec.description)


@tool_app.command("run")
def tool_run(
    name: Annotated[str, typer.Argument(help="Tool name, e.g. describe_table.")],
    args: Annotated[str, typer.Option("--args", "-a", help="JSON arguments.")] = "{}",
    dataset: DatasetOption = None,
    variant: Annotated[str, typer.Option(help="Dataset variant.")] = "pristine",
) -> None:
    """Call one tool directly against a built database, with no LLM involved."""
    try:
        arguments = json.loads(args)
    except json.JSONDecodeError as exc:
        console.print(f"[red]--args is not valid JSON: {exc}[/]")
        raise typer.Exit(code=2) from None
    try:
        result = api.run_tool(
            name, arguments, dataset=dataset, variant=variant, user=TerminalUser(console)
        )
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(code=2) from None
    style = "red" if result.is_error else "green"
    console.rule(f"[{style}]{name}{'  (error)' if result.is_error else ''}")
    console.print(result.content, markup=False, highlight=False)
    if result.is_error:
        raise typer.Exit(code=1)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="A business question, in quotes.")],
    profile: ProfileOption = None,
    dataset: DatasetOption = None,
    variant: Annotated[str, typer.Option(help="Dataset variant.")] = "pristine",
    sim_intent: Annotated[
        str | None,
        typer.Option(
            "--sim-intent",
            help="Let a simulated user answer clarifying questions, holding this hidden intent. "
            "Without it, you answer them at the keyboard.",
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide live steps.")] = False,
    set_: SetOption = None,
) -> None:
    """Ask the agent one question. Steps print live; the full trace goes to runs/."""
    sinks = [] if quiet else [ConsoleSink(console)]
    try:
        out = api.ask(
            question,
            profile=profile,
            dataset=dataset,
            variant=variant,
            user=None if sim_intent else TerminalUser(console),
            sim_intent=sim_intent,
            overrides=set_,
            sinks=sinks,
        )
    except (KeyError, FileNotFoundError, DatasetBuildError) as exc:
        console.print(f"[red]{exc.args[0] if exc.args else exc}[/]")
        raise typer.Exit(code=2) from None
    _print_result(out)
    if out.result.outcome is Outcome.ERROR:
        raise typer.Exit(code=1)


def _print_result(out: api.AskRun) -> None:
    r = out.result
    final = r.final
    ok = r.outcome in (Outcome.ANSWERED, Outcome.CANNOT_ANSWER)
    console.print()
    if final:
        body = f"[bold]{final.answer}[/]"
        if final.assumption:
            body += f"\n\n[dim]Assumption:[/] {final.assumption}"
        console.print(Panel(body, title=f"[bold]{r.outcome.value}", border_style="green"))
        if final.sql:
            console.print(Syntax(final.sql, "sql", background_color="default", word_wrap=True))
    else:
        detail = f": {r.error}" if r.error else ""
        console.print(Panel(f"No final answer{detail}", title=r.outcome.value, border_style="red"))
    u = r.usage
    cache_note = " (all cached)" if r.llm_calls and r.billed_usd == 0 else ""
    console.print(
        f"[dim]{r.llm_calls} LLM calls · {len(r.steps)} tool calls · "
        f"{u.input_tokens:,} in / {u.output_tokens:,} out tokens · "
        f"cost ${r.cost_usd:.4f}, billed ${r.billed_usd:.4f}{cache_note} · "
        f"{r.latency_ms / 1000:.1f}s[/]"
    )
    trace = out.trace_path.relative_to(Path.cwd(), walk_up=True)
    console.print(f"[dim]trace: {trace}[/]", style=None if ok else "red")


@app.command("sim-user")
def sim_user(
    clarifying_question: Annotated[str, typer.Argument(help="What the agent asks the user.")],
    question: Annotated[str, typer.Option("--question", help="The user's original question.")],
    intent: Annotated[str, typer.Option("--intent", help="What the user really meant.")],
) -> None:
    """Get one simulated-user reply, to check the simulator stays in character."""
    try:
        reply = api.simulate_reply(question, intent, clarifying_question)
    except LLMError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from None
    console.print(f"[bold yellow]Agent asks:[/] {clarifying_question}")
    console.print(f"[bold green]Simulated user:[/] {reply}")
    if leaks := leak_signals(reply):
        console.print(f"[red]Possible leak of schema/SQL detail: {', '.join(leaks)}[/]")


@suite_app.command("list")
def suite_list() -> None:
    """List test suites with their label and trap mix."""
    settings = api.get_settings()
    table = Table("suite", "dataset", "cases", "clear", "ambiguous", "unanswerable", "traps")
    for name in api.list_suites(settings):
        suite = api.get_suite(name, settings)
        labels = Counter(c.label.value for c in suite.cases)
        traps = len({c.trap for c in suite.cases if c.trap})
        table.add_row(
            name,
            suite.dataset,
            str(len(suite.cases)),
            str(labels["clear"]),
            str(labels["ambiguous"]),
            str(labels["unanswerable"]),
            str(traps),
        )
    console.print(table)


@suite_app.command("validate")
def suite_validate(
    names: Annotated[list[str] | None, typer.Argument(help="Suites (default: all).")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show every case.")] = False,
) -> None:
    """Run every gold query and check each case is well formed. Exit 1 on any error."""
    with console.status("Running gold SQL..."):
        reports = api.validate_suites(names)
    failed = False
    for report in reports:
        table = Table("case", "label", "trap", "gold rows", "target result", "status")
        for c in report.checks:
            if c.ok and not verbose:
                continue
            rows = ", ".join(f"{k}:{v}" for k, v in c.gold_rows.items()) or "-"
            status = "[green]ok[/]" if c.ok else "[red]" + "; ".join(c.errors) + "[/]"
            table.add_row(c.case_id, c.label, c.trap or "-", rows, c.preview, status)
        n_bad = sum(not c.ok for c in report.checks)
        failed |= n_bad > 0
        labels = ", ".join(f"{k} {v}" for k, v in sorted(report.label_counts.items()))
        colour = "green" if n_bad == 0 else "red"
        console.rule(f"[bold]{report.suite}[/]  {len(report.checks)} cases ({labels})")
        if table.row_count:
            console.print(table)
        console.print(f"[{colour}]{len(report.checks) - n_bad} valid, {n_bad} with errors[/]")
    if failed:
        raise typer.Exit(code=1)


@app.command("eval")
def eval_(
    profile: Annotated[
        list[str] | None, typer.Option("--profile", "-p", help="Profiles (repeatable).")
    ] = None,
    suite: Annotated[
        list[str] | None, typer.Option("--suite", "-s", help="Suites (default: all).")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="First N cases of each suite.")] = None,
    case: Annotated[
        list[str] | None, typer.Option("--case", "-c", help="Only these case ids.")
    ] = None,
    set_: SetOption = None,
) -> None:
    """Run test suites for one or more profiles and score every case."""
    settings = api.get_settings(set_)
    profs = [api.get_profile(p, set_, settings=settings) for p in (profile or [None])]
    if missing := api.check_credentials(profs, settings):
        for model, keys in missing.items():
            console.print(f"[red]{model} needs {', '.join(keys)}.[/]")
        console.print("Add the keys to .env (see .env.example) and rerun.")
        raise typer.Exit(code=2)

    def progress(score: CaseScore, n: int, total: int) -> None:
        mark = "[green]✓[/]" if score.correct else "[red]✗[/]"
        asked = " [yellow]asked[/]" if score.asked else ""
        why = "" if score.correct else f"  [dim]{score.reason}[/]"
        console.print(
            f"[dim]{n:>3}/{total}[/] {mark} {score.profile:<10} {score.case_id:<6} "
            f"{score.label:<12}{asked}  [dim]{score.tool_calls} tools ${score.cost_usd:.3f}[/]{why}"
        )

    out = api.run_eval(
        [p.name for p in profs],
        suite,
        limit=limit,
        case_ids=case,
        overrides=set_,
        on_score=progress,
        settings=settings,
    )
    _print_eval_summary(out)


def _print_eval_summary(out: "api.EvalRun") -> None:
    table = Table("profile", "cases", "correct", "accuracy", "asked", "cost", "billed")
    by_profile: dict[str, list[CaseScore]] = {}
    for s in out.scores:
        by_profile.setdefault(s.profile, []).append(s)
    for name, scores in by_profile.items():
        n = len(scores)
        right = sum(s.correct for s in scores)
        table.add_row(
            name,
            str(n),
            str(right),
            f"{right / n:.0%}" if n else "-",
            str(sum(s.asked for s in scores)),
            f"${sum(s.cost_usd for s in scores):.3f}",
            f"${sum(s.billed_usd for s in scores):.3f}",
        )
    console.print()
    console.print(table)
    console.print(f"[dim]results: {out.run.dir / 'results.jsonl'}[/]")


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
