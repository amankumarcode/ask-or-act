"""Trap interface: a deliberate, measurable pitfall injected into a copy of a dataset.

Each trap is one class in its own module. `apply` mutates a writable connection
deterministically from a seeded RNG, and `verify` asserts the trap is present and actually
bites. The module's source is hashed into the build manifest, so editing a trap triggers
a rebuild.
"""

import hashlib
import inspect
import random
import sqlite3
from abc import ABC, abstractmethod
from typing import ClassVar

from askoract.registry import Registry


class TrapVerificationError(AssertionError):
    pass


class Trap(ABC):
    id: ClassVar[str]
    summary: ClassVar[str]  # what gets injected
    correct_behavior: ClassVar[str]  # what a careful analyst does about it
    # Comments this trap adds to describe_table: table -> {"_table" | column: text}
    comments: ClassVar[dict[str, dict[str, str]]] = {}

    @abstractmethod
    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None: ...

    @abstractmethod
    def verify(self, conn: sqlite3.Connection) -> None:
        """Raise TrapVerificationError if the trap is missing or would not change any answer."""

    @classmethod
    def fingerprint(cls) -> str:
        """Hash of the trap's whole module, so module-level data changes count too."""
        source = inspect.getsource(inspect.getmodule(cls) or cls)
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    @staticmethod
    def check(condition: bool, message: str) -> None:
        if not condition:
            raise TrapVerificationError(message)


# Keyed "<dataset>/<trap id>", e.g. "northwind/cancelled_status".
TRAPS: Registry[type[Trap]] = Registry("trap")


def register_trap(dataset: str):
    def deco(cls: type[Trap]) -> type[Trap]:
        TRAPS.add(f"{dataset}/{cls.id}", cls)
        return cls

    return deco


def trap_rng(seed: int, trap_id: str) -> random.Random:
    """An independent, deterministic RNG per trap. Adding a trap never reshuffles the others."""
    return random.Random(f"{seed}:{trap_id}")
