# Agent 1 — Live Smoke Results (Part 9)

**RUN: 2026-10-24, live Qwen3-8B server** (`start.bat` by user). All numbers below are actual outputs of `smoke_test.py` + one re-probe; nothing hand-edited.

## Endpoint probe
| Field | Value |
|---|---|
| Endpoint | `http://127.0.0.1:8080/v1` (llamafile 0.10.5, `-ngl 99 -c 4096`) |
| Model id from `/v1/models` | `Qwen3-8B-Q4_K_M.gguf` |
| Configured `LLM_MODEL` matches | ❌ default was `Qwen3-8B-Q4_K_M` (no `.gguf`) — llamafile accepted it anyway; `ENDPOINT.md` now records the exact id |

## Generation probes
| # | Question | Status | Latency (ms) | Actual output |
|---|---|---|---|---|
| 1 | total order revenue by customer region? | ❌ error (empty) | 45,617 | `LLM returned an empty completion` |
| 1-retry | same question | ✅ ok | 28,074 | `SELECT c.region, SUM(o.total) AS total_revenue FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.region ORDER BY total_revenue DESC` |
| 2 | which product categories sold the most units overall? | ✅ ok | 19,843 | `SELECT p.category, SUM(oi.quantity) AS total_units FROM order_items oi JOIN products p ON oi.product_id = p.id GROUP BY p.category ORDER BY total_units DESC` — passed identifier guard, alias-aware |
| 3 | how have sales been doing recently? (expect clarify) | ✅ clarification_needed | 22,824 | “Please specify the time period for ‘recently’ (e.g., last week, last month, last quarter).” |
| 4 (extra) | how many orders exist per region? | ✅ ok | 30,528 | correct join + `COUNT` grouped by `customers.region` |

Assumptions surfaced by the model (verbatim, Q2): “Assuming all quantities are valid and no missing values in product categories”.

## Synthesis probe
Status `success`, 9,266 ms. Verbatim answer on the deliberately mismatched fixture
(Q2's SQL vs region/revenue rows): *“The result provided does not answer the question. The query's output includes regions and revenue, but the question asks about product categories and units sold. The data does not contain the required category and unit information.”* — the model refused to force an unsupported answer; exactly the §3 honesty property.

## Findings & known limitations (live evidence)
1. **Intermittent empty completions** (1 of 2 attempts on Q1): suspected Qwen3 thinking tokens consuming the 1024-token budget under llamafile's chat template. Correctly surfaced as `error/empty`, never a crash. **Gate 2 decision needed:** treat `empty` as transiently retryable in the repair loop (currently only `invented_identifier` auto-repairs). Mitigations to test: raise `LLM_MAX_TOKENS`, or disable thinking via template flag.
2. **Latency 20–46 s per generation** on RTX 3050 6 GB — fine for scripted demo, painful for interactive UI; Agent 4 must design loading states accordingly.
3. JSON-only instruction followed reliably when content is produced (4/5 outputs parsed; no fence fallback needed in practice).
4. Ambiguity behavior works on the real model, not just mocks (Q3).

## §3 acceptance re-check with live evidence
Structured SQL-generation output ✅ (live) · handles model failures cleanly ✅ (empty→error, offline proven earlier) · never executes SQL ✅ · clarification on real model ✅ · grounded synthesis ✅.
