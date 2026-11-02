"""Engine-agnostic database interface.

Tools and the evaluator talk only to `Database`. Each engine (SQLite now,
Postgres) implements it and enforces read-only access
at the connection level, not just in the prompt.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from askoract.registry import Registry


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    default: str | None = None
    comment: str | None = None


@dataclass(frozen=True)
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    row_count: int | None  # None for views (counting would run the view's query)
    kind: str = "table"  # "table" | "view"


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    comment: str | None = None


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool = False  # True when more than max_rows rows existed
    elapsed_ms: float = 0.0


class DatabaseError(Exception):
    """Base error. Messages are written for the agent to read and recover from."""


class QueryError(DatabaseError):
    """The SQL failed: syntax error, unknown table or column, and so on."""


class ReadOnlyViolation(QueryError):
    """The SQL tried to write or change the schema."""


class QueryTimeout(QueryError):
    """The SQL ran longer than the timeout."""


@runtime_checkable
class Database(Protocol):
    name: str
    dialect: str  # "sqlite" | "postgresql" | ...; injected into prompts

    def list_tables(self) -> list[TableInfo]: ...

    def describe(self, table: str) -> TableSchema: ...

    def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> QueryResult: ...

    def close(self) -> None: ...


# Engine name -> factory. A factory takes a location (file path or DSN) plus keyword options.
ENGINES: Registry[type] = Registry("database engine")
