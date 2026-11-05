"""SQLite-backed LLM response cache.

The key is a hash of the full request (model, messages, tools, parameters), so a
rerun of the same eval replays exactly the same responses for free. Change a prompt,
a tool description, or the model, and the key changes too.
"""

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def request_key(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            " key TEXT PRIMARY KEY, model TEXT, created_at TEXT, response TEXT)"
        )
        self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT response FROM responses WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, model: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)",
                (
                    key,
                    model,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    json.dumps(response, default=str),
                ),
            )
            self._conn.commit()

    def __len__(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

    def close(self) -> None:
        self._conn.close()
