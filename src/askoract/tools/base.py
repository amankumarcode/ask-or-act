"""Tool interface and the registry that turns a profile's tool list into a toolset.

A tool is a class with a pydantic input model and a `run` method. Its description
is not hard-coded: it is loaded from a versioned file (prompts/tools/vN.yaml),
so tool wording can be iterated and compared like prompts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from askoract.agent.types import FinalAnswer
from askoract.config import Limits
from askoract.db.base import Database
from askoract.llm.base import ToolSpec
from askoract.registry import Registry
from askoract.tracing.tracer import Tracer
from askoract.users.base import UserChannel


@dataclass
class ToolContext:
    """Everything a tool may touch during one question."""

    db: Database
    user: UserChannel
    limits: Limits
    tracer: Tracer = field(default_factory=Tracer)
    clarifications: list[tuple[str, str]] = field(default_factory=list)  # (question, reply)


@dataclass
class ToolResult:
    content: str  # what the model reads
    data: dict[str, Any] = field(default_factory=dict)  # structured copy for traces and scoring
    is_error: bool = False
    final: FinalAnswer | None = None  # set only by terminal tools

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=f"ERROR: {message}", is_error=True)


class Tool(ABC):
    name: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    terminal: ClassVar[bool] = False  # True if calling it ends the run

    def __init__(self, description: str) -> None:
        self.description = description

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    def __call__(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate arguments and run; invalid arguments become an error the model can read."""
        try:
            args = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return ToolResult.error(f"invalid arguments for {self.name}: {problems}")
        return self.run(args, ctx)

    @abstractmethod
    def run(self, args: Any, ctx: ToolContext) -> ToolResult: ...


# Tool name -> Tool class.
TOOLS: Registry[type[Tool]] = Registry("tool")


def register_tool[T: type[Tool]](cls: T) -> T:
    """Class decorator: register a tool under its own `name`."""
    TOOLS.add(cls.name, cls)
    return cls


class Toolset:
    """The instantiated tools one agent profile is allowed to use."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    @classmethod
    def from_names(cls, names: list[str], descriptions: dict[str, str]) -> "Toolset":
        missing = [n for n in names if n not in descriptions]
        if missing:
            raise KeyError(f"no description for tools: {', '.join(missing)}")
        return cls([TOOLS.get(n)(descriptions[n]) for n in names])

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already in the toolset")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
