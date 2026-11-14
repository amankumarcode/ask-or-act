"""Builders that wire configuration into live objects: LLM clients, toolsets, agents,
simulated users. Kept below `api` so that benchmarks and the eval runner can use them
without importing the public facade.
"""

from pathlib import Path

from askoract.agent.loop import Agent
from askoract.config import AgentProfile, LLMConfig, Settings
from askoract.db import Database
from askoract.llm.base import LLMClient
from askoract.llm.cache import ResponseCache
from askoract.llm.client import LiteLLMClient
from askoract.prompts import load_prompt, load_tool_descriptions, prompt_text
from askoract.tools import Tool, ToolContext, Toolset
from askoract.tracing.tracer import Tracer
from askoract.users import SimulatedUser, UserChannel

_CACHES: dict[Path, ResponseCache] = {}


def build_toolset(
    profile: AgentProfile,
    dialect: str,
    settings: Settings,
    *,
    extra_tools: list[Tool] | None = None,
) -> Toolset:
    """The profile's tools, with descriptions from its versioned file, plus any extra tools
    a benchmark supplies (e.g. a knowledge-base lookup)."""
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


def make_llm(
    config: LLMConfig,
    settings: Settings,
    *,
    tracer: Tracer | None = None,
    role: str = "agent",
) -> LiteLLMClient:
    """An LLM client wired to the shared response cache and the price overrides."""
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
    settings: Settings,
    *,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
    extra_tools: list[Tool] | None = None,
) -> Agent:
    tracer = tracer or Tracer()
    system_prompt = load_prompt(
        settings,
        profile.prompt,
        dialect=db.dialect,
        db_name=db.name,
        max_clarifications=profile.limits.max_clarifications,
    )
    return Agent(
        llm=llm or make_llm(profile.llm, settings, tracer=tracer),
        toolset=build_toolset(profile, db.dialect, settings, extra_tools=extra_tools),
        system_prompt=system_prompt,
        limits=profile.limits,
        tracer=tracer,
    )


def make_context(
    profile: AgentProfile,
    db: Database,
    user: UserChannel,
    *,
    tracer: Tracer | None = None,
) -> ToolContext:
    return ToolContext(
        db=db,
        user=user,
        limits=profile.limits,
        tracer=tracer or Tracer(),
        assumption_check=profile.assumption_check and "ask_user" in profile.tools,
    )


def make_simulated_user(
    original_question: str,
    intent: str,
    settings: Settings,
    *,
    tracer: Tracer | None = None,
    llm: LLMClient | None = None,
) -> SimulatedUser:
    """An LLM user holding a hidden intent, on the model set in `simulated_user` config."""
    cfg = settings.simulated_user
    return SimulatedUser(
        llm or make_llm(cfg.llm, settings, tracer=tracer, role="simulated_user"),
        original_question=original_question,
        intent=intent,
        prompt_template=prompt_text(settings, cfg.prompt),
        tracer=tracer,
    )
