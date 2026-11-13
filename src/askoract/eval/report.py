"""Aggregate case scores into the results tables.

Writes, into the run directory:
    results.json   every case score plus the per-profile summary
    summary.md     the headline comparison table, per-trap and per-label breakdowns,
                   and the ask-or-act confusion matrix for each profile
"""

import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from askoract.eval.scorer import AskDecision, CaseScore

LABELS = ("clear", "ambiguous", "unanswerable")


@dataclass
class Rate:
    hits: int = 0
    n: int = 0

    @property
    def value(self) -> float | None:
        return self.hits / self.n if self.n else None

    def __str__(self) -> str:
        return f"{self.value:.0%} ({self.hits}/{self.n})" if self.n else "-"


@dataclass
class ProfileSummary:
    profile: str
    n: int
    accuracy: Rate
    by_label: dict[str, Rate]
    by_suite: dict[str, Rate]
    by_trap: dict[str, Rate]
    confusion: dict[str, int]  # AskDecision value -> count
    ask_recall: Rate  # asked on ambiguous cases
    ask_precision: Rate  # asks that were on ambiguous cases
    ambiguous_correct_without_asking: int  # guessed the hidden reading
    declined_unanswerable: Rate
    false_declines: int  # declined an answerable question
    explored_before_sql: Rate
    recovered_from_errors: Rate
    outcomes: dict[str, int]
    efficiency: dict[str, float] = field(default_factory=dict)


def _rate(scores: list[CaseScore], pred: Any = lambda s: s.correct) -> Rate:
    return Rate(sum(1 for s in scores if pred(s)), len(scores))


