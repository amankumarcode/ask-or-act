"""Read-only SQLite engine, on a tiny synthetic database (no network)."""

import hashlib

import pytest

from askoract.db import QueryError, QueryTimeout, ReadOnlyViolation


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_select_and_row_limit(db):
    res = db.execute("SELECT CustomerID FROM Customers ORDER BY 1", max_rows=2, timeout_s=5)
    assert res.columns == ["CustomerID"]
    assert res.rows == [("A",), ("B",)]
    assert res.truncated is True
    full = db.execute("SELECT COUNT(*) FROM Customers", max_rows=10, timeout_s=5)
    assert full.rows == [(4,)] and full.truncated is False


def test_table_name_with_space(db):
    res = db.execute('SELECT SUM(Qty) FROM "Order Details"', max_rows=10, timeout_s=5)
    assert res.rows == [(12,)]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO Customers VALUES ('Z', 'Spain')",
        "UPDATE Customers SET Country = 'X'",
        "DELETE FROM Customers",
        "DROP TABLE Customers",
        "CREATE TABLE t (x INTEGER)",
        "CREATE TEMP TABLE t AS SELECT 1",
        "ALTER TABLE Customers ADD COLUMN y TEXT",
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA query_only = OFF",
        "PRAGMA writable_schema = ON",
        "BEGIN",
    ],
)
def test_writes_are_rejected_and_file_is_unchanged(db, db_path, sql):
    before = _sha(db_path)
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        db.execute(sql, max_rows=10, timeout_s=5)
    assert _sha(db_path) == before
    # the connection still works afterwards
    assert db.execute("SELECT 1", max_rows=1, timeout_s=5).rows == [(1,)]


def test_multiple_statements_rejected(db):
    with pytest.raises(QueryError, match="one SQL statement"):
        db.execute("SELECT 1; SELECT 2", max_rows=1, timeout_s=5)


def test_syntax_error_is_readable(db):
    with pytest.raises(QueryError, match="SQL error: no such column: Nope"):
        db.execute("SELECT Nope FROM Customers", max_rows=1, timeout_s=5)


def test_timeout(db):
    runaway = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT MAX(x) FROM n"
    with pytest.raises(QueryTimeout, match="timeout"):
        db.execute(runaway, max_rows=1, timeout_s=0.2)
    assert db.execute("SELECT 1", max_rows=1, timeout_s=5).rows == [(1,)]


def test_list_tables(db):
    tables = {t.name: t for t in db.list_tables()}
    assert tables["Customers"].row_count == 4
    assert tables["Order Details"].row_count == 2
    assert tables["German"].kind == "view" and tables["German"].row_count is None


def test_describe_with_keys_and_comments(db):
    schema = db.describe("customers")  # case-insensitive
    assert schema.name == "Customers" and schema.comment == "People"
    cols = {c.name: c for c in schema.columns}
    assert cols["CustomerID"].primary_key
    assert cols["Country"].comment == "ISO"
    fk = db.describe("[Order Details]").foreign_keys
    assert fk[0].ref_table == "Customers" and fk[0].column == "CustomerID"


def test_unknown_table_suggests_close_match(db):
    with pytest.raises(QueryError, match="Did you mean: Customers"):
        db.describe("Customer")
