# ask-or-act

A data-analyst agent that answers business questions over a SQL database, and knows when **not** to answer. It explores the schema, checks real values, writes and checks its SQL, asks one clarifying question when a request is truly ambiguous, and says so when the data cannot answer.

The project is the agent plus the evaluation that measures that judgment:
- 90 questions over two Northwind databases;
- answers checked against gold result sets;
- an ask-or-act confusion matrix;
- success rates per trap;
- a baseline vs full-agent comparison.

![demo: baseline answers confidently and wrongly; the full agent asks, then answers correctly](docs/demo.gif)

## Results

Both profiles run on Claude Haiku 5.5, so the comparison isolates what the prompt and tools add:
- **`baseline`** gets a one-line prompt and only `run_sql`.
- **`full`** gets the full prompt and all seven tools.

<!-- results:start -->
| Metric | baseline_haiku | full_haiku |
|---|---|---|
| **Accuracy (all)** | **68% (61/90)** | **84% (76/90)** |
| Accuracy: clear | 72% (36/50) | 90% (45/50) |
| Accuracy: ambiguous | 42% (11/26) | 65% (17/26) |
| Accuracy: unanswerable | 100% (14/14) | 100% (14/14) |
| Asked when ambiguous (recall) | 0% (0/26) | 42% (11/26) |
| Asks that were needed (precision) | - | 58% (11/19) |
| Declined unanswerable | 100% (14/14) | 100% (14/14) |
| Declined answerable (false declines) | 0 | 0 |
| Explored schema before SQL | 0% (0/90) | 100% (87/87) |
| Recovered from SQL errors | 100% (22/22) | - |
| Tool calls / question (mean) | 5.5 | 8.4 |
| Tokens / question (in / out) | 9,467 / 992 | 25,470 / 1,636 |
| Cost / question (mean) | $0.0008 | $0.0014 |
| Latency / question (p50) | 5.7s | 8.9s |
| Total cost | $0.074 | $0.130 |

#### By suite

| Suite | baseline_haiku | full_haiku |
|---|---|---|
| northwind_a | 78% (47/60) | 87% (52/60) |
| northwind_b | 47% (14/30) | 80% (24/30) |

#### By trap

| Trap | baseline_haiku | full_haiku |
|---|---|---|
| active_customer | 40% (2/5) | 60% (3/5) |
| active_products | 50% (1/2) | 50% (1/2) |
| amount_in_cents | 20% (1/5) | 40% (2/5) |
| average_definition | 50% (1/2) | 50% (1/2) |
| cancelled_status | 0% (0/6) | 100% (6/6) |
| date_format | 100% (1/1) | 100% (1/1) |
| duplicate_join | 100% (5/5) | 100% (5/5) |
| growth_definition | 0% (0/1) | 0% (0/1) |
| late_orders | 0% (0/2) | 0% (0/2) |
| legacy_customers | 40% (2/5) | 80% (4/5) |
| null_semantics | 100% (2/2) | 100% (2/2) |
| ranking_metric | 60% (3/5) | 100% (5/5) |
| region_definition | 100% (1/1) | 100% (1/1) |
| relative_date | 50% (1/2) | 50% (1/2) |
| revenue_definition | 33% (1/3) | 67% (2/3) |
| ship_vs_customer_country | 50% (1/2) | 100% (2/2) |
| ties | 100% (1/1) | 100% (1/1) |

#### Ask-or-act confusion matrix

**baseline_haiku** (correct without asking on ambiguous: 11)

|  | Asked | Did not ask |
|---|---|---|
| Should ask (ambiguous) | 0 | 26 |
| Should act (clear, unanswerable) | 0 | 64 |

**full_haiku** (correct without asking on ambiguous: 8)

|  | Asked | Did not ask |
|---|---|---|
| Should ask (ambiguous) | 11 | 15 |
| Should act (clear, unanswerable) | 8 | 56 |

Full report, every case score and the run manifest: [`results/haiku-prompt-v2/`](results/haiku-prompt-v2/)
<!-- results:end -->

### What the eval found, and what changed because of it

**1. The first full agent never asked.**
- **Result:** with prompt v1 ([`results/haiku-prompt-v1/`](results/haiku-prompt-v1/)) it explored the schema on every question and handled most traps, but asked on **0 of 26** ambiguous questions.
- **What the traces showed:** it saw the ambiguity and resolved it alone, writing its choice into the `assumption` field, e.g. *"the discounted-only average (about 0.144)…"* next to an answer of 0.0002.
- **Why:** the prompt said a "widely used convention" could settle details, and the model treated every ambiguity as a convention.

**2. Better prompt wording alone barely helped.** Prompt v2 replaces that clause with a concrete rule: if your assumption picks between readings that change the result, ask. It moved recall from 0/26 to only **1/26**.

**3. A harness change did most of the work.** The fix is an *assumption checkpoint*:
- When the agent can ask, hasn't asked yet, and submits an answer with an assumption, `final_answer` hands that assumption back once and asks whether it chooses between readings the user could mean.
- **Recall rose to 11/26**, and accuracy on ambiguous questions from 46% to 65–69%.
- **The cost:** 8 unnecessary asks on 64 clear or unanswerable questions, so 58% of its asks were needed.
- It's a profile setting (`assumption_check`), so it can be compared with and without.

