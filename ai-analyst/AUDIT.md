# Agent 1 — Repo & Environment Audit (Part 1.1)

Date: 2026-10-24 · Auditor: Agent 1 (AI Analyst workstream)

## Repository state
- Remote: `aadityajoshi01/AskBase-From-questions-to-verified-SQL-insights.` (name ends with a literal `.` — required in HTTPS URLs or the API 404s)
- Content before this audit: README only. **Greenfield** — no `app.py`, no `backend/`, no tests, no requirements.
- Workstream dirs scaffolded: `ai-analyst/`, `data-intelligence/`, `sql-safety/`, `ui-evaluation/` (commit `c6e3fde`).
- Consequence: nothing to reuse; Agent 1 modules (`backend/llm_client.py`, `backend/sql_generator.py`, `backend/agent.py`) are built from scratch against the report §7 contract.

## Development environment (verified on this machine)
| Check | Result |
|---|---|
| Python | 3.14.0 — available via `py` launcher only (no `python` alias; use `py -m venv`, `py -m pip`) |
| pip | 26.2.1 |
| LLM server binaries | `C:\Users\Aaditya Joshi\Desktop\OfflineAI\` — `llamafile-0.10.5.exe`, `Qwen3-8B-Q4_K_M.gguf` (4.7 GB), `start.bat` |
| LLM endpoint (from start.bat) | `http://127.0.0.1:8080`, OpenAI-compatible, `-ngl 99 -c 4096` |
| GPU | RTX 3050 6 GB, full offload intended |

## Constraints recorded for all agents
1. Tests must run **without** a live model or GPU (mocked LLM) — report §3 requirement.
2. Model context is **4096 tokens** — schema prompts must stay compact; generation budgets must fit.
3. No secrets in git: real `.env` is gitignored; only `.env.example` is committed.
4. Team-sync protocol: `git pull` before work, `git push` after every committed increment.
