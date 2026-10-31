"""Configuration: one global YAML, one YAML per agent profile, CLI overrides on top.

Precedence (lowest to highest):
    configs/default.yaml  ->  agent_defaults  ->  configs/agents/<profile>.yaml  ->  --set overrides
"""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT_ENV_VAR = "ASKORACT_ROOT"
DEFAULT_CONFIG = Path("configs/default.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMConfig(_Strict):
    model: str
    temperature: float = 0.0
    max_tokens: int = 2048


class Limits(_Strict):
    max_steps: int = 15
    max_cost_usd: float = 0.50
    max_clarifications: int = 2
    sql_max_rows: int = 200
    sql_timeout_s: float = 10.0


class ModelPrice(_Strict):
    input_per_mtok: float
    output_per_mtok: float


class Paths(_Strict):
    data_dir: Path
    runs_dir: Path
    datasets_dir: Path
    prompts_dir: Path
    profiles_dir: Path


class CacheConfig(_Strict):
    enabled: bool = True
    path: Path


class SimulatedUserConfig(_Strict):
    llm: LLMConfig
    prompt: str


class AgentProfile(_Strict):
    """Everything that defines one agent configuration under test."""

    name: str
    description: str = ""
    prompt: str  # path under prompts_dir without extension, e.g. "agent/full_v1"
    tool_descriptions: str  # e.g. "tools/v1"
    tools: list[str]
    llm: LLMConfig
    limits: Limits = Field(default_factory=Limits)


class Settings(_Strict):
    root: Path
    seed: int = 42
    paths: Paths
    cache: CacheConfig
    agent_defaults: dict[str, Any] = Field(default_factory=dict)
    simulated_user: SimulatedUserConfig
    dataset: str
    default_profile: str
    prices: dict[str, ModelPrice] = Field(default_factory=dict)


def find_root(start: Path | None = None) -> Path:
    """The project root: $ASKORACT_ROOT, else the nearest parent holding configs/default.yaml."""
    if env := os.environ.get(ROOT_ENV_VAR):
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / DEFAULT_CONFIG).is_file():
            return candidate
    # Fall back to the repo this package lives in (src/askoract/config.py -> repo root).
    return Path(__file__).resolve().parents[2]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def apply_overrides(data: dict[str, Any], overrides: list[str] | None) -> dict[str, Any]:
    """Apply `a.b.c=value` strings; values are parsed as YAML (so 3, true, [x, y] work)."""
    for item in overrides or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError(f"override must look like key.path=value, got {item!r}")
        *parents, leaf = key.strip().split(".")
        patch: dict[str, Any] = {leaf: yaml.safe_load(raw)}
        for part in reversed(parents):
            patch = {part: patch}
        data = deep_merge(data, patch)
    return data


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def _resolve_paths(settings: Settings) -> Settings:
    def fix(p: Path) -> Path:
        return p if p.is_absolute() else settings.root / p

    paths = settings.paths.model_copy(
        update={k: fix(v) for k, v in settings.paths.model_dump().items()}
    )
    cache = settings.cache.model_copy(update={"path": fix(settings.cache.path)})
    return settings.model_copy(update={"paths": paths, "cache": cache})


def load_settings(root: Path | None = None, overrides: list[str] | None = None) -> Settings:
    root = root or find_root()
    data = apply_overrides(_read_yaml(root / DEFAULT_CONFIG), overrides)
    return _resolve_paths(Settings(root=root, **data))


def list_profiles(settings: Settings) -> list[str]:
    return sorted(p.stem for p in settings.paths.profiles_dir.glob("*.yaml"))


def load_profile(
    settings: Settings, name: str | None = None, overrides: list[str] | None = None
) -> AgentProfile:
    name = name or settings.default_profile
    path = settings.paths.profiles_dir / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(list_profiles(settings)) or "(none)"
        raise FileNotFoundError(f"unknown profile {name!r}; available: {available}")
    data = deep_merge(settings.agent_defaults, _read_yaml(path))
    data.setdefault("name", name)
    return AgentProfile(**apply_overrides(data, overrides))
