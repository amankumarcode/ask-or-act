import sqlite3
from datetime import datetime

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def parse_ts(value: str) -> datetime:
    """Northwind mixes 'YYYY-MM-DD' and 'YYYY-MM-DD HH:MM:SS'; fromisoformat reads both."""
    return datetime.fromisoformat(value)
