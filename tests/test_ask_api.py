"""End to end through api.ask with a FakeLLM: run directory, manifest, JSONL trace."""

import json
import shutil

import pytest

from askoract import api
from askoract.agent.types import Outcome
from askoract.config import load_settings
from askoract.prompts import load_prompt
from askoract.tracing import read_trace
from tests.fakes import FakeLLM, final, tool_turn


@pytest.fixture
def settings(repo_root, tmp_path, db_path):
    """Real configs and prompts, but runs and a 'built' toy dataset in a temp dir."""
    data_dir = tmp_path / "data"
    built = data_dir / "built" / "northwind"
    built.mkdir(parents=True)
    shutil.copyfile(db_path, built / "pristine.db")
    return load_settings(
        repo_root,
        [
            f"paths.data_dir={data_dir}",
            f"paths.runs_dir={tmp_path / 'runs'}",
            "cache.enabled=false",
        ],
    )


def test_ask_writes_run_manifest_and_trace(settings):
    sql = "SELECT COUNT(*) FROM Customers WHERE Country = 'Germany'"
    llm = FakeLLM([tool_turn(("run_sql", {"sql": sql})), tool_turn(final(sql, "2"))])
    out = api.ask("How many customers are in Germany?", llm=llm, settings=settings)

    assert out.result.outcome is Outcome.ANSWERED and out.result.final.answer == "2"
    assert (settings.paths.runs_dir / "latest").resolve() == out.run.dir

    manifest = json.loads((out.run.dir / "manifest.json").read_text())
    assert manifest["question"] == "How many customers are in Germany?"
    assert manifest["profiles"][0]["name"] == "full"
    assert set(manifest["prompts"]) >= {"agent/full_v1.md", "tools/v1.yaml"}

    events = read_trace(out.trace_path)
    assert {e["context"]["case_id"] for e in events} == {"ask"}
    types = [e["type"] for e in events]
    assert types.count("tool_call") == 2 and types[-1] == "run_end"
    system = next(e for e in events if e["type"] == "message")["data"]["message"]
    assert system["role"] == "system" and "${" not in system["content"]
    assert "sqlite" in system["content"]


def test_system_prompts_render_cleanly(settings):
    for name in ("agent/full_v1", "agent/minimal_v1"):
        text = load_prompt(settings, name, dialect="sqlite", db_name="x", max_clarifications=2)
        assert "${" not in text and "sqlite" in text
