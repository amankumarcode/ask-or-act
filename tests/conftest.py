from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A minimal throwaway project root with its own config files."""
    (tmp_path / "configs" / "agents").mkdir(parents=True)
    (tmp_path / "configs" / "default.yaml").write_text(
        """
seed: 1
paths: {data_dir: data, runs_dir: runs, datasets_dir: datasets,
        prompts_dir: prompts, profiles_dir: configs/agents}
cache: {path: runs/cache/llm.sqlite}
agent_defaults:
  llm: {model: anthropic/claude-sonnet-5-5}
  limits: {max_steps: 15, sql_max_rows: 200}
simulated_user:
  llm: {model: anthropic/claude-haiku-5-5}
  prompt: sim_user/v1
dataset: northwind
default_profile: mini
"""
    )
    (tmp_path / "configs" / "agents" / "mini.yaml").write_text(
        """
prompt: agent/minimal_v1
tool_descriptions: tools/v1
tools: [run_sql, final_answer]
limits: {max_steps: 5}
"""
    )
    return tmp_path
