"""Read-only SQLite engine.

Read-only access is enforced in three independent layers, none of which rely on the prompt:
  1. the file is opened with `mode=ro`, so SQLite refuses any write to it;
  2. `PRAGMA query_only = ON`;
  3. an authorizer callback that allows only reads and a whitelist of schema pragmas.
Timeouts use a progress handler; row limits use fetchmany(max_rows + 1).
"""

import difflib
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from askoract.db.base import (
    ENGINES,
    ColumnInfo,
    DatabaseError,
    ForeignKey,
    QueryError,
    QueryResult,
    QueryTimeout,
    ReadOnlyViolation,
    TableInfo,
    TableSchema,
)

# table -> {"_table": table comment, "<column>": column comment}
Comments = dict[str, dict[str, str]]

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info"}
_PROGRESS_EVERY_N_OPS = 10_000


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SqliteDatabase:
    dialect = "sqlite"

    def __init__(
        self, path: Path | str, *, name: str | None = None, comments: Comments | None = None
    ) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise DatabaseError(f"database file not found: {self.path}")
        self.name = name or self.path.stem
        self.comments: Comments = comments or {}
        self._denied: str | None = None
        self._deadline: float | None = None

        uri = f"file:{quote(str(self.path.resolve()))}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.execute("PRAGMA query_only = ON")
        self._conn.set_authorizer(self._authorize)
        self._conn.set_progress_handler(self._check_deadline, _PROGRESS_EVERY_N_OPS)

    # -- guards -------------------------------------------------------------

    def _authorize(self, action: int, arg1: Any, arg2: Any, _db: Any, _trigger: Any) -> int:
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1 in _ALLOWED_PRAGMAS:
            return sqlite3.SQLITE_OK
        self._denied = f"{_ACTION_NAMES.get(action, action)} {arg1 or ''}".strip()
        return sqlite3.SQLITE_DENY

    def _check_deadline(self) -> int:
        return int(self._deadline is not None and time.monotonic() > self._deadline)

    # -- Database protocol ----------------------------------------------------

    def execute(self, sql: str, *, max_rows: int, timeout_s: float) -> QueryResult:
        self._denied = None
        self._deadline = time.monotonic() + timeout_s
        start = time.perf_counter()
        try:
            cur = self._conn.execute(sql)
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
            columns = [d[0] for d in cur.description or []]
        except sqlite3.Error as exc:
            raise self._translate(exc, timeout_s) from None
        finally:
            self._deadline = None
        truncated = len(rows) > max_rows
        return QueryResult(
            columns=columns,
            rows=[tuple(r) for r in rows[:max_rows]],
            truncated=truncated,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    def list_tables(self) -> list[TableInfo]:
        objects = self._internal(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
        out = []
        for name, kind in objects:
            # Counting a view runs its whole query, which can be slow; skip it.
            count = (
                self._internal(f"SELECT COUNT(*) FROM {_quote_ident(name)}")[0][0]
                if kind == "table"
                else None
            )
            out.append(TableInfo(name=name, row_count=count, kind=kind))
        return out

    def describe(self, table: str) -> TableSchema:
        name = self.resolve_table(table)
        q = _quote_ident(name)
        comments = self.comments.get(name, {})
        columns = [
            ColumnInfo(
                name=col,
                type=ctype or "",
                nullable=not notnull,
                primary_key=bool(pk),
                default=default,
                comment=comments.get(col),
            )
            for _cid, col, ctype, notnull, default, pk, _hidden in self._internal(
                f"PRAGMA table_xinfo({q})"
            )
        ]
        fks = [
            ForeignKey(column=row[3], ref_table=row[2], ref_column=row[4])
            for row in self._internal(f"PRAGMA foreign_key_list({q})")
        ]
        return TableSchema(
            name=name, columns=columns, foreign_keys=fks, comment=comments.get("_table")
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteDatabase":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- helpers ----------------------------------------------------------------

    def table_names(self) -> list[str]:
        return [t.name for t in self.list_tables()]

    def resolve_table(self, table: str) -> str:
        """Exact name for `table` (case-insensitive, quotes stripped), or a helpful error."""
        wanted = table.strip().strip('[]"`')
        names = [
            r[0]
            for r in self._internal(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in names:
            if name.lower() == wanted.lower():
                return name
        close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise QueryError(f"no such table: {table!r}.{hint}")

    def _internal(self, sql: str) -> list[tuple[Any, ...]]:
        try:
            return self._conn.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise self._translate(exc, None) from None

    def _translate(self, exc: sqlite3.Error, timeout_s: float | None) -> QueryError:
        msg = str(exc)
        if self._denied or "not authorized" in msg or "readonly" in msg.lower():
            what = f" (blocked: {self._denied})" if self._denied else ""
            return ReadOnlyViolation(
                f"This database connection is read-only; only SELECT queries are allowed{what}."
            )
        if "interrupted" in msg:
            return QueryTimeout(
                f"Query exceeded the {timeout_s}s timeout. Simplify it, filter earlier, "
                "or aggregate instead of returning many rows."
            )
        if "one statement at a time" in msg:
            return QueryError("Only one SQL statement can be run per call.")
        return QueryError(f"SQL error: {msg}")


# Authorizer action codes -> names, for error messages. Listed explicitly because
# sqlite3 also exposes result codes under SQLITE_* with overlapping integer values.
_ACTION_NAMES = {
    getattr(sqlite3, f"SQLITE_{n}"): n
    for n in [
        *("INSERT", "UPDATE", "DELETE", "ALTER_TABLE", "ATTACH", "DETACH", "PRAGMA"),
        *("TRANSACTION", "SAVEPOINT", "REINDEX", "ANALYZE", "CREATE_VTABLE", "DROP_VTABLE"),
        *("CREATE_TABLE", "CREATE_INDEX", "CREATE_VIEW", "CREATE_TRIGGER"),
        *("CREATE_TEMP_TABLE", "CREATE_TEMP_INDEX", "CREATE_TEMP_VIEW", "CREATE_TEMP_TRIGGER"),
        *("DROP_TABLE", "DROP_INDEX", "DROP_VIEW", "DROP_TRIGGER"),
        *("DROP_TEMP_TABLE", "DROP_TEMP_INDEX", "DROP_TEMP_VIEW", "DROP_TEMP_TRIGGER"),
    ]
}

ENGINES.add("sqlite", SqliteDatabase)
