from rich.console import Console
from rich.prompt import Prompt

from askoract.users.base import USERS


class TerminalUser:
    """A real person answering the agent's clarifying questions at the keyboard."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def reply(self, question: str) -> str:
        self.console.print(f"\n[bold yellow]Agent asks:[/] {question}")
        return Prompt.ask("[bold]Your answer[/]", console=self.console)


USERS.add("terminal", TerminalUser)
