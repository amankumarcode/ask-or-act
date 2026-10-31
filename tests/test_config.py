import pytest
from pydantic import ValidationError

from askoract import api
from askoract.config import (
    apply_overrides,
    deep_merge,
    find_root,
    list_profiles,
    load_profile,
    load_settings,
)


def test_deep_merge_keeps_unrelated_keys():
    merged = deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 1}


def test_overrides_parse_yaml_values():
    data = apply_overrides({"a": {"b": 1}}, ["a.b=2", "a.c=true", "d=[x, y]"])
    assert data == {"a": {"b": 2, "c": True}, "d": ["x", "y"]}


def test_override_without_equals_is_rejected():
    with pytest.raises(ValueError, match="key.path=value"):
        apply_overrides({}, ["nonsense"])


def test_paths_resolve_against_root(tmp_project):
    settings = load_settings(tmp_project)
    assert settings.paths.data_dir == tmp_project / "data"
    assert settings.cache.path == tmp_project / "runs/cache/llm.sqlite"


def test_profile_inherits_defaults_and_overrides_limits(tmp_project):
    profile = load_profile(load_settings(tmp_project), "mini")
    assert profile.name == "mini"
    assert profile.llm.model == "anthropic/claude-sonnet-5-5"  # from agent_defaults
    assert profile.limits.max_steps == 5  # profile wins
    assert profile.limits.sql_max_rows == 200  # default kept


def test_profile_cli_override(tmp_project):
    profile = load_profile(
        load_settings(tmp_project), "mini", ["llm.model=openrouter/openai/gpt-5"]
    )
    assert profile.llm.model == "openrouter/openai/gpt-5"


def test_unknown_profile_lists_available(tmp_project):
    with pytest.raises(FileNotFoundError, match="available: mini"):
        load_profile(load_settings(tmp_project), "nope")


def test_typo_in_config_is_rejected(tmp_project):
    with pytest.raises(ValidationError):
        load_settings(tmp_project, ["sede=3"])


def test_find_root_uses_env(monkeypatch, tmp_project):
    monkeypatch.setenv("ASKORACT_ROOT", str(tmp_project))
    assert find_root() == tmp_project


def test_split_overrides_routes_agent_prefix():
    s, p = api.split_overrides(["seed=3", "agent.llm.model=x", "agent.limits.max_steps=2"])
    assert s == ["seed=3"]
    assert p == ["llm.model=x", "limits.max_steps=2"]


def test_repo_profiles_load(repo_root):
    """The shipped configs must always be valid."""
    settings = load_settings(repo_root)
    names = list_profiles(settings)
    assert {"baseline", "full"} <= set(names)
    for name in names:
        profile = load_profile(settings, name)
        assert "final_answer" in profile.tools
    assert "ask_user" not in load_profile(settings, "baseline").tools
