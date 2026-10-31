"""Provider-neutral LLM types.

Messages use the OpenAI chat format (role/content/tool_calls), which LiteLLM
translates for every provider. The agent depends on `LLMClient` only, so tests
plug in a scripted fake and other backends can be added without touching the loop.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Message = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """What the model sees for one tool: name, description, JSON-schema parameters."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model: str = ""
    cached: bool = False

    def as_message(self) -> Message:
        """The assistant message to append to the conversation."""
        msg: Message = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    # The OpenAI format carries arguments as a JSON string.
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        return msg


@runtime_checkable
class LLMClient(Protocol):
    model: str

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse: ...
