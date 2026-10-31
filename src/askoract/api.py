"""Public entry points. The CLI, the Makefile, and any future UI call only these.

Overrides use one namespace: keys starting with `agent.` patch the agent profile,
and everything else patches the global settings, e.g.
    --set agent.llm.model=openrouter/openai/gpt-5  --set seed=7
"""

from dotenv import load_dotenv

from askoract.config import AgentProfile, Settings, list_profiles, load_profile, load_settings

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
