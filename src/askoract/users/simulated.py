"""An LLM playing the person who asked the question, with a hidden intent.

It answers the agent's clarifying questions according to that intent, in business
language. It runs on its own model (configured separately from the agent) and goes
through the same cache and tracer as every other LLM call.
"""

import re

from askoract.llm.base import LLMClient, Message
from askoract.prompts import render
from askoract.tracing.tracer import Tracer
from askoract.users.base import USERS

# Signs that a reply leaked implementation detail the user should not know.
_LEAK_PATTERNS = [
    re.compile(r"\b(SELECT|JOIN|WHERE|GROUP BY|FROM \w+\s+WHERE)\b"),  # SQL, uppercase only
    re.compile(r"`[^`]+`"),  # code-formatted identifiers
    re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b"),  # CamelCase names like ShippedDate
]


def leak_signals(reply: str) -> list[str]:
    """Substrings of `reply` that look like schema or SQL rather than business language."""
    return [m.group(0) for p in _LEAK_PATTERNS for m in p.finditer(reply)]


class SimulatedUser:
    def __init__(
        self,
        llm: LLMClient,
        *,
        original_question: str,
        intent: str,
        prompt_template: str,
        tracer: Tracer | None = None,
    ) -> None:
        self.llm = llm
        self.tracer = tracer or Tracer()
        self.system_prompt = render(
            prompt_template, original_question=original_question, intent=intent
        ).strip()
        self.history: list[Message] = []  # earlier clarifications, so replies stay consistent

    def reply(self, question: str) -> str:
        """Answer one clarifying question. Raises LLMError if the model call fails."""
        self.history.append({"role": "user", "content": question})
        messages = [{"role": "system", "content": self.system_prompt}, *self.history]
        resp = self.llm.complete(messages)
        answer = (resp.text or "").strip() or "I have no preference; use the usual definition."
        self.history.append({"role": "assistant", "content": answer})
        self.tracer.emit(
            "user_reply",
            kind="simulated",
            question=question,
            reply=answer,
            leak_signals=leak_signals(answer),
        )
        return answer


USERS.add("simulated", SimulatedUser)
