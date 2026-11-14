"""Show one trace as the sequence of steps the agent took: in the terminal, or as a
self-contained HTML page.

A trace is the JSONL written by the tracer, so the viewer works for `ask` runs and for
every case of an `eval` run. If a score exists for the case it is shown at the top.
"""

import html
import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

OUTPUT_PREVIEW_LINES = 12


@dataclass
class TraceStep:
    kind: str  # "think" | "tool" | "ask" | "sim_llm"
    title: str
    body: str = ""
    detail: str = ""  # tool output, shown collapsed in HTML
    error: bool = False
    meta: str = ""


@dataclass
class TraceView:
    question: str = ""
    profile: str = ""
    case_id: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    final: dict[str, Any] | None = None
    outcome: str = ""
    error: str | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    score: dict[str, Any] | None = None


def _args(arguments: dict[str, Any]) -> str:
    if set(arguments) == {"sql"}:
        return arguments["sql"]
    return json.dumps(arguments, ensure_ascii=False)


def build_view(events: list[dict[str, Any]], score: dict[str, Any] | None = None) -> TraceView:
    view = TraceView(score=score)
    for e in events:
        d, ctx = e["data"], e.get("context", {})
        view.profile = view.profile or ctx.get("profile", "")
        # Eval traces are named "<profile>/<case>"; keep just the case id.
        view.case_id = view.case_id or ctx.get("case_id", "").rpartition("/")[2]
        if e["type"] == "run_start":
            view.question = d["question"]
        elif e["type"] == "llm_call":
            usage = d.get("usage", {})
            view.tokens_in += usage.get("input_tokens", 0)
            view.tokens_out += usage.get("output_tokens", 0)
            if d.get("role") == "agent":
                if d.get("text"):
                    view.steps.append(TraceStep("think", "Agent", d["text"]))
            else:
                view.cost_usd += d.get("cost_usd", 0.0)  # agent cost arrives in run_end
        elif e["type"] == "tool_call":
            tool, args = d["tool"], d.get("arguments", {})
            if tool == "ask_user":
                reply = d.get("data", {}).get("reply", d["output"])
                view.steps.append(
                    TraceStep("ask", "Agent asks", args.get("question", ""), detail=reply)
                )
            elif tool != "final_answer" or d["is_error"]:
                view.steps.append(
                    TraceStep(
                        "tool",
                        tool,
                        _args(args),
                        detail=d["output"],
                        error=d["is_error"],
                        meta=f"{d.get('latency_ms', 0):.0f} ms",
                    )
                )
        elif e["type"] == "run_end":
            view.final, view.outcome, view.error = d.get("final"), d["outcome"], d.get("error")
            view.llm_calls = d.get("llm_calls", 0)
            view.cost_usd += d.get("cost_usd", 0.0)
            view.latency_s = d.get("latency_ms", 0.0) / 1000
    return view


# -- terminal ---------------------------------------------------------------------------


def render_terminal(view: TraceView, console: Console) -> None:
    title = " · ".join(x for x in (view.profile, view.case_id) if x)
    console.rule(f"[bold]{title or 'trace'}")
    console.print(f"[bold]Q:[/] {view.question}")
    if view.score:
        s = view.score
        mark = "[green]✓ correct[/]" if s["correct"] else f"[red]✗ {s['reason']}[/]"
        console.print(
            f"[dim]{s['label']} case · trap: {s.get('trap') or '-'} · "
            f"decision: {s['ask_decision']}[/]  {mark}"
        )
    console.print()
    for i, step in enumerate(view.steps, 1):
        if step.kind == "think":
            console.print(f"[dim]{i:>2}.[/] [italic]{step.body}[/]")
        elif step.kind == "ask":
            console.print(f"[dim]{i:>2}.[/] [bold yellow]Agent asks:[/] {step.body}")
            console.print(f"    [bold green]User:[/] {step.detail}")
        else:
            colour = "red" if step.error else "cyan"
            console.print(f"[dim]{i:>2}.[/] [{colour}]{step.title}[/] [dim]{step.meta}[/]")
            if step.title == "run_sql":
                console.print(
                    Syntax(step.body, "sql", background_color="default", word_wrap=True),
                    width=110,
                )
            elif step.body and step.body != "{}":
                console.print(f"    {step.body}", highlight=False)
            lines = step.detail.splitlines()
            for line in lines[:OUTPUT_PREVIEW_LINES]:
                console.print(Text(f"      {line}", style="red" if step.error else "dim"))
            if len(lines) > OUTPUT_PREVIEW_LINES:
                console.print(f"      [dim]… {len(lines) - OUTPUT_PREVIEW_LINES} more lines[/]")
    console.print()
    if view.final:
        f = view.final
        parts: list[Any] = [Text(f["answer"], style="bold")]
        if f.get("assumption"):
            parts.append(Text(f"Assumption: {f['assumption']}", style="dim"))
        if f.get("sql"):
            parts.append(Syntax(f["sql"], "sql", background_color="default", word_wrap=True))
        console.print(Panel(Group(*parts), title=view.outcome, border_style="green"))
    else:
        console.print(Panel(view.error or "no final answer", title=view.outcome, style="red"))
    console.print(
        f"[dim]{view.llm_calls} LLM calls · {len(view.steps)} steps · "
        f"{view.tokens_in:,} in / {view.tokens_out:,} out tokens · "
        f"${view.cost_usd:.4f} · {view.latency_s:.1f}s[/]"
    )


