"""The eval runner end to end with scripted LLMs on the tiny database."""

import json
import shutil

import pytest

from askoract.benchmarks import YamlSuiteBenchmark
from askoract.config import load_profile, load_settings
from askoract.eval.cases import Suite
from askoract.eval.runner import RESULTS_FILE, run_eval, select_cases
from askoract.tracing import read_trace
from tests.fakes import FakeLLM, final, text_turn, tool_turn

GERMANY = "SELECT COUNT(*) FROM Customers WHERE Country = 'Germany'"
ALL = "SELECT COUNT(*) FROM Customers"

SUITE = {
    "name": "tiny",
    "dataset": "northwind",
    "default_variant": "pristine",
    "cases": [
        {
            "id": "T-1",
            "label": "clear",
            "question": "How many customers are in Germany?",
            "interpretations": [{"id": "de", "intent": "German customers", "gold_sql": GERMANY}],
        },
        {
            "id": "T-2",
            "label": "ambiguous",
            "question": "How many customers?",
            "hidden_intent": "de",
            "interpretations": [
                {"id": "de", "intent": "Only German customers", "gold_sql": GERMANY},
                {"id": "all", "intent": "Every customer", "gold_sql": ALL},
            ],
        },
        {"id": "T-3", "label": "unanswerable", "question": "Profit?", "reason": "no costs"},
    ],
}

# What the scripted agent does per case: T-1 answers, T-2 asks then answers, T-3 declines.
SCRIPTS = {
    "T-1": [tool_turn(("run_sql", {"sql": GERMANY})), tool_turn(final(GERMANY))],
    "T-2": [tool_turn(("ask_user", {"question": "Which customers?"})), tool_turn(final(GERMANY))],
    "T-3": [tool_turn(final(None, "No cost data.", status="cannot_answer"))],
}


@pytest.fixture
def setup(repo_root, tmp_path, db_path):
    built = tmp_path / "data" / "built" / "northwind"
    built.mkdir(parents=True)
    shutil.copyfile(db_path, built / "pristine.db")
    settings = load_settings(
        repo_root,
        [
            f"paths.data_dir={tmp_path / 'data'}",
            f"paths.runs_dir={tmp_path / 'runs'}",
            "cache.enabled=false",
        ],
    )
    user_llm = FakeLLM([text_turn("Only German ones.")])
    bench = YamlSuiteBenchmark(settings, Suite(**SUITE), user_llm=user_llm)
    return settings, bench, user_llm


def test_run_eval_scores_writes_results_and_traces(setup):
    settings, bench, user_llm = setup
    profile = load_profile(settings, "full")
    seen = []
    out = run_eval(
        settings,
        [profile],
        [bench],
        llm_factory=lambda prof, case: FakeLLM(SCRIPTS[case.id]),
        on_score=lambda s, n, total: seen.append((s.case_id, n, total)),
    )
    by_id = {s.case_id: s for s in out.scores}
    assert all(s.correct for s in out.scores), {k: v.reason for k, v in by_id.items()}
    assert by_id["T-2"].asked and by_id["T-2"].ask_decision == "asked_when_needed"
    assert by_id["T-3"].declined
    assert seen == [("T-1", 1, 3), ("T-2", 2, 3), ("T-3", 3, 3)]

    lines = (out.run.dir / RESULTS_FILE).read_text().splitlines()
    assert [json.loads(line)["case_id"] for line in lines] == ["T-1", "T-2", "T-3"]

    trace = read_trace(out.run.trace_path("full/T-2"))
    assert any(e["type"] == "user_reply" for e in trace)
    assert trace[0]["context"]["suite"] == "tiny"
    # The simulated user held the case's hidden intent.
    assert "Only German customers" in user_llm.requests[0][0][0]["content"]


