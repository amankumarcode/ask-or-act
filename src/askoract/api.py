"""Public entry points. The CLI, the Makefile, and any future UI call only these.

Overrides use one namespace: keys starting with `agent.` patch the agent profile,
and everything else patches the global settings, e.g.
    --set agent.llm.model=openrouter/openai/gpt-5  --set seed=7
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

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
from askoract.tracing.tracer import Sink, Tracer, read_trace
from askoract.tracing.viewer import TraceView, build_view
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
        ctx = factory.make_context(prof, db, user or NoUser(), tracer=tracer)
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
    kind: str = "eval",
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
        kind=kind,
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


def list_traces(run_id: str = "latest", settings: Settings | None = None) -> list[str]:
    """Trace names in a run: "ask" for an ask run, "<profile>/<case>" for an eval run."""
    run = resolve_run(settings or get_settings(), run_id)
    root = run.dir / "traces"
    return sorted(str(p.relative_to(root).with_suffix("")) for p in root.rglob("*.jsonl"))


def load_trace_view(
    run_id: str = "latest",
    case: str | None = None,
    profile: str | None = None,
    settings: Settings | None = None,
) -> TraceView:
    """The steps of one trace. `case` may be "A-001", "full/A-001", or omitted for ask runs."""
    settings = settings or get_settings()
    run = resolve_run(settings, run_id)
    names = list_traces(run.id, settings)
    if case is None and names == ["ask"]:
        name = "ask"
    else:
        wanted = f"{profile}/{case}" if profile and case and "/" not in case else case
        matches = [n for n in names if n == wanted or n.endswith(f"/{wanted}")]
        if len(matches) != 1:
            hint = "matches several profiles; pass --profile" if matches else "not found"
            raise KeyError(f"trace {case!r} {hint}. Available: {', '.join(names[:12])}…")
        name = matches[0]
    events = read_trace(run.trace_path(name))
    score = None
    if "/" in name:
        prof, case_id = name.split("/", 1)
        for s in load_scores(run.dir / RESULTS_FILE):
            if s.profile == prof and s.case_id == case_id:
                score = s.to_dict()
    return build_view(events, score)


class DemoConfig(BaseModel):
    """configs/demo.yaml: which cases the demo replays, for which profiles, and why."""

    profiles: list[str]
    cases: list[dict[str, str]]  # {suite, case, why}


def load_demo(settings: Settings | None = None) -> DemoConfig:
    settings = settings or get_settings()
    return DemoConfig(**yaml.safe_load((settings.root / "configs" / "demo.yaml").read_text()))


def run_demo(
    *,
    profiles: list[str] | None = None,
    on_score: Callable[[CaseScore, int, int], None] | None = None,
    settings: Settings | None = None,
) -> tuple[DemoConfig, EvalRun]:
    """Replay the showcase cases. After `make eval` every response comes from the cache."""
    settings = settings or get_settings()
    demo = load_demo(settings)
    out = run_eval(
        profiles or demo.profiles,
        sorted({c["suite"] for c in demo.cases}),
        case_ids=[c["case"] for c in demo.cases],
        on_score=on_score,
        kind="demo",
        settings=settings,
    )
    return demo, out


README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"


def publish_results(
    run_id: str = "latest", name: str | None = None, settings: Settings | None = None
) -> Path:
    """Copy a run's report into results/<name>/ (versioned) and refresh the README tables."""
    settings = settings or get_settings()
    run = resolve_run(settings, run_id)
    md = report(run.id, settings)  # rebuild from results.jsonl so the files are current
    dest = settings.root / "results" / (name or run.id)
    dest.mkdir(parents=True, exist_ok=True)
    for fname in ("summary.md", "results.json", "manifest.json"):
        shutil.copyfile(run.dir / fname, dest / fname)

    readme = settings.root / "README.md"
    text = readme.read_text()
    if README_START in text and README_END in text:
        body = md.read_text().split("\n", 2)[2].strip()  # drop the "# Results: ..." title
        body = body.replace("## ", "#### ")
        rel = dest.relative_to(settings.root)
        block = (
            f"{README_START}\n{body}\n\n"
            f"Full report, every case score and the run manifest: [`{rel}/`]({rel}/)\n"
            f"{README_END}"
        )
        head, _, rest = text.partition(README_START)
        _, _, tail = rest.partition(README_END)
        readme.write_text(head + block + tail)
    return dest
