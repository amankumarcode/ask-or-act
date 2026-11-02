"""Generic builder for datasets distributed as a single SQLite file.

Driven entirely by datasets/<name>/dataset.yaml:
  - download the source once into data/raw/<name>/, verify its sha256, make it read-only;
  - for each variant, copy the raw file and apply the variant's traps in the listed order;
  - build into a temp file and atomically rename it, then write a manifest;
  - skip the rebuild when the manifest key (source sha, traps + their code, seed) is unchanged.

The raw download is never opened for writing, so the original data is always recoverable.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from askoract.config import Settings
from askoract.datasets.base import BuildReport
from askoract.datasets.traps import TRAPS, Trap, trap_rng
from askoract.db.sqlite import Comments, SqliteDatabase

MANIFEST_VERSION = 1


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # http(s) URL, file:// URL, or a local path (tests)
    sha256: str
    filename: str


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    traps: list[str] = Field(default_factory=list)


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    builder: str = "sqlite_file"
    source: SourceSpec
    variants: dict[str, VariantSpec]


class DatasetBuildError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SqliteFileDataset:
    def __init__(self, settings: Settings, spec: DatasetSpec, spec_dir: Path) -> None:
        self.settings = settings
        self.spec = spec
        self.spec_dir = spec_dir
        self.name = spec.name
        self.variants = list(spec.variants)
        self.raw_dir = settings.paths.data_dir / "raw" / spec.name
        self.built_dir = settings.paths.data_dir / "built" / spec.name

    # -- paths ------------------------------------------------------------------

    @property
    def raw_path(self) -> Path:
        return self.raw_dir / self.spec.source.filename

    def location(self, variant: str) -> Path:
        self._variant(variant)
        return self.built_dir / f"{variant}.db"

    def _manifest_path(self, variant: str) -> Path:
        return self.built_dir / f"{variant}.manifest.json"

    def _comments_path(self, variant: str) -> Path:
        return self.built_dir / f"{variant}.comments.json"

    def _variant(self, variant: str) -> VariantSpec:
        if variant not in self.spec.variants:
            raise KeyError(f"{self.name} has no variant {variant!r}; available: {self.variants}")
        return self.spec.variants[variant]

    def traps(self, variant: str) -> list[Trap]:
        return [TRAPS.get(f"{self.name}/{tid}")() for tid in self._variant(variant).traps]

    # -- fetch ------------------------------------------------------------------

    def fetch(self) -> Path:
        """Download and verify the source once. Returns the raw file path."""
        src = self.spec.source
        if self.raw_path.is_file():
            if sha256_file(self.raw_path) != src.sha256:
                raise DatasetBuildError(
                    f"{self.raw_path} does not match the pinned sha256. Delete it and rerun setup."
                )
            return self.raw_path
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.raw_path.with_suffix(".download")
        try:
            self._download(src.url, tmp)
            actual = sha256_file(tmp)
            if actual != src.sha256:
                raise DatasetBuildError(
                    f"checksum mismatch for {src.url}: expected {src.sha256}, got {actual}"
                )
            os.replace(tmp, self.raw_path)
        finally:
            tmp.unlink(missing_ok=True)
        self.raw_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # 0444: never modified
        return self.raw_path

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        if url.startswith(("http://", "https://")):
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
        else:
            shutil.copyfile(Path(url.removeprefix("file://")), dest)

    # -- build ------------------------------------------------------------------

    def manifest_key(self, variant: str) -> dict[str, Any]:
        return {
            "manifest_version": MANIFEST_VERSION,
            "source_sha256": self.spec.source.sha256,
            "seed": self.settings.seed,
            "traps": {t.id: t.fingerprint() for t in self.traps(variant)},
            "comments_sha256": hashlib.sha256(
                json.dumps(self._comments(variant), sort_keys=True).encode()
            ).hexdigest(),
        }

    def is_up_to_date(self, variant: str) -> bool:
        mpath = self._manifest_path(variant)
        if not (mpath.is_file() and self.location(variant).is_file()):
            return False
        manifest = json.loads(mpath.read_text())
        return manifest.get("key") == self.manifest_key(variant)

    def build(self, variant: str | None = None, *, force: bool = False) -> list[BuildReport]:
        targets = [variant] if variant else self.variants
        self.fetch()
        return [self._build_one(v, force=force) for v in targets]

    def _build_one(self, variant: str, *, force: bool) -> BuildReport:
        dest = self.location(variant)
        if not force and self.is_up_to_date(variant):
            self._verify(dest, variant)  # cheap, and catches a tampered file
            return BuildReport(self.name, variant, str(dest), "up_to_date", self._summary(variant))

        self.built_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
        try:
            shutil.copyfile(self.raw_path, tmp)
            tmp.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            traps = self.traps(variant)
            if traps:
                conn = sqlite3.connect(tmp)
                try:
                    with conn:  # one transaction: all traps or none
                        for trap in traps:
                            trap.apply(conn, trap_rng(self.settings.seed, trap.id))
                    conn.execute("VACUUM")
                finally:
                    conn.close()
            self._verify(tmp, variant)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

        self._comments_path(variant).write_text(json.dumps(self._comments(variant), indent=2))
        manifest = {
            "key": self.manifest_key(variant),
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tables": self._table_counts(dest),
        }
        self._manifest_path(variant).write_text(json.dumps(manifest, indent=2))
        return BuildReport(self.name, variant, str(dest), "built", self._summary(variant))

    def _verify(self, path: Path, variant: str) -> None:
        traps = self.traps(variant)
        if not traps:
            return
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for trap in traps:
                trap.verify(conn)
        finally:
            conn.close()

    @staticmethod
    def _table_counts(path: Path) -> dict[str, int]:
        with SqliteDatabase(path) as db:
            return {t.name: t.row_count for t in db.list_tables() if t.kind == "table"}

    def _summary(self, variant: str) -> dict[str, Any]:
        return {"traps": [t.id for t in self.traps(variant)]}

    # -- comments + open ----------------------------------------------------------

    def _comments(self, variant: str) -> Comments:
        """Base column comments from the dataset folder, plus whatever each trap adds."""
        path = self.spec_dir / "column_comments.yaml"
        merged: Comments = {}
        if path.is_file():
            merged = yaml.safe_load(path.read_text()) or {}
        for trap in self.traps(variant):
            for table, cols in trap.comments.items():
                merged.setdefault(table, {}).update(cols)
        return merged

    def open(self, variant: str) -> SqliteDatabase:
        dest = self.location(variant)
        if not dest.is_file():
            raise DatasetBuildError(
                f"{self.name}:{variant} is not built yet. Run `make setup` first."
            )
        cpath = self._comments_path(variant)
        comments = json.loads(cpath.read_text()) if cpath.is_file() else self._comments(variant)
        return SqliteDatabase(dest, name=f"{self.name}:{variant}", comments=comments)


def load_spec(spec_dir: Path) -> DatasetSpec:
    return DatasetSpec(**yaml.safe_load((spec_dir / "dataset.yaml").read_text()))
