import sqlite3
from pathlib import Path

import pytest

from askoract.db import SqliteDatabase

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A minimal throwaway project root with its own config files."""
    (tmp_path / "configs" / "agents").mkdir(parents=True)
    (tmp_path / "configs" / "default.yaml").write_text(
        """
seed: 1
paths: {data_dir: data, runs_dir: runs, datasets_dir: datasets,
        prompts_dir: prompts, profiles_dir: configs/agents}
cache: {path: runs/cache/llm.sqlite}
agent_defaults:
  llm: {model: anthropic/claude-sonnet-5-5}
  limits: {max_steps: 15, sql_max_rows: 200}
simulated_user:
  llm: {model: anthropic/claude-haiku-5-5}
  prompt: sim_user/v1
dataset: northwind
default_profile: mini
"""
    )
    (tmp_path / "configs" / "agents" / "mini.yaml").write_text(
        """
prompt: agent/minimal_v1
tool_descriptions: tools/v1
tools: [run_sql, final_answer]
limits: {max_steps: 5}
"""
    )
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A tiny synthetic database: a table name with a space, a view, a blob, NULLs."""
    path = tmp_path / "tiny.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE Customers (CustomerID TEXT PRIMARY KEY, Country TEXT);
        CREATE TABLE "Order Details" (
            OrderID INTEGER, CustomerID TEXT REFERENCES Customers (CustomerID), Qty INTEGER
        );
        CREATE TABLE Files (id INTEGER PRIMARY KEY, data BLOB, price REAL);
        CREATE VIEW German AS SELECT * FROM Customers WHERE Country = 'Germany';
        INSERT INTO Customers VALUES
            ('A', 'Germany'), ('B', 'France'), ('C', 'Germany'), ('D', NULL);
        INSERT INTO "Order Details" VALUES (1, 'A', 5), (2, 'B', 7);
        INSERT INTO Files VALUES (1, x'00FF00FF', 2407537.5492);
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(db_path: Path):
    comments = {"Customers": {"_table": "People", "Country": "ISO"}}
    with SqliteDatabase(db_path, comments=comments) as d:
        yield d
