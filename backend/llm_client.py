"""OpenAI-compatible LLM client for the local Qwen3 llamafile server.

All configuration comes from environment variables (see .env.example).
Failures are reported as LLMError with a machine-readable `type` so callers
never crash and never present a failed call as success:
offline | timeout | http | malformed | empty.
"""
from __future__ import annotations

import json
import os

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "Qwen3-8B-Q4_K_M"
DEFAULT_TIMEOUT_S = 120
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.1


class LLMError(Exception):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.type = error_type
        self.message = message

    def to_dict(self) -> dict:
        return {"type": self.type, "message": self.message}


def get_config() -> dict:
    """Read LLM settings from the environment with safe defaults."""
    return {
        "base_url": os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "model": os.getenv("LLM_MODEL", DEFAULT_MODEL),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "timeout_s": _as_float(os.getenv("LLM_TIMEOUT_S"), DEFAULT_TIMEOUT_S),
        "max_tokens": _as_int(os.getenv("LLM_MAX_TOKENS"), DEFAULT_MAX_TOKENS),
        "temperature": _as_float(os.getenv("LLM_TEMPERATURE"), DEFAULT_TEMPERATURE),
    }


def _as_float(raw, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def chat_completion(messages: list[dict], max_tokens: int | None = None) -> str:
    """Send a chat request and return the assistant text.

    Raises LLMError (offline | timeout | http | malformed | empty) instead of
    leaking raw transport errors to callers.
    """
    cfg = get_config()
    url = f"{cfg['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else cfg["max_tokens"],
        "temperature": cfg["temperature"],
        "stream": False,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout_s"])
    except requests.exceptions.ConnectionError as exc:
        raise LLMError("offline", f"LLM server unreachable at {url}") from exc
    except requests.exceptions.Timeout as exc:
        raise LLMError("timeout", f"LLM request exceeded {cfg['timeout_s']}s") from exc
    except requests.exceptions.RequestException as exc:
        raise LLMError("offline", f"LLM request failed: {exc}") from exc

    if resp.status_code != 200:
        raise LLMError("http", f"LLM server returned HTTP {resp.status_code}")

    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("malformed", f"Unexpected response shape: {exc}") from exc

    if not content or not str(content).strip():
        raise LLMError("empty", "LLM returned an empty completion")

    return str(content)


def complete_json(messages: list[dict], max_tokens: int | None = None) -> dict:
    """Chat and parse the completion as one JSON object.

    Tolerates code fences and surrounding prose; raises LLMError('malformed')
    when no JSON object can be extracted.
    """
    text = chat_completion(messages, max_tokens=max_tokens)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("malformed", "No JSON object found in LLM output")
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise LLMError("malformed", f"Invalid JSON in LLM output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("malformed", "LLM JSON output is not an object")
    return parsed
