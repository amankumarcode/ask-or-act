"""Test doubles for the LLM layer."""

from askoract.llm.base import LLMResponse, Message, ToolCall, ToolSpec, Usage


def tool_turn(*calls: tuple[str, dict], text: str | None = None, cost: float = 0.01) -> LLMResponse:
    """A scripted assistant turn that calls the given tools in order."""
    return LLMResponse(
        text=text,
        tool_calls=[
            ToolCall(f"call_{i}_{name}", name, args) for i, (name, args) in enumerate(calls)
        ],
        usage=Usage(input_tokens=100, output_tokens=20),
        cost_usd=cost,
        model="fake",
    )


def text_turn(text: str, cost: float = 0.01) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(100, 20), cost_usd=cost, model="fake")


def final(sql: str | None, answer: str = "ok", status: str = "answered") -> tuple[str, dict]:
    return ("final_answer", {"status": status, "answer": answer, "sql": sql, "assumption": None})


class FakeLLM:
    """Replays scripted responses in order and records every request it received."""

    model = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[list[Message], list[ToolSpec] | None]] = []

    def complete(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> LLMResponse:
        self.requests.append(([dict(m) for m in messages], tools))
        if not self._responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self._responses.pop(0)
