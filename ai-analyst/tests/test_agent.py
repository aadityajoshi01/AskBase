"""Mocked tests for backend.agent.synthesize_answer — no live model needed."""
import pytest

from backend import agent
from backend.llm_client import LLMError

RESULT_OK = {
    "status": "success",
    "columns": ["region", "total_sales"],
    "rows": [["West", 12000], ["East", 9000]],
    "row_count": 2,
    "truncated": False,
    "warnings": ["region has 12% missing values"],
    "verification": {"passed": True},
    "error": None,
}


@pytest.fixture
def mock_chat(monkeypatch):
    calls = []

    def install(content=None, exc=None):
        def fake(messages, max_tokens=None):
            calls.append(messages)
            if exc:
                raise exc
            return content
        monkeypatch.setattr(agent.llm_client, "chat_completion", fake)
        return calls
    return install


def test_success_answer_grounds_in_rows(mock_chat):
    calls = mock_chat(content="West leads with 12000 in total sales.\n")
    out = agent.synthesize_answer("sales by region?", "SELECT region, SUM(total) ...", RESULT_OK)
    assert out["status"] == "success"
    assert out["answer"] == "West leads with 12000 in total sales."
    user_prompt = calls[0][1]["content"]
    assert "West | 12000" in user_prompt and "region | total_sales" in user_prompt
    assert out["evidence"] == {"row_count": 2, "columns": ["region", "total_sales"]}


def test_result_warnings_pass_through(mock_chat):
    mock_chat(content="ok")
    out = agent.synthesize_answer("q", "SELECT 1", RESULT_OK)
    assert "region has 12% missing values" in out["warnings"]


def test_truncated_adds_warning(mock_chat):
    mock_chat(content="ok")
    result = dict(RESULT_OK, truncated=True)
    out = agent.synthesize_answer("q", "SELECT 1", result)
    assert any("truncated" in w for w in out["warnings"])


def test_empty_result_is_success_not_error(mock_chat):
    calls = mock_chat(content="should not be called")
    result = dict(RESULT_OK, rows=[], row_count=0)
    out = agent.synthesize_answer("orders from Atlantis?", "SELECT ...", result)
    assert out["status"] == "success"
    assert "no rows" in out["answer"]
    assert "Empty result set." in out["warnings"]
    assert calls == []  # deterministic path — model never consulted


def test_failed_execution_is_refused(mock_chat):
    calls = mock_chat(content="should not be called")
    result = dict(RESULT_OK, status="error",
                  error={"type": "timeout", "message": "query exceeded time limit"})
    out = agent.synthesize_answer("q", "SELECT 1", result)
    assert out["status"] == "error"
    assert "did not execute" in out["answer"]
    assert out["error"]["type"] == "timeout"
    assert calls == []  # never asks the model to narrate a failure


def test_failed_verification_is_refused(mock_chat):
    mock_chat(content="should not be called")
    result = dict(RESULT_OK, verification={"passed": False,
                                           "reason": "reported total mismatched rows"})
    out = agent.synthesize_answer("q", "SELECT 1", result)
    assert out["status"] == "error"


def test_missing_result_refused(mock_chat):
    mock_chat(content=None)
    for bad in (None, "not a dict", []):
        out = agent.synthesize_answer("q", "SELECT 1", bad)
        assert out["status"] == "error"
        assert out["error"]["type"] == "no_result"


def test_model_offline_returns_error_without_fabrication(mock_chat):
    mock_chat(exc=LLMError("offline", "server unreachable"))
    out = agent.synthesize_answer("q", "SELECT 1", RESULT_OK)
    assert out["status"] == "error"
    assert out["error"]["type"] == "offline"
    assert "will not invent" in out["answer"]
    assert out["evidence"]["row_count"] == 2  # evidence still truthful


def test_large_results_sampled_with_warning(mock_chat):
    calls = mock_chat(content="ok")
    rows = [[f"r{i}", i] for i in range(500)]
    result = dict(RESULT_OK, rows=rows, row_count=500)
    out = agent.synthesize_answer("q", "SELECT 1", result)
    assert any("first 50 of 500" in w for w in out["warnings"])
    prompt = calls[0][1]["content"]
    assert len(prompt) < agent.MAX_PROMPT_CHARS + 1000  # context-bounded
    assert "r499" not in prompt


def test_prompt_forbids_inventing_values():
    assert "ONLY the provided columns and rows" in agent.SYSTEM_PROMPT
