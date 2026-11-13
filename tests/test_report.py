"""Report aggregation and rendering from synthetic scores."""

import json

from askoract.eval.report import Rate, render_markdown, summarize, write_report
from askoract.eval.scorer import CaseScore, ask_decision


def sc(
    case_id,
    label,
    correct,
    *,
    profile="full",
    asked=False,
    declined=False,
    trap=None,
    explored=True,
    sql_errors=0,
    recovered=None,
    outcome="answered",
    cost=0.01,
):
    should_ask = label == "ambiguous"
    return CaseScore(
        case_id=case_id,
        suite="s",
        profile=profile,
        label=label,
        trap=trap,
        tags=[],
        outcome=outcome,
        correct=correct,
        reason="ok" if correct else "wrong",
        should_ask=should_ask,
        asked=asked,
        n_questions=int(asked),
        ask_decision=ask_decision(should_ask, asked).value,
        declined=declined,
        matched_interpretation=None,
        explored_before_sql=explored,
        sql_errors=sql_errors,
        recovered_from_error=recovered,
        tool_calls=4,
        llm_calls=4,
        input_tokens=1000,
        output_tokens=100,
        cost_usd=cost,
        billed_usd=cost,
        latency_ms=2000,
    )


SCORES = [
    sc("1", "clear", True, trap="date_format"),
    sc("2", "clear", False, trap="date_format", asked=True),  # unneeded ask
    sc("3", "ambiguous", True, asked=True, trap="late_orders"),
    sc("4", "ambiguous", True, trap="late_orders"),  # lucky guess, no ask
    sc("5", "ambiguous", False, trap="late_orders"),
    sc("6", "unanswerable", True, declined=True, explored=None),
    sc("7", "clear", False, declined=True, sql_errors=1, recovered=False),  # false decline
    sc("1", "clear", False, profile="baseline", explored=False, sql_errors=2, recovered=True),
]


def test_rate():
    assert str(Rate(3, 4)) == "75% (3/4)" and Rate().value is None and str(Rate()) == "-"


def test_summary_metrics():
    full = summarize(SCORES)["full"]
    assert (full.accuracy.hits, full.accuracy.n) == (4, 7)
    assert full.by_label["ambiguous"].hits == 2 and full.by_label["clear"].n == 3
    assert full.by_trap["late_orders"].n == 3 and full.by_trap["date_format"].hits == 1
    assert full.confusion == {
        "asked_when_needed": 1,
        "missed_ask": 2,
        "unneeded_ask": 1,
        "acted_when_clear": 3,
    }
    assert (full.ask_recall.hits, full.ask_recall.n) == (1, 3)
    assert (full.ask_precision.hits, full.ask_precision.n) == (1, 2)
    assert full.ambiguous_correct_without_asking == 1
    assert (full.declined_unanswerable.hits, full.declined_unanswerable.n) == (1, 1)
    assert full.false_declines == 1
    assert full.explored_before_sql.n == 6  # case 6 made no SQL call
    assert (full.recovered_from_errors.hits, full.recovered_from_errors.n) == (0, 1)
    assert full.efficiency["cost_usd_total"] == 0.07


def test_markdown_compares_profiles_side_by_side():
    md = render_markdown(summarize(SCORES), "Results")
    header = md.splitlines()[2]
    assert header == "| Metric | full | baseline |"
    assert "| **Accuracy (all)** | **57% (4/7)** | **0% (0/1)** |" in md
    assert "## By trap" in md and "| late_orders | 67% (2/3) | - |" in md
    assert "Should ask (ambiguous) | 1 | 2 |" in md


def test_write_report_files(tmp_path):
    md = write_report(tmp_path, SCORES)
    assert md.read_text().startswith("# Results:")
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["summary"]["full"]["accuracy"] == {"hits": 4, "n": 7, "value": 4 / 7}
    assert len(data["scores"]) == len(SCORES)


def test_profile_order_is_respected():
    assert list(summarize(SCORES, ["baseline", "full"])) == ["baseline", "full"]
