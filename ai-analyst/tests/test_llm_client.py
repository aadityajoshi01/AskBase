"""Mocked tests for backend.llm_client — run without a live model or GPU."""
import pytest
import requests

from backend import llm_client
from backend.llm_client import LLMError, chat_completion, complete_json, get_config


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, raw_text=None):
        self.status_code = status_code
        self._json = json_body
        self._raw = raw_text

    def json(self):
        if self._raw is not None:
            raise ValueError("not JSON")
        return self._json


def ok_body(content="SELECT 1"):
    return {"choices": [{"message": {"content": content}}]}


def patch_post(monkeypatch, result=None, exc=None):
    def fake_post(url, headers=None, json=None, timeout=None):
        if exc:
            raise exc
        fake_post.calls.append({"url": url, "headers": headers, "payload": json, "timeout": timeout})
        return result
    fake_post.calls = []
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    return fake_post.calls


def test_success_returns_content(monkeypatch):
    calls = patch_post(monkeypatch, result=FakeResponse(json_body=ok_body("SELECT 42")))
    assert chat_completion([{"role": "user", "content": "q"}]) == "SELECT 42"
    assert calls[0]["url"].endswith("/chat/completions")
    assert calls[0]["payload"]["stream"] is False


def test_connection_error_maps_to_offline(monkeypatch):
    patch_post(monkeypatch, exc=requests.exceptions.ConnectionError())
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "offline"


def test_timeout_maps_to_timeout(monkeypatch):
    patch_post(monkeypatch, exc=requests.exceptions.Timeout())
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "timeout"


def test_other_request_exception_maps_to_offline(monkeypatch):
    patch_post(monkeypatch, exc=requests.exceptions.RequestException("boom"))
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "offline"


def test_http_500_maps_to_http(monkeypatch):
    patch_post(monkeypatch, result=FakeResponse(status_code=500, json_body={}))
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "http"
    assert "500" in e.value.message


def test_non_json_body_maps_to_malformed(monkeypatch):
    patch_post(monkeypatch, result=FakeResponse(raw_text="<html>"))
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "malformed"


def test_missing_choices_maps_to_malformed(monkeypatch):
    patch_post(monkeypatch, result=FakeResponse(json_body={"unexpected": True}))
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "malformed"


def test_empty_content_maps_to_empty(monkeypatch):
    patch_post(monkeypatch, result=FakeResponse(json_body=ok_body("   ")))
    with pytest.raises(LLMError) as e:
        chat_completion([])
    assert e.value.type == "empty"


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test:9999/v1/")
    monkeypatch.setenv("LLM_MODEL", "OtherModel")
    monkeypatch.setenv("LLM_TIMEOUT_S", "5")
    monkeypatch.setenv("LLM_MAX_TOKENS", "256")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    cfg = get_config()
    assert cfg["base_url"] == "http://example.test:9999/v1"  # trailing slash stripped
    assert cfg["model"] == "OtherModel"
    assert cfg["timeout_s"] == 5.0
    assert cfg["max_tokens"] == 256
    assert cfg["temperature"] == 0.5
    calls = patch_post(monkeypatch, result=FakeResponse(json_body=ok_body()))
    chat_completion([{"role": "user", "content": "q"}])
    assert calls[0]["url"] == "http://example.test:9999/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-key"
    assert calls[0]["payload"]["model"] == "OtherModel"
    assert calls[0]["payload"]["max_tokens"] == 256
    assert calls[0]["timeout"] == 5.0


def test_invalid_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "not-a-number")
    assert get_config()["timeout_s"] == llm_client.DEFAULT_TIMEOUT_S


def test_no_api_key_means_no_auth_header(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    calls = patch_post(monkeypatch, result=FakeResponse(json_body=ok_body()))
    chat_completion([])
    assert "Authorization" not in calls[0]["headers"]


def test_complete_json_parses_plain_object(monkeypatch):
    monkeypatch.setattr(llm_client, "chat_completion", lambda m, max_tokens=None: '{"a": 1}')
    assert complete_json([]) == {"a": 1}


def test_complete_json_parses_code_fence(monkeypatch):
    text = "Sure!\n```json\n{\"status\": \"ok\", \"sql\": \"SELECT 1\"}\n```"
    monkeypatch.setattr(llm_client, "chat_completion", lambda m, max_tokens=None: text)
    assert complete_json([]) == {"status": "ok", "sql": "SELECT 1"}


def test_complete_json_no_object_maps_to_malformed(monkeypatch):
    monkeypatch.setattr(llm_client, "chat_completion", lambda m, max_tokens=None: "no json here")
    with pytest.raises(LLMError) as e:
        complete_json([])
    assert e.value.type == "malformed"


def test_complete_json_invalid_json_maps_to_malformed(monkeypatch):
    monkeypatch.setattr(llm_client, "chat_completion", lambda m, max_tokens=None: "{broken,}")
    with pytest.raises(LLMError) as e:
        complete_json([])
    assert e.value.type == "malformed"


def test_complete_json_non_object_maps_to_malformed(monkeypatch):
    monkeypatch.setattr(llm_client, "chat_completion", lambda m, max_tokens=None: "[1, 2]")
    with pytest.raises(LLMError) as e:
        complete_json([])
    assert e.value.type == "malformed"