# -- HTML -------------------------------------------------------------------------------

_CSS = """
:root { --bg:#fbfbfa; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e4e3de;
        --accent:#2f6fde; --ok:#1f8a4c; --bad:#c2361f; --ask:#a86400; --code:#f3f2ee; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151514; --fg:#ecebe6; --muted:#9a9993; --card:#1e1e1c; --line:#33332f;
          --accent:#7aa7ff; --ok:#5cc58a; --bad:#ff7a63; --ask:#f0b357; --code:#262624; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width:860px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; } .muted { color:var(--muted); font-size:13px; }
.badge { display:inline-block; padding:2px 8px; border-radius:99px; font-size:12px;
  font-weight:600; border:1px solid currentColor; margin-right:6px; }
.ok { color:var(--ok) } .bad { color:var(--bad) } .ask { color:var(--ask) }
ol { list-style:none; padding:0; margin:24px 0; counter-reset:step; }
li { counter-increment:step; background:var(--card); border:1px solid var(--line);
  border-radius:10px; padding:12px 14px; margin:10px 0; position:relative; }
li::before { content:counter(step); position:absolute; left:-30px; top:12px;
  color:var(--muted); font-size:12px; width:22px; text-align:right; }
li.error { border-color:var(--bad); } li.ask { border-color:var(--ask); }
.tool { font-weight:600; color:var(--accent); font-family:ui-monospace, Menlo, monospace; }
pre { background:var(--code); border-radius:8px; padding:10px 12px; overflow-x:auto;
  font:13px/1.45 ui-monospace, Menlo, monospace; margin:8px 0 0; white-space:pre-wrap; }
details summary { cursor:pointer; color:var(--muted); font-size:13px; margin-top:6px; }
.final { border:2px solid var(--ok); border-radius:12px; padding:14px 16px;
  background:var(--card); }
.final.none { border-color:var(--bad); } .answer { font-size:17px; font-weight:600; }
@media (max-width:600px) { li::before { display:none } }
"""


def _e(text: str) -> str:
    return html.escape(text or "")


def render_html(view: TraceView) -> str:
    items = []
    for step in view.steps:
        if step.kind == "think":
            items.append(f'<li><span class="muted">Agent</span><div>{_e(step.body)}</div></li>')
        elif step.kind == "ask":
            items.append(
                f'<li class="ask"><b class="ask">Agent asks</b><div>{_e(step.body)}</div>'
                f'<div style="margin-top:6px"><b class="ok">User</b> {_e(step.detail)}</div></li>'
            )
        else:
            cls = ' class="error"' if step.error else ""
            body = f"<pre>{_e(step.body)}</pre>" if step.body and step.body != "{}" else ""
            items.append(
                f'<li{cls}><span class="tool">{_e(step.title)}</span> '
                f'<span class="muted">{_e(step.meta)}</span>{body}'
                f"<details{' open' if step.error else ''}><summary>output</summary>"
                f"<pre>{_e(step.detail)}</pre></details></li>"
            )

    badges = []
    if view.score:
        s = view.score
        verdict = (
            '<span class="badge ok">correct</span>'
            if s["correct"]
            else f'<span class="badge bad">wrong: {_e(s["reason"])}</span>'
        )
        badges = [
            verdict,
            f'<span class="badge">{_e(s["label"])}</span>',
            f'<span class="badge">{_e(s["ask_decision"])}</span>',
        ]
        if s.get("trap"):
            badges.append(f'<span class="badge">trap: {_e(s["trap"])}</span>')

    if view.final:
        f = view.final
        sql = f"<pre>{_e(f['sql'])}</pre>" if f.get("sql") else ""
        assumption = (
            f'<div class="muted">Assumption: {_e(f["assumption"])}</div>'
            if f.get("assumption")
            else ""
        )
        final = (
            f'<div class="final"><div class="muted">{_e(view.outcome)}</div>'
            f'<div class="answer">{_e(f["answer"])}</div>{assumption}{sql}</div>'
        )
    else:
        final = (
            f'<div class="final none"><b class="bad">{_e(view.outcome)}</b> '
            f"{_e(view.error or 'no final answer')}</div>"
        )

    title = " · ".join(x for x in (view.profile, view.case_id) if x) or "Trace"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title><style>{_CSS}</style></head>
<body><main>
<div class="muted">{_e(title)}</div>
<h1>{_e(view.question)}</h1>
<div>{"".join(badges)}</div>
<ol>{"".join(items)}</ol>
{final}
<p class="muted">{view.llm_calls} LLM calls · {len(view.steps)} steps ·
{view.tokens_in:,} in / {view.tokens_out:,} out tokens · ${view.cost_usd:.4f} ·
{view.latency_s:.1f}s</p>
</main></body></html>
"""
