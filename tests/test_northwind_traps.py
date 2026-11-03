"""Northwind traps on the real data.

Uses the cached download in data/raw/ (from `make setup`) and builds into a temp dir,
so the project's own built databases are never touched. Skipped if setup hasn't run.
"""

import hashlib
import shutil
import sqlite3

import pytest

from askoract.config import load_settings
from askoract.datasets import get_dataset
from askoract.datasets.traps import TrapVerificationError

TRAP_TABLES = ["Orders", "Customers", "CustomerMaster", "Payments", "OrderShipments"]


def _cached_raw(repo_root):
    path = repo_root / "data" / "raw" / "northwind" / "northwind.db"
    if not path.is_file():
        pytest.skip("Northwind not downloaded yet; run `make setup` first")
    return path


def _dataset(repo_root, data_dir, seed=42):
    raw = _cached_raw(repo_root)
    settings = load_settings(repo_root, [f"paths.data_dir={data_dir}", f"seed={seed}"])
    ds = get_dataset(settings, "northwind")
    ds.raw_dir.mkdir(parents=True, exist_ok=True)
    if not ds.raw_path.exists():
        shutil.copyfile(raw, ds.raw_path)
    return ds


def _content_hash(path):
    h = hashlib.sha256()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table in TRAP_TABLES:
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                h.update(repr(row).encode())
    finally:
        conn.close()
    return h.hexdigest()


@pytest.fixture(scope="module")
def built(repo_root, tmp_path_factory):
    ds = _dataset(repo_root, tmp_path_factory.mktemp("nw"))
    ds.build()
    return ds


def test_all_traps_verify_on_trap_variant(built):
    conn = sqlite3.connect(f"file:{built.location('traps')}?mode=ro", uri=True)
    try:
        for trap in built.traps("traps"):
            trap.verify(conn)
    finally:
        conn.close()


def test_every_trap_fails_verification_on_pristine(built):
    """Proves each verify() really detects its trap, rather than passing vacuously."""
    conn = sqlite3.connect(f"file:{built.location('pristine')}?mode=ro", uri=True)
    try:
        for trap in built.traps("traps"):
            with pytest.raises((TrapVerificationError, sqlite3.OperationalError)):
                trap.verify(conn)
    finally:
        conn.close()


def test_pristine_equals_source(built):
    assert hashlib.sha256(built.location("pristine").read_bytes()).hexdigest() == (
        built.spec.source.sha256
    )


def test_same_seed_same_data_different_seed_different_data(built, repo_root, tmp_path):
    again = _dataset(repo_root, tmp_path / "same", seed=42)
    again.build("traps")
    assert _content_hash(again.location("traps")) == _content_hash(built.location("traps"))

    other = _dataset(repo_root, tmp_path / "other", seed=7)
    other.build("traps")
    assert _content_hash(other.location("traps")) != _content_hash(built.location("traps"))


def test_untouched_tables_identical_to_pristine(built):
    """Traps must only change what they document. Order lines and products are untouched."""
    for table in ['"Order Details"', "Products", "Employees"]:
        sql = f"SELECT * FROM {table} ORDER BY rowid"
        rows = []
        for variant in ("pristine", "traps"):
            conn = sqlite3.connect(f"file:{built.location(variant)}?mode=ro", uri=True)
            rows.append(conn.execute(sql).fetchall())
            conn.close()
        assert rows[0] == rows[1], table


def test_tools_expose_traps_on_real_data(built):
    """The exploration tools surface each trap's evidence to an agent that looks."""
    from askoract.config import Limits
    from askoract.tools import TOOLS, ToolContext
    from askoract.users import NoUser

    with built.open("traps") as db:
        ctx = ToolContext(db=db, user=NoUser(), limits=Limits())

        def call(name, args):
            return TOOLS.get(name)("d")(args, ctx)

        assert (
            "Cancelled" in call("get_column_stats", {"table": "Orders", "column": "Status"}).content
        )
        amount = call("get_column_stats", {"table": "Payments", "column": "Amount"}).data
        assert amount["min"] > 1000  # cents: no order is under $10
        assert "2019 migration" in call("describe_table", {"table": "CustomerMaster"}).content
        assert "IsActive" in call("describe_table", {"table": "Customers"}).content
