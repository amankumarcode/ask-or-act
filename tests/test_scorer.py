"""Scoring of single runs against synthetic cases on the tiny database."""

from askoract.agent.types import AgentResult, FinalAnswer, Outcome, Step
from askoract.eval.cases import TestCase
from askoract.eval.scorer import AskDecision, GoldCache, ask_decision, score_case

GERMANY = "SELECT COUNT(*) FROM Customers WHERE Country = 'Germany'"
ALL = "SELECT COUNT(*) FROM Customers"


def case(label="clear", **kw):
    base = {"id": "T-1", "question": "q", "label": label, "db_variant": "pristine"}
    if label == "clear":
        base["interpretations"] = [{"id": "de", "intent": "German", "gold_sql": GERMANY}]
    elif label == "ambiguous":
        base["interpretations"] = [
            {"id": "de", "intent": "German", "gold_sql": GERMANY},
            {"id": "all", "intent": "All", "gold_sql": ALL},
        ]
        base["hidden_intent"] = "de"
    else:
        base["reason"] = "no such data"
    return TestCase(**{**base, **kw})


def result(sql=None, status="answered", steps=(), asked=False, outcome=None):
    final = FinalAnswer(status=status, answer="a", sql=sql) if status else None
    if outcome is None:
        outcome = {"answered": Outcome.ANSWERED, "cannot_answer": Outcome.CANNOT_ANSWER}.get(
            status, Outcome.STEP_LIMIT
        )
    r = AgentResult(question="q", outcome=outcome, final=final, steps=list(steps))
    if asked:
        r.clarifications = [("Which country?", "Germany")]
    return r


def step(tool, error=False):
    return Step(index=0, tool=tool, arguments={}, output="", is_error=error)


def score(c, r, db):
    return score_case(c, r, db, GoldCache(), suite="s", profile="p")


def test_correct_clear_answer(db):
    s = score(case(), result("SELECT COUNT(*) AS n FROM Customers WHERE Country='Germany'"), db)
    assert s.correct and s.reason == "ok" and s.matched_interpretation == "de"
    assert s.ask_decision == AskDecision.ACTED_WHEN_CLEAR


def test_wrong_result(db):
    s = score(case(), result(ALL), db)
    assert not s.correct and s.reason.startswith("wrong result")


def test_ambiguous_answered_with_other_reading(db):
    s = score(case("ambiguous"), result(ALL), db)
    assert not s.correct and s.matched_interpretation == "all"
    assert "different reading" in s.reason
    assert s.ask_decision == AskDecision.MISSED_ASK


def test_ambiguous_asked_and_correct(db):
    s = score(case("ambiguous"), result(GERMANY, asked=True), db)
    assert s.correct and s.ask_decision == AskDecision.ASKED_WHEN_NEEDED and s.n_questions == 1


def test_unanswerable_declined_is_correct(db):
    s = score(case("unanswerable"), result(None, status="cannot_answer"), db)
    assert s.correct and s.declined


def test_unanswerable_answered_is_wrong(db):
    s = score(case("unanswerable"), result(ALL), db)
    assert not s.correct and "cannot answer" in s.reason


def test_declining_an_answerable_question_is_wrong(db):
    s = score(case(), result(None, status="cannot_answer"), db)
    assert not s.correct and s.reason.startswith("declined")


def test_no_final_answer(db):
    s = score(case(), result(status=None, outcome=Outcome.BUDGET_EXCEEDED), db)
    assert not s.correct and "budget_exceeded" in s.reason


def test_final_sql_that_fails(db):
    s = score(case(), result("SELECT nope FROM Customers"), db)
    assert not s.correct and s.reason.startswith("final SQL fails")


def test_unneeded_ask(db):
    s = score(case(), result(GERMANY, asked=True), db)
    assert s.correct and s.ask_decision == AskDecision.UNNEEDED_ASK


def test_trajectory_flags(db):
    explored = score(case(), result(GERMANY, steps=[step("describe_table"), step("run_sql")]), db)
    assert explored.explored_before_sql is True and explored.sql_errors == 0
    assert explored.recovered_from_error is None

    blind = score(
        case(),
        result(GERMANY, steps=[step("run_sql", error=True), step("run_sql"), step("final_answer")]),
        db,
    )
    assert blind.explored_before_sql is False
    assert blind.sql_errors == 1 and blind.recovered_from_error is True

    no_sql = score(case(), result(GERMANY, steps=[step("final_answer")]), db)
    assert no_sql.explored_before_sql is None


def test_ask_decision_matrix():
    assert ask_decision(True, True) is AskDecision.ASKED_WHEN_NEEDED
    assert ask_decision(True, False) is AskDecision.MISSED_ASK
    assert ask_decision(False, True) is AskDecision.UNNEEDED_ASK
    assert ask_decision(False, False) is AskDecision.ACTED_WHEN_CLEAR


def test_gold_cache_runs_each_query_once(db):
    calls = []
    original = db.execute

    def counting(sql, **kw):
        calls.append(sql)
        return original(sql, **kw)

    db.execute = counting
    gold = GoldCache()
    gold.get(db, "pristine", GERMANY)
    gold.get(db, "pristine", GERMANY)
    assert calls == [GERMANY]