def test_two_profiles_share_gold_and_get_separate_traces(setup):
    settings, bench, _ = setup
    profiles = [load_profile(settings, "full"), load_profile(settings, "baseline")]
    out = run_eval(
        settings,
        profiles,
        [bench],
        case_ids=["T-1"],
        llm_factory=lambda prof, case: FakeLLM(SCRIPTS[case.id]),
    )
    assert [(s.profile, s.case_id) for s in out.scores] == [("full", "T-1"), ("baseline", "T-1")]
    assert out.run.trace_path("baseline/T-1").is_file()


def test_select_cases():
    cases = Suite(**SUITE).cases
    assert [c.id for c in select_cases(cases, limit=2)] == ["T-1", "T-2"]
    assert [c.id for c in select_cases(cases, case_ids=["T-3"])] == ["T-3"]


def _factory(prof, case):
    return FakeLLM(SCRIPTS[case.id])


def test_parallel_run_gives_the_same_scores(setup):
    settings, bench, _ = setup
    profile = load_profile(settings, "full")
    out = run_eval(settings, [profile], [bench], concurrency=3, llm_factory=_factory)
    assert sorted(s.case_id for s in out.scores) == ["T-1", "T-2", "T-3"]
    assert all(s.correct for s in out.scores)
    assert len((out.run.dir / RESULTS_FILE).read_text().splitlines()) == 3


def test_budget_stops_new_cases_and_resume_finishes(setup):
    settings, bench, _ = setup
    profile = load_profile(settings, "full")
    # Each scripted turn bills $0.01; T-1 takes two turns, so a $0.015 cap stops after it.
    first = run_eval(settings, [profile], [bench], max_cost_usd=0.015, llm_factory=_factory)
    assert [s.case_id for s in first.scores] == ["T-1"]
    assert first.skipped_for_budget == 2

    resumed = run_eval(settings, [profile], [bench], resume=first.run, llm_factory=_factory)
    assert resumed.run.dir == first.run.dir and resumed.resumed == 1
    assert sorted(s.case_id for s in resumed.scores) == ["T-1", "T-2", "T-3"]
    lines = (first.run.dir / RESULTS_FILE).read_text().splitlines()
    assert len(lines) == 3  # T-1 was not run twice


def test_report_from_results_file(setup):
    from askoract import api

    settings, bench, _ = setup
    out = run_eval(settings, [load_profile(settings, "full")], [bench], llm_factory=_factory)
    md = api.report(out.run.id, settings=settings)
    assert "**100% (3/3)**" in md.read_text()


def test_trace_view_of_an_eval_case(setup):
    from askoract import api

    settings, bench, _ = setup
    out = run_eval(settings, [load_profile(settings, "full")], [bench], llm_factory=_factory)
    assert api.list_traces(out.run.id, settings) == ["full/T-1", "full/T-2", "full/T-3"]
    view = api.load_trace_view(out.run.id, "T-2", settings=settings)
    assert view.case_id == "T-2" and view.score["correct"] is True
    assert [s.kind for s in view.steps] == ["ask"]
    with pytest.raises(KeyError, match="not found"):
        api.load_trace_view(out.run.id, "T-9", settings=settings)


def test_demo_config_is_valid(repo_root):
    from askoract import api

    settings = load_settings(repo_root)
    demo = api.load_demo(settings)
    suites = {name: api.get_suite(name, settings) for name in api.list_suites(settings)}
    for item in demo.cases:
        assert item["case"] in {c.id for c in suites[item["suite"]].cases}, item
        assert item.get("why"), item
    for name in demo.profiles:
        load_profile(settings, name)


def test_empty_selection_is_an_error_not_an_empty_run(setup):
    settings, bench, _ = setup
    with pytest.raises(ValueError, match="no cases selected"):
        run_eval(settings, [load_profile(settings, "full")], [bench], case_ids=["NOPE"])
    assert not (settings.paths.runs_dir).exists() or not list(
        settings.paths.runs_dir.glob("eval-*")
    )
