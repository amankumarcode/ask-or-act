"""The shipped test suites: structure always; gold SQL whenever the databases are built."""

from collections import Counter

import pytest

from askoract import api
from askoract.config import load_settings
from askoract.eval.cases import Suite

EXPECTED = {
    "northwind_a": {"clear": 30, "ambiguous": 20, "unanswerable": 10},
    "northwind_b": {"clear": 20, "ambiguous": 6, "unanswerable": 4},
}


@pytest.fixture(scope="module")
def settings(repo_root):
    return load_settings(repo_root)


def test_both_suites_are_discovered(settings):
    assert set(api.list_suites(settings)) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_suite_label_mix(settings, name):
    suite = api.get_suite(name, settings)
    assert isinstance(suite, Suite)
    assert dict(Counter(c.label.value for c in suite.cases)) == EXPECTED[name]


def test_every_suite_b_case_targets_a_trap_or_is_unanswerable(settings):
    suite = api.get_suite("northwind_b", settings)
    traps = {
        "cancelled_status",
        "legacy_customers",
        "amount_in_cents",
        "duplicate_join",
        "active_customer",
    }
    for c in suite.cases:
        assert c.trap in traps or c.label.value == "unanswerable", c.id
    assert {c.trap for c in suite.cases if c.trap} == traps


def test_simulated_intent_is_always_defined(settings):
    for name in EXPECTED:
        for c in api.get_suite(name, settings).cases:
            assert c.simulated_intent.strip(), c.id


def test_gold_sql_validates(settings):
    """Runs every gold query on the real databases; skipped until `make setup` has run."""
    for variant in ("pristine", "traps"):
        if not (settings.paths.data_dir / "built" / "northwind" / f"{variant}.db").is_file():
            pytest.skip("databases not built; run `make setup`")
    for report in api.validate_suites(settings=settings):
        bad = {c.case_id: c.errors for c in report.checks if not c.ok}
        assert not bad, f"{report.suite}: {bad}"


def test_oracle_scores_perfectly_and_naive_sql_fails(settings):
    """Gold SQL submitted as the answer scores correct; the trap-falling naive_sql does not."""
    from askoract.agent.types import AgentResult, FinalAnswer, Outcome
    from askoract.benchmarks import YamlSuiteBenchmark, load_suite
    from askoract.eval.scorer import GoldCache, score_case

    for variant in ("pristine", "traps"):
        if not (settings.paths.data_dir / "built" / "northwind" / f"{variant}.db").is_file():
            pytest.skip("databases not built; run `make setup`")

    gold = GoldCache()
    for name in EXPECTED:
        bench = YamlSuiteBenchmark(settings, load_suite(settings, name))
        dbs = {}
        try:
            for case in bench.cases():
                db = dbs.setdefault(case.db_variant, bench.open_db(case.db_variant))
                if case.target is None:
                    final = FinalAnswer(status="cannot_answer", answer="no data")
                else:
                    final = FinalAnswer(status="answered", answer="x", sql=case.target.gold_sql)
                oracle = AgentResult("q", Outcome.ANSWERED, final=final)
                s = score_case(case, oracle, db, gold, suite=name, profile="oracle")
                assert s.correct, f"{case.id}: {s.reason}"
                if case.naive_sql:
                    naive = FinalAnswer(status="answered", answer="x", sql=case.naive_sql)
                    s = score_case(
                        case,
                        AgentResult("q", Outcome.ANSWERED, final=naive),
                        db,
                        gold,
                        suite=name,
                        profile="naive",
                    )
                    assert not s.correct, f"{case.id}: naive_sql scored correct"
        finally:
            for db in dbs.values():
                db.close()
