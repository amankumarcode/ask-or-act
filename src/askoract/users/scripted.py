from askoract.users.base import USERS


class ScriptedUser:
    """Canned replies, in order, for tests. Records every question it was asked."""

    def __init__(self, replies: list[str] | str = "Use your best judgment.") -> None:
        self._replies = [replies] if isinstance(replies, str) else list(replies)
        self.questions: list[str] = []

    def reply(self, question: str) -> str:
        self.questions.append(question)
        if len(self._replies) > 1:
            return self._replies.pop(0)
        return self._replies[0]


USERS.add("scripted", ScriptedUser)
