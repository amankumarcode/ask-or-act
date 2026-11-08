"""Simulated user: prompt, consistency across turns, leak signals, and use inside the agent."""

import json

import pytest

from askoract import api
from askoract.agent.loop import Agent
from askoract.agent.types import Outcome
from askoract.config import Limits, load_settings
from askoract.llm.client import LLMError
from askoract.prompts import prompt_text
from askoract.tools import ToolContext, Toolset
from askoract.tracing import MemorySink, Tracer, read_trace
from askoract.users import SimulatedUser, leak_signals
from tests.fakes import FakeLLM, final, text_turn, tool_turn

QUESTION = "What was revenue last year?"
INTENT = "Revenue after discounts, excluding freight, for the latest full year in the data."
SQL = "SELECT COUNT(*) FROM Customers"


@pytest.fixture
def template(repo_root):
    return prompt_text(load_settings(repo_root), "sim_user/v1")


def make_user(template, replies, tracer=None):
    llm = FakeLLM([text_turn(r) for r in replies])
    return SimulatedUser(
        llm, original_question=QUESTION, intent=INTENT, prompt_template=template, tracer=tracer
    ), llm


def test_system_prompt_holds_question_and_intent(template):
    user, _ = make_user(template, ["x"])
    assert QUESTION in user.system_prompt and INTENT in user.system_prompt
    assert "${" not in user.system_prompt
    assert "never mention tables" in user.system_prompt


def test_reply_and_history_stay_consistent(template):
    user, llm = make_user(template, ["  After discounts.  ", "No freight."])
    assert user.reply("Before or after discounts?") == "After discounts."
    assert user.reply("Include freight?") == "No freight."
    second_request = llm.requests[1][0]
    assert [m["role"] for m in second_request] == ["system", "user", "assistant", "user"]
    assert second_request[2]["content"] == "After discounts."
    assert llm.requests[1][1] is None  # the simulated user gets no tools


def test_empty_reply_falls_back(template):
    user, _ = make_user(template, [""])
    assert "no preference" in user.reply("Anything else?").lower()


@pytest.mark.parametrize(
    "reply, leaked",
    [
        ("After discounts, and leave out shipping costs.", []),
        ("Use SELECT SUM(x) for that.", ["SELECT"]),
        ("Filter on ShippedDate please.", ["ShippedDate"]),
        ("Use the `Status` field.", ["`Status`"]),
    ],
)
def test_leak_signals(reply, leaked):
    assert leak_signals(reply) == leaked


def test_reply_is_traced_with_leak_signals(template):
    sink = MemorySink()
    user, _ = make_user(template, ["Check the UnitPrice column."], tracer=Tracer([sink]))
    user.reply("Which price?")
    event = sink.of_type("user_reply")[0].data
    assert event["kind"] == "simulated" and event["leak_signals"] == ["UnitPrice"]


def test_agent_asks_and_uses_the_simulated_reply(db, template):
    user, _ = make_user(template, ["After discounts, no freight."])
    agent_llm = FakeLLM(
        [tool_turn(("ask_user", {"question": "Gross or net of discounts?"})), tool_turn(final(SQL))]
    )
    tools = ["run_sql", "ask_user", "final_answer"]
    agent = Agent(agent_llm, Toolset.from_names(tools, {t: t for t in tools}), "S", Limits())
    ctx = ToolContext(db=db, user=user, limits=Limits())
    result = agent.run(QUESTION, ctx)
    assert result.outcome is Outcome.ANSWERED
    assert result.clarifications == [("Gross or net of discounts?", "After discounts, no freight.")]
    assert agent_llm.requests[1][0][-1]["content"] == "User replied: After discounts, no freight."


def test_simulator_failure_ends_run_as_error(db, template):
    class Down:
        model = "down"

        def complete(self, messages, tools=None):
            raise LLMError("simulator unavailable")

    user = SimulatedUser(
        Down(), original_question=QUESTION, intent=INTENT, prompt_template=template
    )
    agent_llm = FakeLLM([tool_turn(("ask_user", {"question": "Which?"}))])
    tools = ["ask_user", "final_answer"]
    agent = Agent(agent_llm, Toolset.from_names(tools, {t: t for t in tools}), "S", Limits())
    result = agent.run(QUESTION, ToolContext(db=db, user=user, limits=Limits()))
    assert result.outcome is Outcome.ERROR
    assert "ask_user failed" in result.error and "simulator unavailable" in result.error


def test_api_ask_with_sim_intent(repo_root, tmp_path, db_path):
    import shutil

    built = tmp_path / "data" / "built" / "northwind"
    built.mkdir(parents=True)
    shutil.copyfile(db_path, built / "pristine.db")
    settings = load_settings(
        repo_root,
        [
            f"paths.data_dir={tmp_path / 'data'}",
            f"paths.runs_dir={tmp_path / 'runs'}",
            "cache.enabled=false",
        ],
    )
    agent_llm = FakeLLM(
        [tool_turn(("ask_user", {"question": "Gross or net?"})), tool_turn(final(SQL))]
    )
    user_llm = FakeLLM([text_turn("Net of discounts.")])
    out = api.ask(QUESTION, sim_intent=INTENT, llm=agent_llm, user_llm=user_llm, settings=settings)
    assert out.result.asked and out.result.outcome is Outcome.ANSWERED
    assert QUESTION in user_llm.requests[0][0][0]["content"]  # simulator saw the question
    manifest = json.loads((out.run.dir / "manifest.json").read_text())
    assert manifest["sim_intent"] == INTENT
    assert "sim_user/v1.md" in manifest["prompts"]
    replies = [e for e in read_trace(out.trace_path) if e["type"] == "user_reply"]
    assert replies[0]["data"]["reply"] == "Net of discounts."
