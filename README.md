# ask-or-act

A SQL data-analyst agent that answers when a business question is clear, asks when it is ambiguous, and says so when the data cannot answer. It comes with an evaluation harness that measures that judgment.

> Work in progress. 

## Quickstart

```bash
make install        # uv sync (Python 3.12)
cp .env.example .env  # add ANTHROPIC_API_KEY (needed from milestone 4)
make test
uv run askoract config show --profile full
```

## Layout

| Path | What lives there |
|---|---|
| `configs/` | global settings and one YAML per agent profile (baseline, full, ...) |
| `prompts/` | versioned system prompts, tool descriptions, simulated-user prompt |
| `datasets/<name>/` | dataset definition, column comments, test suites |
| `src/askoract/` | the library: db, datasets, tools, llm, agent, users, benchmarks, eval, tracing |
| `data/`, `runs/` | generated: databases, traces, results (gitignored) |
