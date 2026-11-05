"""LLMClient over LiteLLM: one thin adapter for every provider.

The model string picks the provider (`anthropic/...`, `openai/...`, `openrouter/...`,
`ollama/...`). This module is the only place that knows about LiteLLM. Its job:
  - build the request (no sampling params unless configured; tool_choice "auto");
  - add provider prompt-caching markers where supported;
  - serve repeats from the response cache;
  - turn the reply into a provider-neutral LLMResponse with usage and cost;
  - emit one `llm_call` trace event per call.
"""

import copy
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from askoract.config import LLMConfig, ModelPrice
from askoract.llm.base import LLMResponse, Message, ToolCall, ToolSpec, Usage
from askoract.llm.cache import ResponseCache, request_key
from askoract.tracing.tracer import Tracer

# Provider fields on the assistant message that must be sent back on the next turn.
_ECHO_FIELDS = ("thinking_blocks",)


class LLMError(RuntimeError):
    """A failed LLM call, with a message fit for the CLI."""


def _supports_prompt_caching(model: str) -> bool:
    return model.startswith(("anthropic/", "claude-"))


def add_cache_markers(messages: list[Message]) -> list[Message]:
    """Mark the system prompt and the newest message as cache breakpoints (Anthropic).

    The conversation only ever grows by appending, so each call reuses the prefix
    cached by the previous one.
    """
    out = copy.deepcopy(messages)
    targets = [m for m in out if m["role"] == "system"][:1]
    if out and out[-1]["role"] in ("user", "tool"):
        targets.append(out[-1])
    for msg in targets:
        content = msg.get("content")
        if isinstance(content, str) and content:
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
    return out


def _parse_arguments(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # The tool rejects this with a readable error, so the model can retry.
        return {"__invalid_json__": raw}
    return value if isinstance(value, dict) else {"__invalid_json__": raw}


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    details = getattr(raw, "prompt_tokens_details", None)
    cache_read = getattr(raw, "cache_read_input_tokens", None) or (
        getattr(details, "cached_tokens", None) if details else None
    )
    return Usage(
        input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        output_tokens=getattr(raw, "completion_tokens", 0) or 0,
        cache_read_tokens=cache_read or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )


def _to_dict(resp: LLMResponse) -> dict[str, Any]:
    data = asdict(resp)
    data.pop("cached")
    return data


def _from_dict(data: dict[str, Any], *, cached: bool) -> LLMResponse:
    return LLMResponse(
        text=data.get("text"),
        tool_calls=[ToolCall(**tc) for tc in data.get("tool_calls", [])],
        usage=Usage(**data.get("usage", {})),
        cost_usd=data.get("cost_usd", 0.0),
        latency_ms=data.get("latency_ms", 0.0),
        model=data.get("model", ""),
        stop_reason=data.get("stop_reason"),
        cached=cached,
        extra=data.get("extra", {}),
    )


class LiteLLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        cache: ResponseCache | None = None,
        prices: dict[str, ModelPrice] | None = None,
        tracer: Tracer | None = None,
        role: str = "agent",  # labels trace events: "agent" | "simulated_user" | ...
        completion_fn: Callable[..., Any] | None = None,  # injectable for tests
    ) -> None:
        self.config = config
        self.model = config.model
        self.cache = cache
        self.prices = prices or {}
        self.tracer = tracer or Tracer()
        self.role = role
        self._completion_fn = completion_fn

    def build_request(self, messages: list[Message], tools: list[ToolSpec] | None) -> dict:
        cfg = self.config
        prepared = (
            add_cache_markers(messages)
            if cfg.prompt_caching and _supports_prompt_caching(cfg.model)
            else messages
        )
        request: dict[str, Any] = {
            "model": cfg.model,
            "messages": prepared,
            "max_tokens": cfg.max_tokens,
            **cfg.params,
        }
        if cfg.temperature is not None:
            request["temperature"] = cfg.temperature
        if tools:
            request["tools"] = [t.to_openai() for t in tools]
            request["tool_choice"] = "auto"  # forced tool use is rejected by current models
        return request

    def complete(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> LLMResponse:
        request = self.build_request(messages, tools)
        key = request_key(request)
        # `is not None`, not truthiness: an empty cache has len() == 0.
        hit = self.cache.get(key) if self.cache is not None else None
        if hit is not None:
            resp = _from_dict(hit, cached=True)
        else:
            resp = self._call(request)
            if self.cache is not None:
                self.cache.put(key, self.model, _to_dict(resp))
        self.tracer.emit(
            "llm_call",
            role=self.role,
            model=self.model,
            request_key=key,
            n_messages=len(messages),
            n_tools=len(tools or []),
            cached=resp.cached,
            text=resp.text,
            tool_calls=[asdict(tc) for tc in resp.tool_calls],
            stop_reason=resp.stop_reason,
            usage=asdict(resp.usage),
            cost_usd=resp.cost_usd,
            billed_usd=resp.billed_usd,
            latency_ms=round(resp.latency_ms, 1),
        )
        return resp

    def _call(self, request: dict[str, Any]) -> LLMResponse:
        import litellm  # deferred: importing litellm takes about a second

        completion = self._completion_fn or litellm.completion
        if self._completion_fn is None:
            env = litellm.validate_environment(self.model)
            if not env.get("keys_in_environment", True):
                missing = ", ".join(env.get("missing_keys", [])) or "an API key"
                raise LLMError(f"{self.model} needs {missing}. Add it to .env (see .env.example).")
        start = time.perf_counter()
        try:
            raw = completion(
                **request, timeout=self.config.timeout_s, num_retries=self.config.max_retries
            )
        except Exception as exc:  # litellm maps every provider error to its own types
            raise LLMError(f"{self.model} call failed: {type(exc).__name__}: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        choice = raw.choices[0]
        message = choice.message
        extra = {name: value for name in _ECHO_FIELDS if (value := getattr(message, name, None))}
        usage = _usage(getattr(raw, "usage", None))
        return LLMResponse(
            text=message.content or None,
            tool_calls=[
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_arguments(tc.function.arguments),
                )
                for tc in (message.tool_calls or [])
            ],
            usage=usage,
            cost_usd=self._cost(raw, usage),
            latency_ms=latency_ms,
            model=getattr(raw, "model", None) or self.model,
            stop_reason=choice.finish_reason,
            extra=json.loads(json.dumps(extra, default=lambda o: getattr(o, "__dict__", str(o)))),
        )

    def _cost(self, raw: Any, usage: Usage) -> float:
        price = self.prices.get(self.model)
        if price is None:
            try:
                import litellm

                return float(litellm.completion_cost(completion_response=raw) or 0.0)
            except Exception:
                return 0.0  # unknown model and no configured price: report 0, don't fail
        uncached = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
        return (
            max(uncached, 0) * price.input_per_mtok
            + usage.cache_read_tokens * price.input_per_mtok * 0.1
            + usage.cache_write_tokens * price.input_per_mtok * 1.25
            + usage.output_tokens * price.output_per_mtok
        ) / 1_000_000
