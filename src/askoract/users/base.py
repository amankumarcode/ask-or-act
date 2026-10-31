"""Whoever answers the agent's clarifying questions.

The agent only sees `UserChannel.reply`. Behind it can be a human at the terminal,
an LLM playing a user with a hidden intent, canned test replies, or later a
benchmark's own user simulator or a web UI.
"""

from typing import Protocol, runtime_checkable

from askoract.registry import Registry


@runtime_checkable
class UserChannel(Protocol):
    def reply(self, question: str) -> str: ...


class NoUser:
    """For runs where nobody can answer, e.g. a profile without ask_user."""

    def reply(self, question: str) -> str:
        return "No user is available to answer. Proceed with your best judgment and state it."


USERS: Registry[type] = Registry("user channel")
USERS.add("none", NoUser)
