"""Find a dataset by name.

A dataset is either registered explicitly in DATASETS (for custom Python builders) or
discovered from datasets/<name>/dataset.yaml, whose `builder` field picks a generic
builder. Northwind uses the generic "sqlite_file" builder plus its own traps.
"""

from askoract.config import Settings
from askoract.datasets.base import DATASETS, Dataset
from askoract.datasets.sqlite_file import SqliteFileDataset, load_spec

BUILDERS = {"sqlite_file": SqliteFileDataset}


def get_dataset(settings: Settings, name: str | None = None) -> Dataset:
    name = name or settings.dataset
    if name in DATASETS:
        return DATASETS.get(name)(settings)
    spec_dir = settings.paths.datasets_dir / name
    if not (spec_dir / "dataset.yaml").is_file():
        available = sorted({*DATASETS.names(), *list_dataset_dirs(settings)})
        raise KeyError(f"unknown dataset {name!r}; available: {', '.join(available) or '(none)'}")
    spec = load_spec(spec_dir)
    try:
        builder = BUILDERS[spec.builder]
    except KeyError:
        raise KeyError(
            f"{name}: unknown builder {spec.builder!r}; known: {list(BUILDERS)}"
        ) from None
    return builder(settings, spec, spec_dir)


def list_dataset_dirs(settings: Settings) -> list[str]:
    return sorted(p.parent.name for p in settings.paths.datasets_dir.glob("*/dataset.yaml"))
