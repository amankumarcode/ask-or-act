"""The agent loop, driven by a scripted FakeLLM against the tiny database (no network)."""

import pytest

from askoract.agent.loop import NUDGE, Agent
from askoract.agent.types import Outcome
from askoract.config import Limits
from askoract.llm.client import LLMError
from askoract.tools import TOOLS, ToolContext, Toolset
from askoract.tracing import MemorySink, Tracer
from askoract.users import ScriptedUser
from tests.fakes import FakeLLM, final, text_turn, tool_turn

ALL_TOOLS = [
    "list_tables",
    "describe_table",
    "sample_rows",
    "get_column_stats",
    "run_sql",
    "ask_user",
    "final_answer",
]
COUNT_SQL = "SELECT COUNT(*) FROM Customers WHERE Country = 'Germany'"


def make(db, responses, *, limits=None, user=None, tools=ALL_TOOLS):
    sink = MemorySink()
    tracer = Tracer([sink])
    llm = FakeLLM(responses)
    limits = limits or Limits()
    agent = Agent(
        llm,
        Toolset.from_names(tools, {t: f"desc {t}" for t in tools}),
        "SYSTEM",
        limits,
        tracer,
    )
    ctx = ToolContext(db=db, user=user or ScriptedUser(), limits=limits, tracer=tracer)
    return agent, ctx, llm, sink


def test_explore_then_answer(db):
    agent, ctx, llm, sink = make(
        db,
        [
            tool_turn(("list_tables", {})),
            tool_turn(("describe_table", {"table": "Customers"})),
            tool_turn(("run_sql", {"sql": COUNT_SQL})),
            tool_turn(final(COUNT_SQL, "2 customers")),
        ],
    )
    result = agent.run("How many customers are in Germany?", ctx)
    assert result.outcome is Outcome.ANSWERED
    assert result.final.answer == "2 customers" and result.final.sql == COUNT_SQL
    assert [s.tool for s in result.steps] == [
        "list_tables",
        "describe_table",
        "run_sql",
        "final_answer",
    ]
    assert result.llm_calls == 4
    assert result.usage.input_tokens == 400 and result.cost_usd == pytest.approx(0.04)
    assert not result.asked

    # The conversation sent to the model is well formed.
    messages, tools = llm.requests[-1]
    assert [m["role"] for m in messages[:2]] == ["system", "user"]
    assert messages[0]["content"] == "SYSTEM"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert tool_msgs[-1]["tool_call_id"] == "call_0_run_sql"
    assert "2" in tool_msgs[-1]["content"]
    assert {t.name for t in tools} == set(ALL_TOOLS)

    # Every message, LLM call is not emitted here (FakeLLM), tool call and the run end are traced.
    types = [e.type for e in sink.events]
    assert types[0] == "message" and "run_start" in types and types[-1] == "run_end"
    assert len(sink.of_type("tool_call")) == 4


def test_recovers_from_sql_error(db):
    agent, ctx, _, _ = make(
        db,
        [
            tool_turn(("run_sql", {"sql": "SELECT * FROM Order Details"})),
            tool_turn(("run_sql", {"sql": 'SELECT COUNT(*) FROM "Order Details"'})),
            tool_turn(final('SELECT COUNT(*) FROM "Order Details"')),
        ],
    )
    result = agent.run("How many order lines?", ctx)
    assert result.outcome is Outcome.ANSWERED
    assert [s.is_error for s in result.steps] == [True, False, False]
    assert "double-quoted" in result.steps[0].output


def test_unknown_tool_is_an_error_the_model_sees(db):
    agent, ctx, llm, _ = make(
        db, [tool_turn(("drop_everything", {})), tool_turn(final("SELECT 1"))]
    )
    result = agent.run("q", ctx)
    assert result.steps[0].is_error and "unknown tool" in result.steps[0].output
    assert result.outcome is Outcome.ANSWERED


def test_nudges_once_then_gives_up(db):
    agent, ctx, llm, _ = make(db, [text_turn("The answer is 2."), text_turn("Still 2.")])
    result = agent.run("q", ctx)
    assert result.outcome is Outcome.NO_FINAL_ANSWER
    assert llm.requests[1][0][-1] == {"role": "user", "content": NUDGE}


