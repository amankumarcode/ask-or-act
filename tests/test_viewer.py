"""Trace viewer: building steps from events and rendering them."""

from io import StringIO

from rich.console import Console

from askoract.tracing.viewer import build_view, render_html, render_terminal


def ev(type_, **data):
    return {"type": type_, "data": data, "context": {"profile": "full", "case_id": "full/A-1"}}


EVENTS = [
    ev("run_start", question="How many <active> products?", tools=[]),
    ev(
        "llm_call",
        role="agent",
        text="Let me look.",
        usage={"input_tokens": 100, "output_tokens": 10},
        cost_usd=0.001,
    ),
    ev(
        "tool_call",
        tool="run_sql",
        arguments={"sql": "SELEC 1"},
        output="ERROR: syntax",
        is_error=True,
        data={},
        latency_ms=1.0,
    ),
    ev(
        "tool_call",
        tool="ask_user",
        arguments={"question": "In stock or not discontinued?"},
        output="User replied: Not discontinued.",
        is_error=False,
        data={"question": "In stock or not discontinued?", "reply": "Not discontinued."},
        latency_ms=900.0,
    ),
    ev(
        "llm_call",
        role="simulated_user",
        text="Not discontinued.",
        usage={"input_tokens": 50, "output_tokens": 5},
        cost_usd=0.0002,
    ),
    ev(
        "tool_call",
        tool="final_answer",
        arguments={},
        output="Final answer recorded.",
        is_error=False,
        data={},
        latency_ms=0.1,
    ),
    ev(
        "run_end",
        outcome="answered",
        final={
            "status": "answered",
            "answer": "69",
            "sql": "SELECT 69",
            "assumption": "Active = not discontinued.",
        },
        error=None,
        steps=3,
        llm_calls=2,
        asked=True,
        cost_usd=0.002,
        latency_ms=3200.0,
    ),
]
SCORE = {
    "correct": True,
    "reason": "ok",
    "label": "ambiguous",
    "trap": "active_products",
    "ask_decision": "asked_when_needed",
}


def test_build_view():
    view = build_view(EVENTS, SCORE)
    assert view.case_id == "A-1" and view.profile == "full"
    assert [s.kind for s in view.steps] == ["think", "tool", "ask"]
    assert view.steps[1].error and view.steps[2].detail == "Not discontinued."
    assert view.final["answer"] == "69" and view.outcome == "answered"
    assert view.tokens_in == 150
    assert round(view.cost_usd, 4) == 0.0022  # agent cost plus the simulated user's


def test_terminal_render_mentions_every_step():
    buf = StringIO()
    render_terminal(build_view(EVENTS, SCORE), Console(file=buf, width=100))
    out = buf.getvalue()
    for text in (
        "Let me look.",
        "SELEC 1",
        "Agent asks:",
        "Not discontinued.",
        "✓ correct",
        "Active = not discontinued.",
    ):
        assert text in out, text


def test_html_is_standalone_and_escaped():
    page = render_html(build_view(EVENTS, SCORE))
    assert page.startswith("<!doctype html>") and "prefers-color-scheme: dark" in page
    assert "How many &lt;active&gt; products?" in page  # user text is escaped
    assert "<script" not in page and "http" not in page  # no external resources
    assert 'class="badge ok">correct' in page and "trap: active_products" in page


def test_no_final_answer_renders():
    events = [
        EVENTS[0],
        ev(
            "run_end",
            outcome="step_limit",
            final=None,
            error=None,
            steps=0,
            llm_calls=15,
            asked=False,
            cost_usd=0.1,
            latency_ms=1.0,
        ),
    ]
    assert "step_limit" in render_html(build_view(events))
