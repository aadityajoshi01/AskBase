# Agent 1 — Live Smoke Results (Part 9)

**Status: NOT YET RUN — honest placeholder.**
The llamafile server was never reachable at `127.0.0.1:8080` during development
(agent-side launches exit silently on this Windows machine; user must start
`OfflineAI\start.bat`). No live outputs or latencies have been fabricated here.

To execute once the server is up:

```
cd agent1_ai-analyst
../.venv/Scripts/python.exe smoke_test.py
```

Fill the tables below with actual output only.

## Endpoint probe
| Field | Value |
|---|---|
| Date/time run | |
| Model id(s) from `/v1/models` | |
| Configured `LLM_MODEL` matches | |

## Generation probes
| # | Question | Status | Latency (ms) | SQL / clarification / error |
|---|---|---|---|---|
| 1 | total order revenue by customer region? | | | |
| 2 | which product categories sold the most units? | | | |
| 3 | how have sales been doing recently? (expect clarification) | | | |

## Synthesis probe
| Field | Value |
|---|---|
| Status | |
| Answer (verbatim) | |
| Latency (ms) | |

## Observed limitations (add real ones here)
-