def _p50(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def summarize(
    scores: list[CaseScore], profile_order: list[str] | None = None
) -> dict[str, ProfileSummary]:
    """Per-profile summaries, in `profile_order` (else first appearance)."""
    by_profile: dict[str, list[CaseScore]] = {name: [] for name in profile_order or []}
    for s in scores:
        by_profile.setdefault(s.profile, []).append(s)
    by_profile = {k: v for k, v in by_profile.items() if v}

    out: dict[str, ProfileSummary] = {}
    for profile, rows in by_profile.items():
        confusion = {d.value: sum(s.ask_decision == d.value for s in rows) for d in AskDecision}
        ambiguous = [s for s in rows if s.should_ask]
        askers = [s for s in rows if s.asked]
        unanswerable = [s for s in rows if s.label == "unanswerable"]
        with_sql = [s for s in rows if s.explored_before_sql is not None]
        with_errors = [s for s in rows if s.recovered_from_error is not None]
        outcomes: dict[str, int] = defaultdict(int)
        for s in rows:
            outcomes[s.outcome] += 1

        out[profile] = ProfileSummary(
            profile=profile,
            n=len(rows),
            accuracy=_rate(rows),
            by_label={k: _rate(r) for k, r in _grouped(rows, lambda s: s.label).items()},
            by_suite={k: _rate(r) for k, r in _grouped(rows, lambda s: s.suite).items()},
            by_trap={k: _rate(r) for k, r in _grouped(rows, lambda s: s.trap).items() if k},
            confusion=confusion,
            ask_recall=_rate(ambiguous, lambda s: s.asked),
            ask_precision=_rate(askers, lambda s: s.should_ask),
            ambiguous_correct_without_asking=sum(1 for s in ambiguous if s.correct and not s.asked),
            declined_unanswerable=_rate(unanswerable, lambda s: s.declined),
            false_declines=sum(1 for s in rows if s.declined and s.label != "unanswerable"),
            explored_before_sql=_rate(with_sql, lambda s: s.explored_before_sql),
            recovered_from_errors=_rate(with_errors, lambda s: s.recovered_from_error),
            outcomes=dict(outcomes),
            efficiency={
                "tool_calls_mean": _mean([s.tool_calls for s in rows]),
                "tool_calls_p50": _p50([s.tool_calls for s in rows]),
                "llm_calls_mean": _mean([s.llm_calls for s in rows]),
                "input_tokens_mean": _mean([s.input_tokens for s in rows]),
                "output_tokens_mean": _mean([s.output_tokens for s in rows]),
                "cost_usd_mean": _mean([s.cost_usd for s in rows]),
                "cost_usd_total": sum(s.cost_usd for s in rows),
                "billed_usd_total": sum(s.billed_usd for s in rows),
                "latency_s_p50": _p50([s.latency_ms / 1000 for s in rows]),
                "latency_s_mean": _mean([s.latency_ms / 1000 for s in rows]),
            },
        )
    return out


def _grouped(rows: list[CaseScore], key: Any) -> dict[str, list[CaseScore]]:
    groups: dict[str, list[CaseScore]] = defaultdict(list)
    for s in rows:
        groups[key(s)].append(s)
    return dict(sorted(groups.items(), key=lambda kv: str(kv[0])))


# -- rendering -------------------------------------------------------------------------


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def headline_rows(summaries: dict[str, ProfileSummary]) -> list[list[str]]:
    profs = list(summaries.values())

    def row(name: str, fn: Any) -> list[str]:
        return [name, *[fn(p) for p in profs]]

    return [
        row("**Accuracy (all)**", lambda p: f"**{p.accuracy}**"),
        *[
            row(f"Accuracy: {label}", lambda p, lb=label: str(p.by_label.get(lb, Rate())))
            for label in LABELS
        ],
        row("Asked when ambiguous (recall)", lambda p: str(p.ask_recall)),
        row("Asks that were needed (precision)", lambda p: str(p.ask_precision)),
        row("Declined unanswerable", lambda p: str(p.declined_unanswerable)),
        row("Declined answerable (false declines)", lambda p: str(p.false_declines)),
        row("Explored schema before SQL", lambda p: str(p.explored_before_sql)),
        row("Recovered from SQL errors", lambda p: str(p.recovered_from_errors)),
        row("Tool calls / question (mean)", lambda p: f"{p.efficiency['tool_calls_mean']:.1f}"),
        row(
            "Tokens / question (in / out)",
            lambda p: (
                f"{p.efficiency['input_tokens_mean']:,.0f} / "
                f"{p.efficiency['output_tokens_mean']:,.0f}"
            ),
        ),
        row("Cost / question (mean)", lambda p: f"${p.efficiency['cost_usd_mean']:.4f}"),
        row("Latency / question (p50)", lambda p: f"{p.efficiency['latency_s_p50']:.1f}s"),
        row("Total cost", lambda p: f"${p.efficiency['cost_usd_total']:.3f}"),
    ]


def render_markdown(summaries: dict[str, ProfileSummary], title: str = "Results") -> str:
    names = list(summaries)
    profs = list(summaries.values())
    parts = [f"# {title}", "", _table(["Metric", *names], headline_rows(summaries))]

    suites = sorted({k for p in profs for k in p.by_suite})
    if len(suites) > 1:
        parts += ["", "## By suite", ""]
        rows = [[s, *[str(p.by_suite.get(s, Rate())) for p in profs]] for s in suites]
        parts.append(_table(["Suite", *names], rows))

    traps = sorted({k for p in profs for k in p.by_trap})
    if traps:
        parts += ["", "## By trap", ""]
        rows = [[t, *[str(p.by_trap.get(t, Rate())) for p in profs]] for t in traps]
        parts.append(_table(["Trap", *names], rows))

    parts += ["", "## Ask-or-act confusion matrix", ""]
    for p in profs:
        c = p.confusion
        parts += [
            f"**{p.profile}** (correct without asking on ambiguous: "
            f"{p.ambiguous_correct_without_asking})",
            "",
            _table(
                ["", "Asked", "Did not ask"],
                [
                    [
                        "Should ask (ambiguous)",
                        str(c["asked_when_needed"]),
                        str(c["missed_ask"]),
                    ],
                    [
                        "Should act (clear, unanswerable)",
                        str(c["unneeded_ask"]),
                        str(c["acted_when_clear"]),
                    ],
                ],
            ),
            "",
        ]

    failures = [
        f"`{o}` {n}"
        for p in profs
        for o, n in sorted(p.outcomes.items())
        if o not in ("answered", "cannot_answer")
    ]
    if failures:
        parts += ["Runs without a final answer: " + ", ".join(failures), ""]
    return "\n".join(parts).rstrip() + "\n"


def write_report(
    run_dir: Path,
    scores: list[CaseScore],
    title: str | None = None,
    profile_order: list[str] | None = None,
) -> Path:
    summaries = summarize(scores, profile_order)
    payload = {
        "summary": {name: _jsonable(asdict(s)) for name, s in summaries.items()},
        "scores": [s.to_dict() for s in scores],
    }
    (run_dir / "results.json").write_text(json.dumps(payload, indent=2, default=str))
    md = run_dir / "summary.md"
    md.write_text(render_markdown(summaries, title or f"Results: {run_dir.name}"))
    return md


def _jsonable(data: Any) -> Any:
    if isinstance(data, dict):
        if set(data) == {"hits", "n"}:  # a Rate
            n = data["n"]
            return {**data, "value": data["hits"] / n if n else None}
        return {k: _jsonable(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_jsonable(v) for v in data]
    return data
