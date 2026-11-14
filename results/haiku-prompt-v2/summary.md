# Results: eval-20261113-230542

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

## By suite

| Suite | baseline_haiku | full_haiku |
|---|---|---|
| northwind_a | 78% (47/60) | 87% (52/60) |
| northwind_b | 47% (14/30) | 80% (24/30) |

## By trap

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

## Ask-or-act confusion matrix

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
