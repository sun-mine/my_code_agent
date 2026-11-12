from io import BytesIO
from urllib.error import HTTPError

from anthropic.types import TextBlock, ToolUseBlock

from evals import ollama_eval_client as adapter


def _response(**overrides):
    value = {
        "model": "qwen-test",
        "message": {"role": "assistant", "content": "done"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 21,
        "eval_count": 4,
        "total_duration": 100,
        "load_duration": 10,
        "prompt_eval_duration": 20,
        "eval_duration": 70,
    }
    value.update(overrides)
    return value


def test_history_tools_and_errors_round_trip_to_native_chat(monkeypatch):
    requests = []
    tool_call = ToolUseBlock(
        type="tool_use", id="call-1", name="read_file", input={"path": "a.py"},
    )

    def fake_post(base_url, route, payload, timeout):
        requests.append((base_url, route, payload, timeout))
        return _response(message={
            "role": "assistant",
            "content": "checking",
            "tool_calls": [{"id": "call-2", "function": {
                "name": "write_file", "arguments": '{"path":"b.py","content":"ok"}',
            }}],
        })

    monkeypatch.setattr(adapter, "post_json", fake_post)
    records = []
    client = adapter.OllamaEvalClient("http://localhost:11434", "qwen-test", records=records)
    messages = [
        {"role": "user", "content": [TextBlock(type="text", text="inspect")]},
        {"role": "assistant", "content": [TextBlock(type="text", text="I will."), tool_call]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "permission denied", "is_error": True},
            TextBlock(type="text", text="continue"),
        ]},
    ]
    tools = [{"name": "write_file", "description": "write", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}},
    }}]

    with client.messages.stream(
        model="qwen-test", system="be concise", messages=messages, tools=tools, max_tokens=900,
    ) as stream:
        assert [(event.type, event.text) for event in stream] == [("text", "checking")]
        final = stream.get_final_message()

    payload = requests[0][2]
    assert requests[0][:2] == ("http://localhost:11434", "/api/chat")
    assert payload["stream"] is False
    assert payload["keep_alive"] == "5m"
    assert [message["role"] for message in payload["messages"]] == [
        "system", "user", "assistant", "tool", "user",
    ]
    assert payload["messages"][2]["tool_calls"][0]["function"]["arguments"] == {"path": "a.py"}
    assert payload["messages"][3] == {
        "role": "tool", "tool_name": "read_file", "content": "[tool_error]\npermission denied",
    }
    assert payload["tools"][0]["function"]["parameters"] == tools[0]["input_schema"]
    assert payload["options"] == {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 700}
    assert final.content[0].text == "checking"
    assert final.content[1].type == "tool_use"
    assert final.content[1].input == {"path": "b.py", "content": "ok"}
    assert final.stop_reason == "tool_use"
    assert (final.usage.input_tokens, final.usage.output_tokens) == (21, 4)
    assert records[0]["prompt_eval_count"] == 21
    assert records[0]["eval_duration"] == 70
    assert records[0]["wall_time_seconds"] >= 0


def test_with_options_shares_tool_names_and_preserves_determinism(monkeypatch):
    payloads = []
    monkeypatch.setattr(adapter, "post_json", lambda base, route, payload, timeout: payloads.append(payload) or _response())
    client = adapter.OllamaEvalClient("http://localhost", "qwen-test", timeout=90, seed=7)
    first = client.with_options(timeout=2, max_retries=0)
    with first.messages.stream(
        model="qwen-test", messages=[{"role": "assistant", "content": [{
            "type": "tool_use", "id": "shared", "name": "bash", "input": {"command": "pwd"},
        }]}], max_tokens=32,
    ):
        pass
    second = client.with_options(timeout=3, max_retries=0)
    with second.messages.stream(model="qwen-test", messages=[{"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "shared", "content": "ok", "is_error": False,
    }]}], max_tokens=32):
        pass

    assert payloads[0]["options"] == payloads[1]["options"] == {
        "temperature": 0, "seed": 7, "num_ctx": 8192, "num_predict": 32,
    }
    assert payloads[1]["messages"] == [{"role": "tool", "tool_name": "bash", "content": "ok"}]


def test_missing_usage_is_rejected_instead_of_defaulting_to_zero(monkeypatch):
    response = _response()
    response["eval_count"] = None
    monkeypatch.setattr(adapter, "post_json", lambda *args, **kwargs: response)
    client = adapter.OllamaEvalClient("http://localhost", "qwen-test")

    try:
        with client.messages.stream(model="qwen-test", messages=[], max_tokens=10):
            pass
    except ValueError as exc:
        assert "token usage" in str(exc)
    else:
        raise AssertionError("missing usage should fail")
    assert client.records[-1]["error"] == "missing_token_usage"
    assert client.records[-1]["eval_count"] is None


def test_failed_request_records_unknown_usage_before_reraising(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("offline timeout")

    monkeypatch.setattr(adapter, "post_json", fail)
    records = []
    client = adapter.OllamaEvalClient("http://localhost", "qwen-test", records=records)
    try:
        with client.messages.stream(model="qwen-test", messages=[]):
            pass
    except TimeoutError:
        pass
    else:
        raise AssertionError("request failure should propagate")

    assert records[0]["error"] == "TimeoutError"
    assert records[0]["prompt_eval_count"] is None
    assert records[0]["eval_count"] is None
    assert records[0]["wall_time_seconds"] >= 0


def test_length_done_reason_maps_to_anthropic_max_tokens(monkeypatch):
    monkeypatch.setattr(
        adapter, "post_json", lambda *args, **kwargs: _response(done_reason="length", eval_count=5),
    )
    client = adapter.OllamaEvalClient("http://localhost", "qwen-test")

    with client.messages.stream(model="qwen-test", messages=[], max_tokens=5) as stream:
        final = stream.get_final_message()

    assert final.stop_reason == "max_tokens"


def test_post_json_http_error_is_diagnostic_without_credentials(monkeypatch):
    def fail(request, timeout):
        raise HTTPError(request.full_url, 503, "secret should not leak", {}, BytesIO())

    monkeypatch.setattr(adapter, "urlopen", fail)
    try:
        adapter.post_json("http://user:password@localhost:11434", "/api/chat", {})
    except RuntimeError as exc:
        assert "HTTP 503" in str(exc)
        assert "localhost:11434/api/chat" in str(exc)
        assert "user" not in str(exc)
        assert "password" not in str(exc)
        assert exc.__cause__ is None
    else:
        raise AssertionError("HTTP errors should be translated")
