"""Public entry points. The CLI, the Makefile, and any future UI call only these.

Overrides use one namespace: keys starting with `agent.` patch the agent profile,
and everything else patches the global settings, e.g.
    --set agent.llm.model=openrouter/openai/gpt-5  --set seed=7
"""

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from askoract import datasets
from askoract.agent.loop import Agent
from askoract.agent.types import AgentResult
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
from askoract.llm.base import LLMClient
from askoract.llm.cache import ResponseCache
from askoract.llm.client import LiteLLMClient
from askoract.prompts import load_prompt, load_tool_descriptions, prompt_text
from askoract.runs import Run, new_run
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
    """The profile's tools, with descriptions from its versioned file, plus any extra tools
    a benchmark supplies (e.g. a knowledge-base lookup)."""
    settings = settings or get_settings()
    descriptions = load_tool_descriptions(
        settings,
        profile.tool_descriptions,
        dialect=dialect,
        max_rows=profile.limits.sql_max_rows,
        max_clarifications=profile.limits.max_clarifications,
    )
    toolset = Toolset.from_names(profile.tools, descriptions)
    for tool in extra_tools or []:
        toolset.add(tool)
    return toolset


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


_CACHES: dict[Path, ResponseCache] = {}


def make_llm(
    config: LLMConfig,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    role: str = "agent",
) -> LiteLLMClient:
    """An LLM client wired to the shared response cache and the price overrides."""
    settings = settings or get_settings()
    cache = None
    if settings.cache.enabled:
        path = settings.cache.path
        if path not in _CACHES:  # one connection per cache file per process
            _CACHES[path] = ResponseCache(path)
        cache = _CACHES[path]
    return LiteLLMClient(config, cache=cache, prices=settings.prices, tracer=tracer, role=role)


def build_agent(
    profile: AgentProfile,
    db: Database,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
    extra_tools: list[Tool] | None = None,
) -> Agent:
    settings = settings or get_settings()
    tracer = tracer or Tracer()
    system_prompt = load_prompt(
        settings,
        profile.prompt,
        dialect=db.dialect,
        db_name=db.name,
        max_clarifications=profile.limits.max_clarifications,
    )
    return Agent(
        llm=llm or make_llm(profile.llm, settings=settings, tracer=tracer),
        toolset=build_toolset(profile, db.dialect, settings=settings, extra_tools=extra_tools),
        system_prompt=system_prompt,
        limits=profile.limits,
        tracer=tracer,
    )


def make_simulated_user(
    original_question: str,
    intent: str,
    *,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
) -> SimulatedUser:
    """An LLM user holding a hidden intent, on the model set in `simulated_user` config."""
    settings = settings or get_settings()
    cfg = settings.simulated_user
    return SimulatedUser(
        llm or make_llm(cfg.llm, settings=settings, tracer=tracer, role="simulated_user"),
        original_question=original_question,
        intent=intent,
        prompt_template=prompt_text(settings, cfg.prompt),
        tracer=tracer,
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
