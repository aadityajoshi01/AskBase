# AskBase — From Questions to Verified SQL Insights

AI-powered data analyst: connect a database, ask a question in plain English, get an answer
grounded in verified SQL execution, with result tables, charts and transparent data-quality warnings.

## Workstreams (four parallel engineering agents)

| Directory | Agent | Owns |
|-----------|-------|------|
| `agent1_ai-analyst/` | Agent 1 — AI Analyst & SQL Generation | NL interpretation, schema-aware SQL gen, bounded repair, answer synthesis |
| `agent2_data-intelligence/` | Agent 2 — Database Intelligence & Data Quality | Schema discovery, profiling, synthetic test DB (read-only) |
| `agent3_sql-safety/` | Agent 3 — SQL Safety, Execution & Verification | Validation, restricted execution, result verification |
| `agent4_ui-evaluation/` | Agent 4 — UI, Evaluation & Integration | App UI, orchestration, evaluation, integration |

## Reference

Full specification: [`AskBase_AI_Agent_Work_Reports.pdf`](AskBase_AI_Agent_Work_Reports.pdf)

- §2 Target architecture and module layout
- §3–§6 Per-agent responsibilities, interfaces, testing, acceptance
- §7 Shared integration contract and response envelope
- §8 Integration sequence and test gates (Gate 1–9)
- §9 Final acceptance checklist · §10 Demonstration plan

> This structure is a target architecture. The actual source tree, DB types, UI framework and current
> implementation must be confirmed by inspecting the code before coding against specific paths.
