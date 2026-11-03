"""Render query results as compact text for the model.

Keeps tool output readable and bounded: blobs become placeholders, long text is cut,
floats are rounded, and the whole result is capped at `max_chars`.
"""

from typing import Any

MAX_CELL_CHARS = 80


def format_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    if isinstance(value, float):
        # Plain notation, never scientific: magnitude is a signal (e.g. cents vs dollars).
        if value != 0 and abs(value) < 1e-4:
            return f"{value:g}"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    text = str(value).replace("\n", " ")
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def json_safe(value: Any) -> Any:
    """Cell value for traces/JSON: blobs are summarised, everything else kept as-is."""
    return f"<blob {len(value)} bytes>" if isinstance(value, bytes) else value


def format_table(columns: list[str], rows: list[tuple[Any, ...]], max_chars: int) -> str:
    """Pipe-separated table. Stops adding rows once `max_chars` would be exceeded."""
    lines = [" | ".join(columns)]
    used = len(lines[0])
    for i, row in enumerate(rows):
        line = " | ".join(format_value(v) for v in row)
        if used + len(line) + 1 > max_chars:
            lines.append(f"... output cut at {i} of {len(rows)} rows to save space")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [{len(text) - max_chars} more characters cut]"
