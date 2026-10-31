"""Contract tests for the scaffolding interfaces: tools, tracing, LLM types, test cases."""

import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from askoract.config import Limits
from askoract.eval.cases import Label, TestCase
from askoract.llm.base import LLMResponse, ToolCall, Usage
from askoract.tools.base import TOOLS, Tool, ToolContext, ToolResult, Toolset
from askoract.tracing.tracer import JsonlSink, MemorySink, Tracer, read_trace
from askoract.users.base import NoUser


class EchoArgs(BaseModel):
    text: str = Field(description="Text to echo back.")


class EchoTool(Tool):
    name = "echo_test"
    input_model = EchoArgs

    def run(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(content=args.text)


@pytest.fixture
def ctx():
    return ToolContext(db=None, user=NoUser(), limits=Limits())  # type: ignore[arg-type]


def test_tool_spec_uses_injected_description():
    spec = EchoTool("Echo the text.").spec()
    assert spec.description == "Echo the text."
    assert spec.parameters["required"] == ["text"]
    assert spec.to_openai()["function"]["name"] == "echo_test"


def test_tool_bad_arguments_become_readable_error(ctx):
    result = EchoTool("d")({"wrong": 1}, ctx)
    assert result.is_error
    assert "text" in result.content and result.content.startswith("ERROR:")


def test_tool_valid_call(ctx):
    assert EchoTool("d")({"text": "hi"}, ctx).content == "hi"


def test_toolset_requires_descriptions():
    TOOLS.add("echo_test", EchoTool)
    try:
        ts = Toolset.from_names(["echo_test"], {"echo_test": "Echo."})
        assert ts.names() == ["echo_test"]
        with pytest.raises(KeyError, match="no description"):
            Toolset.from_names(["echo_test"], {})
    finally:
        TOOLS._items.pop("echo_test")


def test_tracer_fans_out_to_sinks(tmp_path):
    mem = MemorySink()
    path = tmp_path / "t.jsonl"
    tracer = Tracer([mem, JsonlSink(path)], case_id="A-001")
    tracer.emit("tool_call", tool="run_sql", ok=True)
    tracer.emit("llm_call", tokens=10)
    tracer.close()
    assert [e.type for e in mem.events] == ["tool_call", "llm_call"]
    rows = read_trace(path)
    assert rows[0]["context"] == {"case_id": "A-001"}
    assert rows[1]["seq"] == 2


def test_llm_response_as_message_serializes_arguments():
    resp = LLMResponse(text=None, tool_calls=[ToolCall("c1", "run_sql", {"sql": "SELECT 1"})])
    msg = resp.as_message()
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1"}
    assert Usage(1, 2) + Usage(3, 4) == Usage(4, 6)


def _interp(id_: str) -> dict:
    return {"id": id_, "intent": f"meaning {id_}", "gold_sql": "SELECT 1"}


def test_clear_case_targets_its_only_interpretation():
    case = TestCase(
        id="A-1", question="q", label="clear", db_variant="pristine", interpretations=[_interp("x")]
    )
    assert case.target.id == "x" and not case.should_ask


def test_ambiguous_case_targets_hidden_intent():
    case = TestCase(
        id="A-2",
        question="q",
        label="ambiguous",
        db_variant="pristine",
        interpretations=[_interp("net"), _interp("gross")],
        hidden_intent="gross",
    )
    assert case.target.id == "gross" and case.should_ask


@pytest.mark.parametrize(
    "fields",
    [
        {"label": "clear", "interpretations": []},
        {"label": "ambiguous", "interpretations": [_interp("a")], "hidden_intent": "a"},
        {"label": "ambiguous", "interpretations": [_interp("a"), _interp("b")]},
        {"label": "unanswerable"},
        {"label": "unanswerable", "reason": "r", "interpretations": [_interp("a")]},
    ],
)
def test_malformed_cases_rejected(fields):
    with pytest.raises(ValidationError):
        TestCase(id="bad", question="q", db_variant="pristine", **fields)


def test_unanswerable_case():
    case = TestCase(
        id="A-3", question="q", label="unanswerable", db_variant="pristine", reason="no data"
    )
    assert case.label is Label.UNANSWERABLE and case.target is None
