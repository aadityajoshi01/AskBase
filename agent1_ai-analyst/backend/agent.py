"""Answer synthesis (Agent 1).

synthesize_answer() turns an ACTUAL executed+verified query result into a
plain-language answer. Ground rules (report §3):
- never invent values — the model only sees real rows/columns;
- failed execution or failed verification is refused, never narrated as insight;
- empty result is a valid, honestly-explained success, not an error.
"""
from __future__ import annotations

from backend import llm_client
from backend.llm_client import LLMError

MAX_ROWS_IN_PROMPT = 50
MAX_PROMPT_CHARS = 3000

SYSTEM_PROMPT = """You are AskBase's analyst. Explain, in 1-3 short sentences, what the given query result means for the user's question.
Rules:
- Use ONLY the provided columns and rows. Never add numbers, currencies, units, comparisons or time periods that are not in the data.
- If the data cannot answer the question, say so plainly.
- Mention relevant limitations stated in the warnings.
Answer with plain text only."""


def _refusal(result: dict) -> dict:
    error = result.get("error") if isinstance(result.get("error"), dict) else {
        "type": "execution_failed",
        "message": result.get("error") or "query execution did not succeed",
    }
    return {"status": "error",
            "answer": "No answer: the query did not execute/verify successfully, "
                      "so there are no verified results to report.",
            "warnings": [f"{error.get('type', 'error')}: {error.get('message', '')}".strip(": ")],
            "evidence": {"row_count": 0, "columns": []},
            "error": error}


def _format_rows(columns: list, rows: list) -> tuple[str, bool]:
    """Serialize rows for the prompt with hard bounds; returns (text, sampled)."""
    lines = [" | ".join(str(c) for c in columns)]
    sampled = len(rows) > MAX_ROWS_IN_PROMPT
    for row in rows[:MAX_ROWS_IN_PROMPT]:
        candidate = "\n".join(lines) + "\n" + " | ".join(str(v) for v in row)
        if len(candidate) > MAX_PROMPT_CHARS:
            sampled = True
            break
        lines.append(" | ".join(str(v) for v in row))
    return "\n".join(lines), sampled


def synthesize_answer(question: str, sql: str, result: dict | None) -> dict:
    """Produce the contract output from Agent 3's execution/verification result."""
    if not isinstance(result, dict):
        return _refusal({"error": {"type": "no_result",
                                   "message": "no execution result was provided"}})

    status = result.get("status")
    verification = result.get("verification") or {}
    if status != "success" or verification.get("passed") is False:
        return _refusal(result)

    columns = result.get("columns") or []
    rows = result.get("rows") or []
    row_count = result.get("row_count", len(rows))
    base_warnings = [str(w) for w in result.get("warnings", [])]

    if result.get("truncated"):
        base_warnings.append("Results were truncated by the executor's row limit.")

    evidence = {"row_count": row_count, "columns": list(columns)}

    if row_count == 0 and not rows:
        # Empty is a legitimate, complete answer — deterministic text, no model needed.
        return {"status": "success",
                "answer": f"The query ran successfully but returned no rows, so there are "
                          f"no records matching “{question}”. An empty result is not an "
                          f"error; filters or spelling may explain it.",
                "warnings": base_warnings + ["Empty result set."],
                "evidence": evidence,
                "error": None}

    rows_text, sampled = _format_rows(columns, rows)
    if sampled:
        base_warnings.append(
            f"Answer summarized from the first {MAX_ROWS_IN_PROMPT} of {row_count} rows.")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"Question: {question}\nSQL: {sql}\n"
            f"Warnings: {'; '.join(base_warnings) or 'none'}\n"
            f"Result ({row_count} rows):\n{rows_text}"},
    ]
    try:
        answer = llm_client.chat_completion(messages)
    except LLMError as exc:
        return {"status": "error",
                "answer": "No answer: the explanation model was unavailable, and AskBase "
                          "will not invent an answer.",
                "warnings": base_warnings, "evidence": evidence,
                "error": exc.to_dict()}

    return {"status": "success", "answer": answer.strip(),
            "warnings": base_warnings, "evidence": evidence, "error": None}