def test_nudge_can_recover(db):
    agent, ctx, _, _ = make(db, [text_turn("It's 2."), tool_turn(final(COUNT_SQL))])
    assert agent.run("q", ctx).outcome is Outcome.ANSWERED


def test_step_limit(db):
    agent, ctx, _, _ = make(db, [tool_turn(("list_tables", {}))] * 3, limits=Limits(max_steps=3))
    result = agent.run("q", ctx)
    assert result.outcome is Outcome.STEP_LIMIT and result.llm_calls == 3


def test_cost_budget(db):
    agent, ctx, _, _ = make(
        db,
        [tool_turn(("list_tables", {}), cost=0.3)] * 5,
        limits=Limits(max_cost_usd=0.5),
    )
    result = agent.run("q", ctx)
    assert result.outcome is Outcome.BUDGET_EXCEEDED and result.llm_calls == 2


def test_clarifying_question_then_answer(db):
    user = ScriptedUser("Only Germany, please.")
    agent, ctx, llm, _ = make(
        db,
        [
            tool_turn(("ask_user", {"question": "Which country?"})),
            tool_turn(final(COUNT_SQL)),
        ],
        user=user,
    )
    result = agent.run("How many customers in the region?", ctx)
    assert result.asked
    assert result.clarifications == [("Which country?", "Only Germany, please.")]
    assert "Only Germany" in llm.requests[1][0][-1]["content"]


def test_parallel_tool_calls_in_one_turn(db):
    agent, ctx, llm, _ = make(
        db,
        [
            tool_turn(("describe_table", {"table": "Customers"}), ("list_tables", {})),
            tool_turn(final(COUNT_SQL)),
        ],
    )
    result = agent.run("q", ctx)
    tool_msgs = [m for m in llm.requests[1][0] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [
        "call_0_describe_table",
        "call_1_list_tables",
    ]
    assert result.outcome is Outcome.ANSWERED


def test_broken_final_sql_is_bounced_back(db):
    agent, ctx, _, _ = make(db, [tool_turn(final("SELEC 1")), tool_turn(final(COUNT_SQL))])
    result = agent.run("q", ctx)
    assert result.steps[0].is_error and result.outcome is Outcome.ANSWERED


def test_cannot_answer(db):
    agent, ctx, _, _ = make(
        db, [tool_turn(final(None, "No marketing spend data.", status="cannot_answer"))]
    )
    result = agent.run("What was our marketing spend?", ctx)
    assert result.outcome is Outcome.CANNOT_ANSWER


def test_llm_error_ends_run(db):
    class Broken:
        model = "broken"

        def complete(self, messages, tools=None):
            raise LLMError("no key")

    agent, ctx, _, _ = make(db, [])
    agent.llm = Broken()
    result = agent.run("q", ctx)
    assert result.outcome is Outcome.ERROR and result.error == "no key"


def test_baseline_profile_cannot_call_tools_it_lacks(db):
    agent, ctx, _, _ = make(
        db,
        [tool_turn(("describe_table", {"table": "Customers"})), tool_turn(final(COUNT_SQL))],
        tools=["run_sql", "final_answer"],
    )
    result = agent.run("q", ctx)
    assert result.steps[0].is_error and "unknown tool" in result.steps[0].output
    assert "describe_table" in TOOLS  # registered globally, just not in this toolset


def test_assumption_check_leads_to_a_question(db):
    from askoract.tools import ToolContext

    with_assumption = (
        "final_answer",
        {"status": "answered", "answer": "2", "sql": COUNT_SQL, "assumption": "Germany only."},
    )
    user = ScriptedUser("Yes, only Germany.")
    agent, ctx, llm, _ = make(
        db,
        [
            tool_turn(with_assumption),
            tool_turn(("ask_user", {"question": "Only Germany?"})),
            tool_turn(with_assumption),
        ],
        user=user,
    )
    ctx = ToolContext(db=db, user=user, limits=Limits(), assumption_check=True)
    result = agent.run("How many customers in the region?", ctx)
    assert result.outcome is Outcome.ANSWERED and result.asked
    assert [s.tool for s in result.steps] == ["final_answer", "ask_user", "final_answer"]
    assert "Check your assumption" in result.steps[0].output
