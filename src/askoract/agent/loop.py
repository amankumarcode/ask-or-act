"""The agent loop. Plain on purpose: no framework, every decision visible.

    messages = [system, question]
    repeat up to max_steps:
        ask the model (with the profile's tools)
        no tool call   -> nudge once to call final_answer, then give up
        tool calls     -> run each, append results (errors included, so it can recover)
        final_answer   -> stop
    stop early if the per-question cost budget is spent

The loop is the same for every profile; profiles differ only in prompt, tools, model, limits.
"""

import time

from askoract.agent.types import AgentResult, Outcome, Step
from askoract.config import Limits
from askoract.llm.base import LLMClient, LLMResponse, Message
from askoract.llm.client import LLMError
from askoract.tools.base import ToolContext, ToolResult, Toolset
from askoract.tracing.tracer import Tracer

NUDGE = (
    "You stopped without calling a tool. Every task must end with the final_answer tool. "
    "If you already know the answer, call final_answer now; otherwise keep working with tools."
)


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        toolset: Toolset,
        system_prompt: str,
        limits: Limits,
        tracer: Tracer | None = None,
    ) -> None:
        self.llm = llm
        self.toolset = toolset
        self.system_prompt = system_prompt
        self.limits = limits
        self.tracer = tracer or Tracer()

    def run(self, question: str, ctx: ToolContext) -> AgentResult:
        start = time.perf_counter()
        result = AgentResult(question=question, outcome=Outcome.STEP_LIMIT)
        messages: list[Message] = []
        self._append(messages, {"role": "system", "content": self.system_prompt})
        self._append(messages, {"role": "user", "content": question})
        self.tracer.emit("run_start", question=question, tools=self.toolset.names())

        nudged = False
        specs = self.toolset.specs()
        for _turn in range(self.limits.max_steps):
            if result.cost_usd >= self.limits.max_cost_usd:
                result.outcome = Outcome.BUDGET_EXCEEDED
                break
            try:
                resp = self.llm.complete(messages, specs)
            except LLMError as exc:
                result.outcome, result.error = Outcome.ERROR, str(exc)
                break
            self._account(result, resp)
            self._append(messages, resp.as_message())

            if not resp.tool_calls:
                if nudged:
                    result.outcome = Outcome.NO_FINAL_ANSWER
                    break
                nudged = True
                self._append(messages, {"role": "user", "content": NUDGE})
                continue

            for call in resp.tool_calls:
                tool_result = self._run_tool(call.name, call.arguments, ctx)
                result.steps.append(
                    Step(
                        index=len(result.steps),
                        tool=call.name,
                        arguments=call.arguments,
                        output=tool_result.content,
                        is_error=tool_result.is_error,
                        latency_ms=tool_result.data.get("_latency_ms", 0.0),
                    )
                )
                self._append(
                    messages,
                    {"role": "tool", "tool_call_id": call.id, "content": tool_result.content},
                )
                if tool_result.final is not None:
                    result.final = tool_result.final

            if result.final is not None:
                result.outcome = (
                    Outcome.ANSWERED if result.final.status == "answered" else Outcome.CANNOT_ANSWER
                )
                break

        result.clarifications = list(ctx.clarifications)
        result.latency_ms = (time.perf_counter() - start) * 1000
        self.tracer.emit(
            "run_end",
            outcome=result.outcome.value,
            final=result.final.model_dump() if result.final else None,
            error=result.error,
            steps=len(result.steps),
            llm_calls=result.llm_calls,
            asked=result.asked,
            cost_usd=result.cost_usd,
            latency_ms=round(result.latency_ms, 1),
        )
        return result

    def _run_tool(self, name: str, arguments: dict, ctx: ToolContext) -> ToolResult:
        tool = self.toolset.get(name)
        start = time.perf_counter()
        if tool is None:
            res = ToolResult.error(
                f"unknown tool {name!r}. Available tools: {', '.join(self.toolset.names())}."
            )
        else:
            res = tool(arguments, ctx)
        latency_ms = (time.perf_counter() - start) * 1000
        res.data["_latency_ms"] = round(latency_ms, 1)
        self.tracer.emit(
            "tool_call",
            tool=name,
            arguments=arguments,
            output=res.content,
            is_error=res.is_error,
            data={k: v for k, v in res.data.items() if k != "_latency_ms"},
            latency_ms=round(latency_ms, 1),
        )
        return res

    def _append(self, messages: list[Message], message: Message) -> None:
        messages.append(message)
        self.tracer.emit("message", index=len(messages) - 1, message=message)

    @staticmethod
    def _account(result: AgentResult, resp: LLMResponse) -> None:
        result.llm_calls += 1
        result.usage = result.usage + resp.usage
        result.cost_usd += resp.cost_usd
        result.billed_usd += resp.billed_usd
