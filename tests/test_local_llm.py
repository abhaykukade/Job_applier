import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from job_applier.llm import LLMError, get_llm, list_local_models
from job_applier.llm.base import extract_json
from job_applier.llm.local_llm import LocalServerLLM, OllamaLLM
from job_applier.pipeline import ai
from job_applier.settings import LLMSettings, Settings, load_settings, save_settings


class Score(BaseModel):
    score: int
    reason: str


def recorder(replies):
    """A mock server returning `replies` in order and recording request bodies."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content) if request.content else None)
        status, body = replies[min(len(seen) - 1, len(replies) - 1)]
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)

    return seen, httpx.Client(transport=httpx.MockTransport(handler))


def ollama_reply(content, **extra):
    return 200, {"message": {"role": "assistant", "content": content}, "done_reason": "stop", "prompt_eval_count": 900, **extra}


def test_extract_json_tolerates_local_model_habits():
    assert extract_json('<think>hmm, 7?</think>\n```json\n{"score": 7, "reason": "ok"}\n```') == {"score": 7, "reason": "ok"}
    assert extract_json('Sure! Here it is: {"score": 1, "reason": "x"} Hope that helps.') == {"score": 1, "reason": "x"}


def test_ollama_sends_context_size_and_schema():
    seen, client = recorder([ollama_reply('{"score": 80, "reason": "good"}')])
    llm = OllamaLLM("qwen2.5:14b", context_tokens=12000, client=client)
    assert llm.structured("sys", "Rate this", Score) == Score(score=80, reason="good")
    body = seen[0]
    assert body["model"] == "qwen2.5:14b"
    assert body["options"]["num_ctx"] == 12000
    assert body["format"]["required"] == ["score", "reason"]
    assert '"score"' in body["messages"][1]["content"]  # schema spelled out in the prompt too


def test_ollama_retries_once_on_bad_json():
    seen, client = recorder([ollama_reply('{"score": "high"}'), ollama_reply('{"score": 70, "reason": "fine"}')])
    assert OllamaLLM("m", client=client).structured("sys", "Rate", Score).score == 70
    assert len(seen) == 2 and "previous reply was invalid" in seen[1]["messages"][1]["content"]


def test_ollama_gives_up_after_retry():
    _, client = recorder([ollama_reply("no idea")])
    with pytest.raises(LLMError, match="valid Score JSON"):
        OllamaLLM("m", client=client).structured("sys", "Rate", Score)


def test_ollama_errors_are_actionable():
    _, client = recorder([(404, {"error": "model 'llama9' not found"})])
    with pytest.raises(LLMError, match="ollama pull llama9"):
        OllamaLLM("llama9", client=client).structured("s", "p", Score)

    _, client = recorder([ollama_reply('{"score": 1, "reason": "x"}', prompt_eval_count=8192)])
    with pytest.raises(LLMError, match="Context size"):
        OllamaLLM("m", context_tokens=8192, client=client).structured("s", "p", Score)

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="is it running"):
        OllamaLLM("m", client=httpx.Client(transport=httpx.MockTransport(down))).structured("s", "p", Score)


def test_local_server_falls_back_when_json_schema_unsupported():
    ok = {"choices": [{"message": {"content": '<think>…</think>{"score": 55, "reason": "meh"}'}, "finish_reason": "stop"}]}
    seen, client = recorder([(400, "response_format json_schema is not supported"), (200, ok)])
    llm = LocalServerLLM("local-model", client=client)
    assert llm.structured("sys", "Rate", Score).score == 55
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[1]["response_format"] == {"type": "json_object"}
    assert llm.mode == "json_object"  # remembered for the next call


def test_list_models():
    _, client = recorder([(200, {"models": [{"name": "qwen2.5:14b"}, {"name": "llama3.1:8b"}]})])
    assert OllamaLLM("", client=client).list_models() == ["llama3.1:8b", "qwen2.5:14b"]
    _, client = recorder([(200, {"data": [{"id": "lmstudio-community/model"}]})])
    assert LocalServerLLM("", client=client).list_models() == ["lmstudio-community/model"]


def test_get_llm_rejects_claude_model_for_local():
    with pytest.raises(LLMError, match="Choose a local model"):
        get_llm(LLMSettings(provider="ollama"))
    assert isinstance(get_llm(LLMSettings(provider="local", model="m")), LocalServerLLM)
    assert list_local_models(LLMSettings(provider="ollama", base_url="http://127.0.0.1:9")) == []  # not running


def test_real_pipeline_schemas_work_through_local_path():
    """The app's actual output models survive the local JSON path."""
    reply = json.dumps({"score": 77, "summary": "s", "strengths": ["a"], "gaps": [], "dealbreakers": []})
    _, client = recorder([ollama_reply(f"```json\n{reply}\n```")])
    match = ai.score_job(OllamaLLM("m", client=client), ai.Resume(), "Engineer", "Acme", "Remote", "Python")
    assert match.score == 77


def test_settings_switch_to_local_and_test_button(monkeypatch):
    from job_applier.web import app as web_app

    monkeypatch.setattr(web_app, "list_local_models", lambda cfg: ["qwen2.5:14b"] if cfg.provider == "ollama" else [])
    web = TestClient(web_app.app)
    web.post("/setup/settings", data={"llm_provider": "ollama", "llm_model": "claude-opus-5-5", "llm_context_tokens": "20000"})
    cfg = load_settings().llm
    assert (cfg.provider, cfg.model, cfg.context_tokens) == ("ollama", "qwen2.5:14b", 20000)
    assert "qwen2.5:14b" in web.get("/setup").text

    class Fake:
        def structured(self, system, prompt, output):
            return output(ok=True, greeting="hello")

    monkeypatch.setattr(web_app, "get_llm", lambda cfg: Fake())
    resp = web.post("/setup/test-llm")
    assert "replied in" in resp.text


def test_queue_asks_to_choose_local_model():
    s = Settings()
    s.llm.provider, s.llm.model = "local", ""
    save_settings(s)
    from job_applier.web.app import app

    assert "Choose a local AI model" in TestClient(app).get("/").text
