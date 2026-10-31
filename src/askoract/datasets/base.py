"""A Dataset knows how to fetch its source data and build one or more database variants.

Northwind has two variants: "pristine" (the untouched source) and "traps" (deliberate
pitfalls injected). Another dataset registers a builder under its own name.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from askoract.db.base import Database
from askoract.registry import Registry


@dataclass(frozen=True)
class BuildReport:
    dataset: str
    variant: str
    location: str  # file path or DSN
    status: Literal["built", "up_to_date"]
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Dataset(Protocol):
    name: str
    variants: list[str]

    def build(self, variant: str | None = None, *, force: bool = False) -> list[BuildReport]:
        """Build one variant, or all variants when `variant` is None. Must be idempotent."""
        ...

    def location(self, variant: str) -> Path | str: ...

    def open(self, variant: str) -> Database:
        """A read-only connection to a built variant."""
        ...


# Dataset name -> factory taking (settings) and returning a Dataset.
DATASETS: Registry[Any] = Registry("dataset")
