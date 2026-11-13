"""Run agent profiles over benchmark cases and score every run.

    for each profile, benchmark, case (in parallel, `concurrency` at a time):
        env    = benchmark.environment(case, tracer)    # db + simulated user (+ extra tools)
        result = agent.run(case.question)               # traced to traces/<profile>/<case>.jsonl
        score  = score_case(...)                        # appended to results.jsonl

Resumable: given an existing run, (profile, case) pairs already in its results.jsonl are
skipped. Budgeted: once the money actually billed reaches `max_cost_usd`, no new cases
start (cases in flight finish, each under its own per-question cap).
"""

import json
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

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

Task = tuple[AgentProfile, Benchmark, TestCase]


@dataclass
class EvalRun:
    run: Run
    scores: list[CaseScore] = field(default_factory=list)  # every score, including resumed ones
    skipped_for_budget: int = 0
    resumed: int = 0


def select_cases(
    cases: list[TestCase], *, limit: int | None = None, case_ids: list[str] | None = None
) -> list[TestCase]:
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
    return cases[:limit] if limit else cases


def load_scores(path: Path) -> list[CaseScore]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return [CaseScore(**json.loads(line)) for line in fh if line.strip()]


def _run_one(
    settings: Settings,
    task: Task,
    run: Run,
    gold: GoldCache,
    llm_factory: Callable[[AgentProfile, TestCase], LLMClient] | None,
    sinks: list[Sink] | None,
) -> CaseScore:
    profile, bench, case = task
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
        return scorer(case, result, env.db, gold, suite=bench.name, profile=profile.name)
    finally:
        tracer.close()
        env.db.close()


def run_eval(
    settings: Settings,
    profiles: list[AgentProfile],
    benchmarks: list[Benchmark],
    *,
    limit: int | None = None,
    case_ids: list[str] | None = None,
    concurrency: int = 1,
    max_cost_usd: float | None = None,
    resume: Run | None = None,
    llm_factory: Callable[[AgentProfile, TestCase], LLMClient] | None = None,
    sinks: list[Sink] | None = None,
    on_score: Callable[[CaseScore, int, int], None] | None = None,
) -> EvalRun:
    plan: list[Task] = [
        (profile, bench, case)
        for profile in profiles
        for bench in benchmarks
        for case in select_cases(list(bench.cases()), limit=limit, case_ids=case_ids)
    ]
    run = resume or new_run(
        settings,
        "eval",
        profiles,
        extra={
            "benchmarks": [b.name for b in benchmarks],
            "limit": limit,
            "case_ids": case_ids,
            "n_runs": len(plan),
            "max_cost_usd": max_cost_usd,
        },
    )
    results_path = run.dir / RESULTS_FILE
    previous = load_scores(results_path)
    done = {(s.profile, s.case_id) for s in previous}
    todo = [t for t in plan if (t[0].name, t[2].id) not in done]
    out = EvalRun(run, scores=list(previous), resumed=len(plan) - len(todo))

    gold = GoldCache()
    lock = threading.Lock()
    billed = 0.0
    finished = out.resumed

    def record(score: CaseScore) -> None:
        nonlocal billed, finished
        with lock:
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(score.to_dict(), default=str) + "\n")
            out.scores.append(score)
            billed += score.billed_usd
            finished += 1
            if on_score:
                on_score(score, finished, len(plan))

    def over_budget() -> bool:
        return max_cost_usd is not None and billed >= max_cost_usd

    queue = list(todo)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        running: set[Future] = set()
        while queue or running:
            while queue and len(running) < max(1, concurrency) and not over_budget():
                task = queue.pop(0)
                running.add(pool.submit(_run_one, settings, task, run, gold, llm_factory, sinks))
            if not running:
                break  # budget reached with nothing in flight
            completed, running = wait(running, return_when=FIRST_COMPLETED)
            for fut in completed:
                record(fut.result())
    out.skipped_for_budget = len(queue)
    return out
