"""Generic SQLite-file dataset builder, using a toy dataset and toy traps (no network)."""

import random
import sqlite3
import stat

import pytest

from askoract.config import load_settings
from askoract.datasets.sqlite_file import (
    DatasetBuildError,
    DatasetSpec,
    SqliteFileDataset,
    sha256_file,
)
from askoract.datasets.traps import TRAPS, Trap, register_trap


@register_trap("toy")
class ToyFlag(Trap):
    id = "flag"
    summary = "adds Items.Flag"
    correct_behavior = "n/a"
    comments = {"Items": {"Flag": "random flag"}}

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute("ALTER TABLE Items ADD COLUMN Flag INTEGER")
        for (item_id,) in conn.execute("SELECT id FROM Items ORDER BY id").fetchall():
            conn.execute("UPDATE Items SET Flag = ? WHERE id = ?", (rng.randint(0, 1000), item_id))

    def verify(self, conn: sqlite3.Connection) -> None:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(Items)")]
        self.check("Flag" in cols, "Flag missing")


@register_trap("toy")
class ToyBroken(Trap):
    id = "broken"
    summary = "applies nothing, so verification fails"
    correct_behavior = "n/a"

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        pass

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check(False, "always fails")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src" / "toy.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO Items (name) VALUES (?)", [(f"item{i}",) for i in range(50)])
    conn.commit()
    conn.close()
    return path


def make_dataset(tmp_project, source, *, seed=1, traps=("flag",), sha=None):
    settings = load_settings(tmp_project, [f"seed={seed}"])
    spec = DatasetSpec(
        name="toy",
        source={"url": str(source), "sha256": sha or sha256_file(source), "filename": "toy.db"},
        variants={"pristine": {}, "traps": {"traps": list(traps)}},
    )
    spec_dir = tmp_project / "datasets" / "toy"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "column_comments.yaml").write_text("Items:\n  name: item name\n")
    return SqliteFileDataset(settings, spec, spec_dir)


def flags(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT Flag FROM Items ORDER BY id").fetchall()
    finally:
        conn.close()


def test_build_then_up_to_date(tmp_project, source):
    ds = make_dataset(tmp_project, source)
    assert [r.status for r in ds.build()] == ["built", "built"]
    assert [r.status for r in ds.build()] == ["up_to_date", "up_to_date"]
    assert [r.status for r in ds.build(force=True)] == ["built", "built"]


def test_raw_is_untouched_and_read_only(tmp_project, source):
    ds = make_dataset(tmp_project, source)
    ds.build()
    assert sha256_file(ds.raw_path) == sha256_file(source)
    assert not ds.raw_path.stat().st_mode & stat.S_IWUSR
    assert sha256_file(ds.location("pristine")) == sha256_file(source)


def test_rebuild_is_deterministic(tmp_project, source):
    ds = make_dataset(tmp_project, source)
    ds.build("traps")
    first = flags(ds.location("traps"))
    ds.build("traps", force=True)
    assert flags(ds.location("traps")) == first


def test_seed_change_triggers_different_rebuild(tmp_project, source):
    ds = make_dataset(tmp_project, source, seed=1)
    ds.build("traps")
    first = flags(ds.location("traps"))
    ds2 = make_dataset(tmp_project, source, seed=2)
    assert not ds2.is_up_to_date("traps")
    assert ds2.build("traps")[0].status == "built"
    assert flags(ds2.location("traps")) != first


def test_checksum_mismatch_rejected(tmp_project, source):
    ds = make_dataset(tmp_project, source, sha="0" * 64)
    with pytest.raises(DatasetBuildError, match="checksum mismatch"):
        ds.build()
    assert not ds.raw_path.exists()


def test_failed_verification_leaves_no_database(tmp_project, source):
    ds = make_dataset(tmp_project, source, traps=("broken",))
    with pytest.raises(AssertionError, match="always fails"):
        ds.build("traps")
    assert not ds.location("traps").exists()
    assert not list(ds.built_dir.glob(".*tmp*"))


def test_open_is_read_only_with_merged_comments(tmp_project, source):
    ds = make_dataset(tmp_project, source)
    ds.build()
    with ds.open("traps") as db:
        cols = {c.name: c.comment for c in db.describe("Items").columns}
    assert cols == {"id": None, "name": "item name", "Flag": "random flag"}


def test_open_before_build_explains(tmp_project, source):
    ds = make_dataset(tmp_project, source)
    with pytest.raises(DatasetBuildError, match="make setup"):
        ds.open("traps")


def test_toy_traps_registered_under_dataset_namespace():
    assert "toy/flag" in TRAPS