**4. The eval exposed errors in the test set, and they were fixed.**
- **A-017 / A-034 / A-035:** "late" compared raw timestamps, so an order shipped later on its due date counted as late. The agent's clarifying question exposed this, and the gold now compares calendar days.
- **A-030:** the question didn't say whether "2022" meant the order date or the ship date.
- **B-008 to B-011:** the hidden intents said "current accounts", which the simulated user read as "active accounts".

Every fix passed `suite validate` and the oracle test, and both profiles were re-scored on the corrected suite.

**Caveats:**
- **The prompt and checkpoint were developed on this same test set,** so the full agent's numbers are optimistic. The checkpoint is generic and names no traps or definitions, but a held-out suite is the honest next step.
- **Both profiles run on Haiku 5.5** to fit a small budget: about $0.30 for every eval in this README, including the probes. A Sonnet run is one config change (`-p full -p baseline`).

**What still fails:**
- **Amounts in cents** (2/5): the agent sometimes reports the raw cents.
- **Late-order and growth definitions:** it doesn't ask.
- **Agent asks, then misreads "no preference"** (A-014).

## Quickstart

```bash
make install                 # Python 3.12 via uv
make setup                   # download Northwind, build the pristine and trap databases
make test                    # ~190 offline tests: no network, no API key
cp .env.example .env         # add ANTHROPIC_API_KEY

uv run askoract ask "How many orders came from Germany in 2021?"
make eval PROFILES="baseline_haiku full_haiku" MAX_COST=0.5
make demo                    # replays the showcase cases from cache: free
uv run askoract trace latest A-039 --profile full_haiku --html
```

| Command | What it does |
|---|---|
| `askoract ask "<question>" [--sim-intent "..."]` | Answer one question. Shows steps live; you answer clarifying questions, or a simulated user does. |
| `askoract eval -p <profile>... [--suite] [--limit] [--max-cost] [--resume]` | Run suites, score every case, write `runs/<id>/summary.md`. |
| `askoract report [run]` / `publish [run]` | Rebuild a run's tables / copy them into `results/` and this README. |
| `askoract trace [run] [case] [--html]` | Show one run step by step, or list a run's traces with their scores. |
| `askoract demo` | Baseline vs full agent, side by side, on the showcase cases. |
| `askoract suite validate` | Run every gold query and check each case is well formed. |
| `askoract tool run <tool> --args '{...}'` | Call any agent tool by hand, no LLM involved. |
| `askoract db info --variant traps` / `db traps` | Inspect the built databases and what each trap injects. |

## How it works

```
question ──► Agent loop ──► LLMClient (LiteLLM: any provider) ──► response cache (SQLite)
               │  ▲
     tool call ▼  │ result (errors included, so it can recover)
            Toolset: list_tables · describe_table · sample_rows · get_column_stats
                     run_sql · ask_user ──► UserChannel (terminal | simulated LLM user)
                     final_answer {status, answer, sql, assumption}
               │
            Database (read-only SQLite; protocol ready for Postgres)
every LLM call, tool call and reply ──► Tracer ──► runs/<id>/traces/*.jsonl ──► trace viewer
```

- **Agent** ([agent/loop.py](src/askoract/agent/loop.py)): about 150 lines with no framework. It ends on `final_answer`, the step limit, or the per-question cost budget, and nudges the model once if it stops without answering.
- **Tools** ([tools/](src/askoract/tools/)): the descriptions the model sees are versioned in [prompts/tools/v1.yaml](prompts/tools/v1.yaml). `final_answer` runs the submitted SQL before accepting it.
- **Simulated user** ([users/simulated.py](src/askoract/users/simulated.py)): a different model from the agent, holding each case's hidden intent. It answers in business language and never mentions SQL or schema; replies that look like they do are flagged in the trace.
- **Datasets and traps** ([datasets/](src/askoract/datasets/)): the community Northwind for SQLite, pinned by commit and sha256. A seeded, idempotent build produces `pristine.db` and `traps.db` with five injected traps, which `askoract db traps` lists:
  - cancelled orders that keep their order lines;
  - a stale copy of the customer table;
  - amounts stored in cents;
  - a tracking table whose rows multiply on a naive join;
  - an "active customer" flag that disagrees with order recency.
- **Suites** ([datasets/northwind/suites/](datasets/northwind/suites/)): Suite A has 60 questions on pristine Northwind's natural ambiguity, such as revenue with or without discount and freight, or customer vs ship-to country. Suite B has 30 questions targeting the injected traps.
- **Scoring** ([eval/](src/askoract/eval/)): re-runs the agent's final SQL and compares result sets. It also records the ask-or-act decision, trajectory flags (explored before SQL, recovered from errors), and tokens, cost and latency.

## Layout

```
configs/         default.yaml, agents/*.yaml (profiles), demo.yaml
prompts/         agent/, tools/, sim_user/   (versioned text the models see)
datasets/        northwind/: dataset.yaml, column_comments.yaml, suites/*.yaml
src/askoract/    api · cli · config · factory · runs
                 db/ datasets/ tools/ llm/ agent/ users/ benchmarks/ eval/ tracing/
tests/           offline: FakeLLM, tiny synthetic DB, plus real-data checks after setup
results/         published eval reports (versioned)
data/, runs/     generated, gitignored
```
