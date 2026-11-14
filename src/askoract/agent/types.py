"""Data types shared by the agent loop, the tools, and the evaluator."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from askoract.llm.base import Usage


class FinalAnswer(BaseModel):
    """What the agent commits to. Its `sql` is re-executed by the scorer."""

    status: Literal["answered", "cannot_answer"] = Field(
        description="'answered' if the data answers the question, "
        "'cannot_answer' if the database does not contain what is needed."
    )
    answer: str = Field(description="The answer in plain business language.")
    sql: str | None = Field(
        default=None, description="The single SQL query that produces the answer."
    )
    assumption: str | None = Field(
        default=None, description="One line stating any assumption made, or null."
    )


class Outcome(StrEnum):
    ANSWERED = "answered"  # final_answer with status=answered
    CANNOT_ANSWER = "cannot_answer"  # final_answer with status=cannot_answer
    STEP_LIMIT = "step_limit"
    BUDGET_EXCEEDED = "budget_exceeded"
    NO_FINAL_ANSWER = "no_final_answer"  # model stopped calling tools without answering
    ERROR = "error"


@dataclass
class Step:
    """One tool call within a run, in order."""

    index: int
    tool: str
    arguments: dict[str, Any]
    output: str
    is_error: bool = False
    latency_ms: float = 0.0


@dataclass
class AgentResult:
    question: str
    outcome: Outcome
    final: FinalAnswer | None = None
    steps: list[Step] = field(default_factory=list)
    clarifications: list[tuple[str, str]] = field(default_factory=list)  # (question, reply)
    llm_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0  # nominal cost of every LLM call, cached or not
    billed_usd: float = 0.0  # what this run actually spent (cache hits are free)
    latency_ms: float = 0.0  # wall time of this run (near zero on a cache replay)
    model_latency_ms: float = 0.0  # original latency of every LLM call, cached or not
    error: str | None = None

    @property
    def nominal_latency_ms(self) -> float:
        """Latency as first measured: model time plus tool time. Stable across replays."""
        return self.model_latency_ms + sum(s.latency_ms for s in self.steps)

    @property
    def asked(self) -> bool:
        return bool(self.clarifications)
