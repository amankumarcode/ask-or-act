"""A Benchmark supplies test cases and the environment each case runs in.

Our YAML suites are one implementation. An external benchmark such as
BIRD-Interact becomes another: its tasks map to TestCase, its user simulator is
a UserChannel, its knowledge base becomes an extra tool, and it may override
scoring. The agent loop doesn't change.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from askoract.db.base import Database
from askoract.eval.cases import TestCase
from askoract.registry import Registry
from askoract.tools.base import Tool
from askoract.tracing.tracer import Tracer
from askoract.users.base import UserChannel


@dataclass
class CaseEnv:
    db: Database
    user: UserChannel
    extra_tools: list[Tool] = field(default_factory=list)
    scorer: Callable[..., Any] | None = None  # None means the default result-set scorer


@runtime_checkable
class Benchmark(Protocol):
    name: str

    def cases(self) -> Iterable[TestCase]: ...

    def environment(self, case: TestCase, tracer: Tracer) -> CaseEnv:
        """Everything one case runs against. The caller closes `env.db` afterwards.

        `tracer` is the case's tracer, so anything the environment calls (e.g. a simulated
        user's model) lands in the same trace as the agent.
        """
        ...


# Benchmark name -> factory taking (settings, **options) and returning a Benchmark.
BENCHMARKS: Registry[Any] = Registry("benchmark")
