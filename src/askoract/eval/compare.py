"""Compare two query results as data, not as SQL text.

Rules, chosen so that every correct query passes and wrong ones don't:
  - Column names and column order are ignored.
  - Extra predicted columns are allowed: gold columns only need to appear somewhere in the
    prediction (e.g. the agent adds a name next to an id).
  - Rows are a multiset (order ignored) unless the case says order matters.
  - Integers compare exactly. A float compares within max(abs_tol, rel_tol * magnitude),
    so rounding to cents passes but a missed filter does not.
  - Strings are compared after stripping whitespace; numeric strings count as numbers.
"""

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
MAX_MAPPINGS = 20_000


@dataclass(frozen=True)
class Tolerance:
    rel: float = 1e-4
    abs: float = 0.01


@dataclass(frozen=True)
class Comparison:
    match: bool
    reason: str = ""
    column_map: list[int] = field(default_factory=list)  # gold column i -> predicted column


def normalize(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _NUMBER.match(text):
            number = float(text)
            return int(number) if number.is_integer() and "." not in text else number
        return text
    return value


def cells_equal(a: Any, b: Any, tol: Tolerance) -> bool:
    if a is None or b is None:
        return a is None and b is None
    a_num = isinstance(a, int | float)
    b_num = isinstance(b, int | float)
    if a_num and b_num:
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        if math.isnan(a) or math.isnan(b):
            return False
        return abs(a - b) <= max(tol.abs, tol.rel * max(abs(a), abs(b)))
    if a_num != b_num:
        return False
    return a == b


def _rows_equal(r1: tuple, r2: tuple, tol: Tolerance) -> bool:
    return all(cells_equal(a, b, tol) for a, b in zip(r1, r2, strict=True))


def _sort_key(value: Any) -> tuple:
    if value is None:
        return (0, 0)
    if isinstance(value, int | float):
        return (1, value)
    return (2, str(value))


def _multiset_equal(gold: list[tuple], pred: list[tuple], tol: Tolerance) -> bool:
    if len(gold) != len(pred):
        return False
    key = lambda row: tuple(_sort_key(v) for v in row)  # noqa: E731
    g_sorted, p_sorted = sorted(gold, key=key), sorted(pred, key=key)
    if all(_rows_equal(a, b, tol) for a, b in zip(g_sorted, p_sorted, strict=True)):
        return True
    # Sorting can misalign rows whose floats differ within tolerance; fall back to matching.
    unused = list(p_sorted)
    for row in g_sorted:
        for i, candidate in enumerate(unused):
            if _rows_equal(row, candidate, tol):
                del unused[i]
                break
        else:
            return False
    return True


def _sequence_equal(gold: list[tuple], pred: list[tuple], tol: Tolerance) -> bool:
    return len(gold) == len(pred) and all(
        _rows_equal(a, b, tol) for a, b in zip(gold, pred, strict=True)
    )


def compare_results(
    gold_rows: list[tuple],
    pred_rows: list[tuple],
    *,
    ordered: bool = False,
    tol: Tolerance | None = None,
) -> Comparison:
    tol = tol or Tolerance()
    gold = [tuple(normalize(v) for v in r) for r in gold_rows]
    pred = [tuple(normalize(v) for v in r) for r in pred_rows]
    if len(gold) != len(pred):
        return Comparison(False, f"row count differs: expected {len(gold)}, got {len(pred)}")
    if not gold:
        return Comparison(True, "both empty")
    k, m = len(gold[0]), len(pred[0])
    if m < k:
        return Comparison(False, f"expected at least {k} columns, got {m}")

    same = _sequence_equal if ordered else _multiset_equal
    # Candidate predicted columns for each gold column, by comparing single columns first.
    candidates: list[list[int]] = []
    for i in range(k):
        g_col = [(r[i],) for r in gold]
        cands = [j for j in range(m) if same(g_col, [(r[j],) for r in pred], tol)]
        if not cands:
            return Comparison(False, f"no predicted column matches expected column {i + 1}")
        candidates.append(cands)

    for n, mapping in enumerate(itertools.product(*candidates)):
        if n >= MAX_MAPPINGS:
            break
        if len(set(mapping)) < k:
            continue
        projected = [tuple(r[j] for j in mapping) for r in pred]
        if same(gold, projected, tol):
            return Comparison(True, "match", list(mapping))
    reason = "rows differ in order" if ordered else "columns match individually but rows differ"
    return Comparison(False, reason)
