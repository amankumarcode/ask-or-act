"""The LiteLLM adapter, exercised offline through LiteLLM's own mock_response support."""

import json
from types import SimpleNamespace

import litellm
import pytest

from askoract.config import LLMConfig, ModelPrice
from askoract.llm.base import ToolSpec, Usage
from askoract.llm.cache import ResponseCache, request_key
from askoract.llm.client import LiteLLMClient, LLMError, add_cache_markers
from askoract.tracing import MemorySink, Tracer

SQL_TOOL = ToolSpec(
    "run_sql", "Run SQL.", {"type": "object", "properties": {"sql": {"type": "string"}}}
)
MESSAGES = [
    {"role": "system", "content": "SYSTEM"},
    {"role": "user", "content": "How many customers?"},
]


class CountingCompletion:
    """Wraps litellm.completion with a mock reply and counts real (non-cached) calls."""

    def __init__(self, **mock):
        self.mock = mock
        self.calls: list[dict] = []

    def __call__(self, **request):
        self.calls.append(request)
        return litellm.completion(**request, **self.mock)


def client(tmp_path, fn, **cfg):
    sink = MemorySink()
    c = LiteLLMClient(
        LLMConfig(model="anthropic/claude-sonnet-5-5", **cfg),
        cache=ResponseCache(tmp_path / "cache.sqlite"),
        tracer=Tracer([sink]),
        completion_fn=fn,
    )
    return c, sink


def test_parses_tool_calls_usage_and_cost(tmp_path):
    fn = CountingCompletion(
        mock_tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_sql", "arguments": json.dumps({"sql": "SELECT 1"})},
            }
        ]
    )
    c, sink = client(tmp_path, fn)
    resp = c.complete(MESSAGES, [SQL_TOOL])
    assert resp.tool_calls[0].name == "run_sql"
    assert resp.tool_calls[0].arguments == {"sql": "SELECT 1"}
    assert resp.usage.input_tokens > 0 and resp.cost_usd > 0  # LiteLLM knows Sonnet 5.5 prices
    event = sink.of_type("llm_call")[0].data
    assert event["role"] == "agent" and event["cached"] is False
    assert event["tool_calls"][0]["arguments"] == {"sql": "SELECT 1"}


def test_second_identical_call_is_served_from_cache(tmp_path):
    fn = CountingCompletion(mock_response="Hello")
    c, _ = client(tmp_path, fn)
    first = c.complete(MESSAGES)
    second = c.complete(MESSAGES)
    assert len(fn.calls) == 1
    assert second.cached and not first.cached
    assert second.text == first.text == "Hello"
    assert second.cost_usd == first.cost_usd and second.billed_usd == 0.0


def test_cache_survives_a_new_client(tmp_path):
    fn = CountingCompletion(mock_response="Hello")
    client(tmp_path, fn)[0].complete(MESSAGES)
    again, _ = client(tmp_path, fn)
    assert again.complete(MESSAGES).cached and len(fn.calls) == 1


def test_different_request_misses_cache(tmp_path):
    fn = CountingCompletion(mock_response="Hello")
    c, _ = client(tmp_path, fn)
    c.complete(MESSAGES)
    c.complete([*MESSAGES[:1], {"role": "user", "content": "Different question"}])
    assert len(fn.calls) == 2


def test_no_sampling_params_by_default_and_auto_tool_choice(tmp_path):
    c, _ = client(tmp_path, CountingCompletion(mock_response="x"))
    req = c.build_request(MESSAGES, [SQL_TOOL])
    assert "temperature" not in req
    assert req["tool_choice"] == "auto"
    c2, _ = client(tmp_path, CountingCompletion(mock_response="x"), temperature=0.0)
    assert c2.build_request(MESSAGES, None)["temperature"] == 0.0


def test_prompt_cache_markers_only_for_anthropic(tmp_path):
    c, _ = client(tmp_path, CountingCompletion(mock_response="x"))
    req = c.build_request(MESSAGES, None)
    assert req["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][-1]["content"][0]["text"] == "How many customers?"
    other = LiteLLMClient(LLMConfig(model="openai/gpt-5"), completion_fn=lambda **k: None)
    assert other.build_request(MESSAGES, None)["messages"] == MESSAGES


def test_add_cache_markers_does_not_mutate_input():
    before = json.dumps(MESSAGES)
    add_cache_markers(MESSAGES)
    assert json.dumps(MESSAGES) == before


def test_invalid_tool_json_is_kept_for_the_tool_to_reject(tmp_path):
    fn = CountingCompletion(
        mock_tool_calls=[
            {"id": "c", "type": "function", "function": {"name": "run_sql", "arguments": "{oops"}}
        ]
    )
    c, _ = client(tmp_path, fn)
    assert c.complete(MESSAGES, [SQL_TOOL]).tool_calls[0].arguments == {"__invalid_json__": "{oops"}


def test_configured_price_overrides_litellm(tmp_path):
    c = LiteLLMClient(
        LLMConfig(model="custom/model"),
        prices={"custom/model": ModelPrice(input_per_mtok=1.0, output_per_mtok=10.0)},
        completion_fn=lambda **k: None,
    )
    raw = SimpleNamespace()
    cost = c._cost(raw, Usage(input_tokens=1_000_000, output_tokens=100_000))
    assert cost == pytest.approx(2.0)


def test_provider_errors_become_llm_error(tmp_path):
    def boom(**_):
        raise RuntimeError("rate limited")

    c, _ = client(tmp_path, boom)
    with pytest.raises(LLMError, match="rate limited"):
        c.complete(MESSAGES)


def test_missing_api_key_is_explained(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = LiteLLMClient(LLMConfig(model="anthropic/claude-sonnet-5-5"))
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        c.complete(MESSAGES)


def test_request_key_ignores_dict_order():
    assert request_key({"a": 1, "b": [1, 2]}) == request_key({"b": [1, 2], "a": 1})
    assert request_key({"a": 1}) != request_key({"a": 2})
