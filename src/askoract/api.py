"""Public entry points. The CLI, the Makefile, and any future UI call only these.

Overrides use one namespace: keys starting with `agent.` patch the agent profile,
and everything else patches the global settings, e.g.
    --set agent.llm.model=openrouter/openai/gpt-5  --set seed=7
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from askoract import datasets, factory
from askoract.agent.loop import Agent
from askoract.agent.types import AgentResult
from askoract.benchmarks import YamlSuiteBenchmark, find_suites, load_suite
from askoract.config import (
    AgentProfile,
    LLMConfig,
    Settings,
    list_profiles,
    load_profile,
    load_settings,
)
from askoract.datasets import BuildReport, Dataset
from askoract.db import Database
from askoract.eval.cases import Suite
from askoract.eval.report import write_report
from askoract.eval.runner import RESULTS_FILE, EvalRun, load_scores
from askoract.eval.runner import run_eval as _run_eval
from askoract.eval.scorer import CaseScore
from askoract.eval.validate import SuiteReport, validate_suite
from askoract.llm.base import LLMClient
from askoract.llm.client import missing_credentials
from askoract.runs import Run, manifest, new_run, resolve_run
from askoract.tools import TOOLS, Tool, ToolContext, ToolResult, Toolset
from askoract.tracing.tracer import Sink, Tracer
from askoract.users import NoUser, SimulatedUser, UserChannel

AGENT_PREFIX = "agent."


def split_overrides(overrides: list[str] | None) -> tuple[list[str], list[str]]:
    settings_ov, profile_ov = [], []
    for item in overrides or []:
        if item.startswith(AGENT_PREFIX):
            profile_ov.append(item.removeprefix(AGENT_PREFIX))
        else:
            settings_ov.append(item)
    return settings_ov, profile_ov


def get_settings(overrides: list[str] | None = None) -> Settings:
    settings_ov, _ = split_overrides(overrides)
    settings = load_settings(overrides=settings_ov)
    load_dotenv(settings.root / ".env", override=False)
    return settings


def get_profile(
    name: str | None = None,
    overrides: list[str] | None = None,
    settings: Settings | None = None,
) -> AgentProfile:
    settings = settings or get_settings(overrides)
    _, profile_ov = split_overrides(overrides)
    return load_profile(settings, name, profile_ov)


def get_profiles(settings: Settings | None = None) -> list[str]:
    return list_profiles(settings or get_settings())


def get_dataset(name: str | None = None, settings: Settings | None = None) -> Dataset:
    return datasets.get_dataset(settings or get_settings(), name)


def build_dataset(
    name: str | None = None,
    variant: str | None = None,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> list[BuildReport]:
    return get_dataset(name, settings).build(variant, force=force)


def open_database(
    name: str | None = None, variant: str = "pristine", settings: Settings | None = None
) -> Database:
    """A read-only connection to a built dataset variant."""
    return get_dataset(name, settings).open(variant)


def build_toolset(
    profile: AgentProfile,
    dialect: str,
    *,
    settings: Settings | None = None,
    extra_tools: list[Tool] | None = None,
) -> Toolset:
    return factory.build_toolset(
        profile, dialect, settings or get_settings(), extra_tools=extra_tools
    )


def run_tool(
    name: str,
    arguments: dict,
    *,
    dataset: str | None = None,
    variant: str = "pristine",
    user: UserChannel | None = None,
    profile: str | None = None,
    settings: Settings | None = None,
) -> ToolResult:
    """Call one tool by hand, outside any agent run. Handy for debugging and demos."""
    TOOLS.get(name)  # fail fast with the list of known tools on a typo
    settings = settings or get_settings()
    prof = get_profile(profile, settings=settings)
    db = open_database(dataset, variant, settings)
    try:
        prof = prof.model_copy(update={"tools": [name]})
        tool = build_toolset(prof, db.dialect, settings=settings).get(name)
        ctx = ToolContext(db=db, user=user or NoUser(), limits=prof.limits)
        return tool(arguments, ctx)
    finally:
        db.close()


def make_llm(
    config: LLMConfig,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    role: str = "agent",
) -> LLMClient:
    return factory.make_llm(config, settings or get_settings(), tracer=tracer, role=role)


def build_agent(
    profile: AgentProfile,
    db: Database,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
    extra_tools: list[Tool] | None = None,
) -> Agent:
    return factory.build_agent(
        profile, db, settings or get_settings(), tracer=tracer, llm=llm, extra_tools=extra_tools
    )


def make_simulated_user(
    original_question: str,
    intent: str,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
) -> SimulatedUser:
    return factory.make_simulated_user(
        original_question, intent, settings or get_settings(), tracer=tracer, llm=llm
    )


@dataclass(frozen=True)
class AskRun:
    result: AgentResult
    run: Run
    trace_path: Path


def ask(
    question: str,
    *,
    profile: str | None = None,
    dataset: str | None = None,
    variant: str = "pristine",
    user: UserChannel | None = None,
    sim_intent: str | None = None,
    overrides: list[str] | None = None,
    sinks: list[Sink] | None = None,
    llm: LLMClient | None = None,
    user_llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> AskRun:
    """Answer one question end to end, writing a trace to runs/<run_id>/traces/ask.jsonl.

    Clarifying questions go to `user`, or to a simulated user holding `sim_intent`
    when that is given, or nowhere (NoUser) when neither is.
    """
    settings = settings or get_settings(overrides)
    prof = get_profile(profile, overrides, settings=settings)
    db = open_database(dataset, variant, settings)
    run = new_run(
        settings,
        "ask",
        [prof],
        extra={
            "question": question,
            "dataset": dataset or settings.dataset,
            "variant": variant,
            "sim_intent": sim_intent,
        },
    )
    tracer = run.tracer("ask", sinks=sinks, profile=prof.name)
    try:
        if sim_intent is not None:
            user = make_simulated_user(
                question, sim_intent, settings=settings, tracer=tracer, llm=user_llm
            )
        agent = build_agent(prof, db, settings=settings, tracer=tracer, llm=llm)
        ctx = ToolContext(db=db, user=user or NoUser(), limits=prof.limits, tracer=tracer)
        result = agent.run(question, ctx)
    finally:
        tracer.close()
        db.close()
    return AskRun(result=result, run=run, trace_path=run.trace_path("ask"))


def simulate_reply(
    original_question: str,
    intent: str,
    clarifying_question: str,
    *,
    settings: Settings | None = None,
    sinks: list[Sink] | None = None,
) -> str:
    """One simulated-user reply, outside any agent run. For checking the simulator by hand."""
    tracer = Tracer(sinks)
    user = make_simulated_user(original_question, intent, settings=settings, tracer=tracer)
    return user.reply(clarifying_question)


def list_suites(settings: Settings | None = None) -> list[str]:
    return list(find_suites(settings or get_settings()))


def get_suite(name: str, settings: Settings | None = None) -> Suite:
    return load_suite(settings or get_settings(), name)


def validate_suites(
    names: list[str] | None = None, settings: Settings | None = None
) -> list[SuiteReport]:
    """Run every gold query of the named suites (all suites when none are named)."""
    settings = settings or get_settings()
    reports = []
    for name in names or list_suites(settings):
        bench = YamlSuiteBenchmark(settings, load_suite(settings, name))
        reports.append(validate_suite(bench.suite, bench.open_db))
    return reports


def check_credentials(profiles: list[AgentProfile], settings: Settings) -> dict[str, list[str]]:
    """Model -> missing environment variables, for every model an eval would call."""
    models = {p.llm.model for p in profiles} | {settings.simulated_user.llm.model}
    return {m: missing for m in sorted(models) if (missing := missing_credentials(m))}


def run_eval(
    profiles: list[str] | None = None,
    suites: list[str] | None = None,
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    concurrency: int = 4,
    max_cost_usd: float | None = None,
    resume: str | None = None,
    overrides: list[str] | None = None,
    sinks: list[Sink] | None = None,
    on_score: Callable[[CaseScore, int, int], None] | None = None,
    llm_factory: Callable | None = None,
    user_llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> EvalRun:
    """Run suites for profiles, then write results.json and summary.md into the run dir.

    With `resume` (a run id), the run's own manifest decides profiles, suites, limit and
    cases, so finishing an interrupted run can't silently change what is measured.
    """
    settings = settings or get_settings(overrides)
    prior = resolve_run(settings, resume) if resume else None
    if prior:
        m = manifest(prior)
        profs = [AgentProfile(**p) for p in m["profiles"]]
        suites, limit, case_ids = m["benchmarks"], m.get("limit"), m.get("case_ids")
    else:
        profs = [get_profile(p, overrides, settings=settings) for p in (profiles or [None])]
    benches = [
        YamlSuiteBenchmark(settings, load_suite(settings, name), user_llm=user_llm)
        for name in (suites or list_suites(settings))
    ]
    out = _run_eval(
        settings,
        profs,
        benches,
        limit=limit,
        case_ids=case_ids,
        concurrency=concurrency,
        max_cost_usd=max_cost_usd,
        resume=prior,
        llm_factory=llm_factory,
        sinks=sinks,
        on_score=on_score,
    )
    write_report(out.run.dir, out.scores, profile_order=[p.name for p in profs])
    return out


def report(run_id: str = "latest", settings: Settings | None = None) -> Path:
    """Rebuild results.json and summary.md for a run from its results.jsonl."""
    run = resolve_run(settings or get_settings(), run_id)
    scores = load_scores(run.dir / RESULTS_FILE)
    if not scores:
        raise FileNotFoundError(f"run {run.id} has no scored cases yet")
    order = [p["name"] for p in manifest(run).get("profiles", [])]
    return write_report(run.dir, scores, profile_order=order)
