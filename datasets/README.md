# Datasets

Each subfolder defines one dataset. The folders hold data and definitions; the build logic lives in `src/askoract/datasets/<name>/`.

```
datasets/<name>/
├── dataset.yaml          # source (URL + sha256), engine, variants
├── column_comments.yaml  # optional: comments for engines without native COMMENT support
└── suites/*.yaml         # test cases (see src/askoract/eval/cases.py for the format)
```

To add a dataset:
1. Create the folder above.
2. Write a builder class that implements the `Dataset` protocol (`src/askoract/datasets/base.py`).
3. Register it with `DATASETS.add("<name>", ...)`.
4. Run `uv run askoract setup --dataset <name>`.
