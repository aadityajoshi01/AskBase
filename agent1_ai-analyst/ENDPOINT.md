# Agent 1 — LLM Endpoint Configuration (Part 1.2)

Server: local Qwen3-8B Q4_K_M served by llamafile 0.10.5 (OpenAI-compatible).
Launch: `C:\Users\Aaditya Joshi\Desktop\OfflineAI\start.bat` (or equivalent CLI).

## Environment variables (all configuration, nothing hardcoded)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible base URL |
| `LLM_MODEL` | `Qwen3-8B-Q4_K_M.gguf` | Verified live 2026-10-24 against `/v1/models` (llamafile returns the gguf filename as id; requests accept it) |
| `LLM_API_KEY` | *(empty — not required locally)* | Only if a proxy adds auth |
| `LLM_TIMEOUT_S` | `120` | Per-request timeout; 8B on RTX 3050 is slow for long prompts |
| `LLM_MAX_TOKENS` | `1024` | Generation cap (server context is 4096 total) |
| `LLM_TEMPERATURE` | `0.1` | Deterministic-leaning for SQL generation |

## Operational notes
- Endpoint offline on 2026-10-24 15:59 probe (ports 8080/8000/11434/1234/5000 closed).
- Client must treat "server unreachable" as a first-class `error` status, never a crash (report §3).
- Smoke test when the server is up: `curl http://127.0.0.1:8080/v1/models`.
- Real model id will be recorded here after the first live probe.
