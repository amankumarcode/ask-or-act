"""Run agent profiles over benchmark cases and score every run.

    for each profile, benchmark, case:
        env    = benchmark.environment(case, tracer)    # db + simulated user (+ extra tools)
        result = agent.run(case.question)               # traced to traces/<profile>/<case>.jsonl
        score  = score_case(...)                        # appended to results.jsonl

Sequential for now; the report and concurrency arrive with the comparison milestone.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from askoract.benchmarks.base import Benchmark
from askoract.config import AgentProfile, Settings
from askoract.eval.cases import TestCase
from askoract.eval.scorer import CaseScore, GoldCache, score_case
from askoract.factory import build_agent
from askoract.llm.base import LLMClient
from askoract.runs import Run, new_run
from askoract.tools import ToolContext
from askoract.tracing.tracer import Sink

RESULTS_FILE = "results.jsonl"


@dataclass
class EvalRun:
    run: Run
    scores: list[CaseScore] = field(default_factory=list)


def select_cases(
    cases: list[TestCase], *, limit: int | None = None, case_ids: list[str] | None = None
) -> list[TestCase]:
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    return cases[:limit] if limit else cases


def run_eval(
    settings: Settings,
    profiles: list[AgentProfile],
    benchmarks: list[Benchmark],
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    llm_factory: Callable[[AgentProfile, TestCase], LLMClient] | None = None,
    sinks: list[Sink] | None = None,
    on_score: Callable[[CaseScore, int, int], None] | None = None,
) -> EvalRun:
    plan = [
        (profile, bench, case)
        for profile in profiles
        for bench in benchmarks
        for case in select_cases(list(bench.cases()), limit=limit, case_ids=case_ids)
    ]
    run = new_run(
        settings,
        "eval",
        profiles,
        extra={
            "benchmarks": [b.name for b in benchmarks],
            "limit": limit,
            "case_ids": case_ids,
            "n_runs": len(plan),
        },
    )
    out = EvalRun(run)
    gold = GoldCache()
    results_path = run.dir / RESULTS_FILE

    for n, (profile, bench, case) in enumerate(plan, start=1):
        tracer = run.tracer(
            f"{profile.name}/{case.id}", sinks=sinks, profile=profile.name, suite=bench.name
        )
        env = bench.environment(case, tracer)
        try:
            agent = build_agent(
                profile,
                env.db,
                settings,
                tracer=tracer,
                llm=llm_factory(profile, case) if llm_factory else None,
                extra_tools=env.extra_tools,
            )
            ctx = ToolContext(db=env.db, user=env.user, limits=profile.limits, tracer=tracer)
            result = agent.run(case.question, ctx)
            scorer = env.scorer or score_case
            score = scorer(case, result, env.db, gold, suite=bench.name, profile=profile.name)
        finally:
            tracer.close()
            env.db.close()
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(score.to_dict(), default=str) + "\n")
        out.scores.append(score)
        if on_score:
            on_score(score, n, len(plan))
    return out
