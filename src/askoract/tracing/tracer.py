"""Event tracing with pluggable sinks.

The agent, tools, and users emit events (llm_call, tool_call, user_reply, ...).
Sinks decide where events go: a JSONL file for the trace viewer, memory for tests,
and later a websocket for a UI. Emitting code never knows which sinks are attached.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)  # run_id, case_id, profile...
    seq: int = 0
    ts: float = field(default_factory=time.time)


class Sink(Protocol):
    def write(self, event: Event) -> None: ...

    def close(self) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def write(self, event: Event) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass

    def of_type(self, type_: str) -> list[Event]:
        return [e for e in self.events if e.type == type_]


class JsonlSink:
    """Appends one JSON object per event. Values that aren't JSON-native become strings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, event: Event) -> None:
        self._fh.write(json.dumps(asdict(event), default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class Tracer:
    def __init__(self, sinks: list[Sink] | None = None, **context: Any) -> None:
        self.sinks: list[Sink] = list(sinks or [])
        self.context = context
        self._seq = 0

    def emit(self, type_: str, **data: Any) -> Event:
        self._seq += 1
        event = Event(type=type_, data=data, context=self.context, seq=self._seq)
        for sink in self.sinks:
            sink.write(event)
        return event

    def close(self) -> None:
        for sink in self.sinks:
            sink.close()


def read_trace(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
