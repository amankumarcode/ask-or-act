"""Public entry points. The CLI, the Makefile, and any future UI call only these.

Overrides use one namespace: keys starting with `agent.` patch the agent profile,
and everything else patches the global settings, e.g.
    --set agent.llm.model=openrouter/openai/gpt-5  --set seed=7
"""

from dotenv import load_dotenv

from askoract import datasets
from askoract.config import AgentProfile, Settings, list_profiles, load_profile, load_settings
from askoract.datasets import BuildReport, Dataset
from askoract.db import Database
from askoract.prompts import load_tool_descriptions
from askoract.tools import TOOLS, Tool, ToolContext, ToolResult, Toolset
from askoract.users import NoUser, UserChannel

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
