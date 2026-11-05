"""A trace sink that prints the agent's progress live in the terminal."""

import json

from rich.console import Console

from askoract.tracing.tracer import Event

MAX_ARG_CHARS = 160
MAX_OUTPUT_LINES = 6


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ConsoleSink:
    def __init__(self, console: Console | None = None, *, show_outputs: bool = True) -> None:
        self.console = console or Console()
        self.show_outputs = show_outputs

    def write(self, event: Event) -> None:
        d = event.data
        if event.type == "llm_call":
            usage = d["usage"]
            tag = "[green]cached[/]" if d["cached"] else f"${d['cost_usd']:.4f}"
            self.console.print(
                f"[dim]· {d['role']} {d['model']}  {usage['input_tokens']:,} in / "
                f"{usage['output_tokens']:,} out  {d['latency_ms'] / 1000:.1f}s  [/]{tag}"
            )
            if d.get("text"):
                self.console.print(f"  [italic]{_short(d['text'], 300)}[/]", highlight=False)
        elif event.type == "tool_call":
            args = _short(json.dumps(d["arguments"], ensure_ascii=False), MAX_ARG_CHARS)
            colour = "red" if d["is_error"] else "cyan"
            self.console.print(f"[{colour}]→ {d['tool']}[/] {args}", highlight=False)
            if self.show_outputs and d["tool"] != "final_answer":
                lines = d["output"].splitlines()
                shown = lines[:MAX_OUTPUT_LINES]
                more = len(lines) - len(shown)
                for line in shown:
                    self.console.print(f"    [dim]{_short(line, 140)}[/]", highlight=False)
                if more > 0:
                    self.console.print(f"    [dim]… {more} more lines[/]")

    def close(self) -> None:
        pass
