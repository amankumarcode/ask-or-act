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
