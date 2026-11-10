"""Score one agent run against one test case.

Correctness re-executes the agent's final SQL and compares result sets with the gold
(see compare.py); the agent's prose answer is never trusted. Ask-or-act, trajectory,
and efficiency signals are recorded alongside, so every report is one pass over scores.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from askoract.agent.types import AgentResult, Outcome
from askoract.db.base import Database, QueryError, QueryResult
from askoract.eval.cases import Label, TestCase
from askoract.eval.compare import compare_results
from askoract.eval.validate import run_gold, tolerance_for

SCHEMA_TOOLS = frozenset({"list_tables", "describe_table", "sample_rows", "get_column_stats"})


class AskDecision(StrEnum):
    """The four cells of the ask-or-act confusion matrix."""

    ASKED_WHEN_NEEDED = "asked_when_needed"  # ambiguous, asked
    MISSED_ASK = "missed_ask"  # ambiguous, didn't ask
    UNNEEDED_ASK = "unneeded_ask"  # clear or unanswerable, asked anyway
    ACTED_WHEN_CLEAR = "acted_when_clear"  # clear or unanswerable, didn't ask


@dataclass
class CaseScore:
    case_id: str
    suite: str
    profile: str
    label: str
    trap: str | None
    tags: list[str]
    outcome: str
    correct: bool
    reason: str  # why it was scored wrong, or "ok"
    should_ask: bool
    asked: bool
    n_questions: int
    ask_decision: str
    declined: bool  # submitted status "cannot_answer"
    matched_interpretation: str | None  # which reading the SQL's result matches, if any
    explored_before_sql: bool | None  # None when no run_sql call was made
    sql_errors: int
    recovered_from_error: bool | None  # None when there were no SQL errors
    tool_calls: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    billed_usd: float
    latency_ms: float
    answer: str | None = None
    sql: str | None = None
    assumption: str | None = None
    error: str | None = None
    clarifications: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoldCache:
    """Gold results keyed by (variant, sql), shared across profiles in one eval run."""

    def __init__(self) -> None:
        self._results: dict[tuple[str, str], QueryResult] = {}

    def get(self, db: Database, variant: str, sql: str) -> QueryResult:
        key = (variant, sql)
        if key not in self._results:
            self._results[key] = run_gold(db, sql)
        return self._results[key]


def ask_decision(should_ask: bool, asked: bool) -> AskDecision:
    if should_ask:
        return AskDecision.ASKED_WHEN_NEEDED if asked else AskDecision.MISSED_ASK
    return AskDecision.UNNEEDED_ASK if asked else AskDecision.ACTED_WHEN_CLEAR


def _trajectory(result: AgentResult) -> tuple[bool | None, int, bool | None]:
    steps = result.steps
    sql_steps = [i for i, s in enumerate(steps) if s.tool == "run_sql"]
    explored = None
    if sql_steps:
        explored = any(s.tool in SCHEMA_TOOLS for s in steps[: sql_steps[0]])
    errors = [i for i in sql_steps if steps[i].is_error]
    recovered = None
    if errors:
        later_ok = any(not steps[i].is_error for i in sql_steps if i > errors[0])
        recovered = later_ok or result.outcome is Outcome.ANSWERED
    return explored, len(errors), recovered


def score_case(
    case: TestCase,
    result: AgentResult,
    db: Database,
    gold: GoldCache,
    *,
    suite: str,
    profile: str,
) -> CaseScore:
    final = result.final
    declined = final is not None and final.status == "cannot_answer"
    matched: str | None = None
    reason = "ok"

    if final is None:
        reason = f"no final answer ({result.outcome.value})"
    elif case.label is Label.UNANSWERABLE:
        if not declined:
            reason = "answered a question the data cannot answer"
    elif declined:
        reason = "declined a question the data can answer"
    else:
        try:
            pred = db.execute(final.sql or "", max_rows=5_000, timeout_s=60.0)
        except QueryError as exc:
            reason = f"final SQL fails: {exc}"
        else:
            tol = tolerance_for(case)
            if pred.truncated:
                reason = "final SQL returns more than 5000 rows"
            else:
                # Check the target first, so a match to it is never reported as another reading.
                ordered = sorted(case.interpretations, key=lambda i: i.id != case.target.id)
                detail = ""
                for interp in ordered:
                    gold_rows = gold.get(db, case.db_variant, interp.gold_sql).rows
                    cmp = compare_results(
                        gold_rows, pred.rows, ordered=case.compare.ordered, tol=tol
                    )
                    if cmp.match:
                        matched = interp.id
                        break
                    if interp is case.target:
                        detail = cmp.reason
                if matched is None:
                    reason = f"wrong result: {detail}"
                elif matched != case.target.id:
                    reason = f"answered a different reading ({matched})"

    correct = reason == "ok"
    explored, sql_errors, recovered = _trajectory(result)
    return CaseScore(
        case_id=case.id,
        suite=suite,
        profile=profile,
        label=case.label.value,
        trap=case.trap,
        tags=list(case.tags),
        outcome=result.outcome.value,
        correct=correct,
        reason=reason,
        should_ask=case.should_ask,
        asked=result.asked,
        n_questions=len(result.clarifications),
        ask_decision=ask_decision(case.should_ask, result.asked).value,
        declined=declined,
        matched_interpretation=matched,
        explored_before_sql=explored,
        sql_errors=sql_errors,
        recovered_from_error=recovered,
        tool_calls=len(result.steps),
        llm_calls=result.llm_calls,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_usd=round(result.cost_usd, 6),
        billed_usd=round(result.billed_usd, 6),
        latency_ms=round(result.latency_ms, 1),
        answer=final.answer if final else None,
        sql=final.sql if final else None,
        assumption=final.assumption if final else None,
        error=result.error,
        clarifications=[list(c) for c in result.clarifications],
    )
