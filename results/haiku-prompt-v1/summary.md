# Results: eval-20261113-224946

| Metric | baseline_haiku | full_haiku |
|---|---|---|
| **Accuracy (all)** | **67% (60/90)** | **77% (69/90)** |
| Accuracy: clear | 68% (34/50) | 86% (43/50) |
| Accuracy: ambiguous | 46% (12/26) | 46% (12/26) |
| Accuracy: unanswerable | 100% (14/14) | 100% (14/14) |
| Asked when ambiguous (recall) | 0% (0/26) | 0% (0/26) |
| Asks that were needed (precision) | - | - |
| Declined unanswerable | 100% (14/14) | 100% (14/14) |
| Declined answerable (false declines) | 0 | 0 |
| Explored schema before SQL | 0% (0/90) | 100% (89/89) |
| Recovered from SQL errors | 100% (22/22) | 100% (3/3) |
| Tool calls / question (mean) | 5.4 | 7.3 |
| Tokens / question (in / out) | 9,458 / 991 | 17,919 / 1,175 |
| Cost / question (mean) | $0.0008 | $0.0011 |
| Latency / question (p50) | 5.7s | 6.7s |
| Total cost | $0.074 | $0.096 |

## By suite

| Suite | baseline_haiku | full_haiku |
|---|---|---|
| northwind_a | 77% (46/60) | 78% (47/60) |
| northwind_b | 47% (14/30) | 73% (22/30) |

## By trap

| Trap | baseline_haiku | full_haiku |
|---|---|---|
| active_customer | 40% (2/5) | 40% (2/5) |
| active_products | 50% (1/2) | 50% (1/2) |
| amount_in_cents | 20% (1/5) | 40% (2/5) |
| average_definition | 50% (1/2) | 50% (1/2) |
| cancelled_status | 0% (0/6) | 83% (5/6) |
| date_format | 100% (1/1) | 100% (1/1) |
| duplicate_join | 100% (5/5) | 100% (5/5) |
| growth_definition | 0% (0/1) | 0% (0/1) |
| late_orders | 50% (1/2) | 0% (0/2) |
| legacy_customers | 40% (2/5) | 80% (4/5) |
| null_semantics | 100% (2/2) | 100% (2/2) |
| ranking_metric | 60% (3/5) | 60% (3/5) |
| region_definition | 100% (1/1) | 100% (1/1) |
| relative_date | 50% (1/2) | 100% (2/2) |
| revenue_definition | 33% (1/3) | 33% (1/3) |
| ship_vs_customer_country | 50% (1/2) | 50% (1/2) |
| ties | 100% (1/1) | 100% (1/1) |

## Ask-or-act confusion matrix

**baseline_haiku** (correct without asking on ambiguous: 12)

|  | Asked | Did not ask |
|---|---|---|
| Should ask (ambiguous) | 0 | 26 |
| Should act (clear, unanswerable) | 0 | 64 |

**full_haiku** (correct without asking on ambiguous: 12)

|  | Asked | Did not ask |
|---|---|---|
| Should ask (ambiguous) | 0 | 26 |
| Should act (clear, unanswerable) | 0 | 64 |
