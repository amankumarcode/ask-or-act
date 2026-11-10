"""Validate a suite by running its gold SQL. A suite with any error is not used for evaluation.

Checks per case:
  - every gold query runs on the case's variant and returns 1..MAX_GOLD_ROWS rows;
  - an ambiguous case's interpretations give different results (otherwise it isn't ambiguous);
  - `naive_sql`, if given, gives a different result from the target (otherwise the trap is inert).
Structural rules (label vs interpretations) are already enforced when the YAML is loaded.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from askoract.db.base import Database, QueryError, QueryResult
from askoract.eval.cases import Label, Suite, TestCase
from askoract.eval.compare import Tolerance, compare_results

MAX_GOLD_ROWS = 5_000
GOLD_TIMEOUT_S = 60.0


@dataclass
class CaseCheck:
    case_id: str
    label: str
    trap: str | None
    errors: list[str] = field(default_factory=list)
    gold_rows: dict[str, int] = field(default_factory=dict)  # interpretation -> row count
    preview: str = ""  # first cell of the target result, for eyeballing

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class SuiteReport:
    suite: str
    checks: list[CaseCheck]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def label_counts(self) -> Counter:
        return Counter(c.label for c in self.checks)

    @property
    def trap_counts(self) -> Counter:
        return Counter(c.trap or "-" for c in self.checks)


def run_gold(db: Database, sql: str) -> QueryResult:
    return db.execute(sql, max_rows=MAX_GOLD_ROWS, timeout_s=GOLD_TIMEOUT_S)


def tolerance_for(case: TestCase) -> Tolerance:
    return Tolerance(rel=case.compare.rel_tol, abs=case.compare.abs_tol)


def check_case(case: TestCase, db: Database) -> CaseCheck:
    check = CaseCheck(case.id, case.label.value, case.trap)
    results: dict[str, QueryResult] = {}
    for interp in case.interpretations:
        try:
            res = run_gold(db, interp.gold_sql)
        except QueryError as exc:
            check.errors.append(f"[{interp.id}] gold_sql fails: {exc}")
            continue
        if not res.rows:
            check.errors.append(f"[{interp.id}] gold_sql returns no rows")
        if res.truncated:
            check.errors.append(f"[{interp.id}] gold_sql returns more than {MAX_GOLD_ROWS} rows")
        results[interp.id] = res
        check.gold_rows[interp.id] = len(res.rows)

    tol = tolerance_for(case)
    if case.label is Label.AMBIGUOUS and len(results) == len(case.interpretations):
        ids = list(results)
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                same = compare_results(
                    results[a].rows, results[b].rows, ordered=case.compare.ordered, tol=tol
                ).match
                if same:
                    check.errors.append(f"interpretations {a!r} and {b!r} give the same result")

    target = case.target
    if target and target.id in results:
        rows = results[target.id].rows
        check.preview = repr(rows[0][0]) if rows and rows[0] else ""
        if len(rows) > 1:
            check.preview += f" (+{len(rows) - 1} rows)"
        if case.naive_sql:
            try:
                naive = run_gold(db, case.naive_sql)
            except QueryError as exc:
                check.errors.append(f"naive_sql fails: {exc}")
            else:
                if compare_results(rows, naive.rows, ordered=case.compare.ordered, tol=tol).match:
                    check.errors.append("naive_sql gives the gold result; the trap does not bite")
    return check


def validate_suite(suite: Suite, open_db: Callable[[str], Database]) -> SuiteReport:
    dbs: dict[str, Database] = {}
    try:
        checks = []
        for case in suite.cases:
            if case.db_variant not in dbs:
                dbs[case.db_variant] = open_db(case.db_variant)
            checks.append(check_case(case, dbs[case.db_variant]))
        return SuiteReport(suite.name, checks)
    finally:
        for db in dbs.values():
            db.close()
