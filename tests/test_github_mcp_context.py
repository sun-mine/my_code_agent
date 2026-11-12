from copy import deepcopy
import threading
import time
from types import SimpleNamespace

from anthropic.types import TextBlock

import api_server
import api.routes.chat as chat_routes
from components.command_runner import ToolResult
from components.github_mcp import linked_requests
from core.loop import AgentLoop, RunLimits


class ScriptedMessages:
    def __init__(self, responses, on_request=None):
        self.responses = iter(responses)
        self.requests = []
        self.on_request = on_request

    def stream(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        if self.on_request:
            self.on_request()
        response = next(self.responses)

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter(
                    SimpleNamespace(type="text", text=block.text)
                    for block in response.content
                    if block.type == "text"
                )

            def get_final_message(self):
                return response

        return Stream()


def response(text="Done."):
    return SimpleNamespace(
        content=[TextBlock(type="text", text=text)],
        usage=None,
        stop_reason="end_turn",
    )


def tool(name):
    return {
        "name": name,
        "description": f"Test {name}",
        "input_schema": {"type": "object"},
    }


def make_agent(
    responses,
    *,
    tools,
    handlers,
    context_tools=None,
    read_only_tools=(),
    limits=None,
    on_request=None,
):
    events = []
    messages = ScriptedMessages(responses, on_request=on_request)
    agent = AgentLoop(
        client=SimpleNamespace(messages=messages),
        model="offline",
        system="test",
        tools=tools,
        tool_handlers=handlers,
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append,
        action_continuation=False,
        context_tools=context_tools,
        read_only_tools=read_only_tools,
        limits=limits,
    )
    return agent, events, messages


ISSUE_TOOL = "github__issue_read"
PULL_TOOL = "github__pull_request_read"


def test_issue_link_is_loaded_before_first_model_request_with_paired_history():
    order = []
    calls = []

    def read_issue(method, owner, repo, issue_number):
        order.append("context")
        calls.append((method, owner, repo, issue_number))
        return '{"title":"Context first"}'

    agent, events, messages = make_agent(
        [response()],
        tools=[tool(ISSUE_TOOL)],
        handlers={ISSUE_TOOL: read_issue},
        context_tools=linked_requests,
        read_only_tools=[ISSUE_TOOL],
        on_request=lambda: order.append("model"),
    )
    history = [{
        "role": "user",
        "content": "Please inspect https://github.com/openai/codex/issues/123",
    }]

    agent.run(history, run_id="issue-context")

    assert order == ["context", "model"]
    assert calls == [("get", "openai", "codex", 123)]
    assert [message["role"] for message in history] == [
        "user", "assistant", "user", "assistant",
    ]
    use = history[1]["content"][0]
    result = history[2]["content"][0]
    assert (use.name, use.input) == (
        ISSUE_TOOL,
        {"method": "get", "owner": "openai", "repo": "codex", "issue_number": 123},
    )
    assert result == {
        "type": "tool_result",
        "tool_use_id": use.id,
        "content": '{"title":"Context first"}',
        "is_error": False,
    }
    request_roles = [message["role"] for message in messages.requests[0]["messages"]]
    assert request_roles == ["user", "assistant", "user"]
    context_events = [
        event for event in events
        if event["type"] in {"tool_start", "tool_result"}
    ]
    assert [event["type"] for event in context_events] == ["tool_start", "tool_result"]
    assert all(event["purpose"] == "context" for event in context_events)
    assert all(event["run_id"] == "issue-context" for event in context_events)
    assert events[-1]["tool_calls"] == 1


def test_only_links_in_current_user_content_are_loaded():
    calls = []

    def read_issue(method, owner, repo, issue_number):
        calls.append((ISSUE_TOOL, method, owner, repo, issue_number))
        return "old"

    def read_pull(method, owner, repo, pullNumber):
        calls.append((PULL_TOOL, method, owner, repo, pullNumber))
        return "new"

    agent, _, _ = make_agent(
        [response()],
        tools=[tool(ISSUE_TOOL), tool(PULL_TOOL)],
        handlers={ISSUE_TOOL: read_issue, PULL_TOOL: read_pull},
        context_tools=linked_requests,
        read_only_tools=[ISSUE_TOOL, PULL_TOOL],
    )
    history = [
        {"role": "user", "content": "Old: https://github.com/OldOrg/Repo/issues/7"},
        {"role": "assistant", "content": "The old issue was already reviewed."},
        {"role": "user", "content": "Now inspect https://github.com/NewOrg/Next/pull/42"},
    ]

    agent.run(history)

    assert calls == [(PULL_TOOL, "get", "NewOrg", "Next", 42)]


def test_plan_mode_exposes_configured_read_only_tools_but_not_writes():
    agent, _, messages = make_agent(
        [response("Plan ready.")],
        tools=[tool(ISSUE_TOOL), tool("write_file")],
        handlers={},
        read_only_tools=[ISSUE_TOOL],
    )

    agent.run([{"role": "user", "content": "Plan the change"}], plan_mode=True)

    assert [item["name"] for item in messages.requests[0]["tools"]] == [ISSUE_TOOL]


def test_context_prefetch_consumes_tool_budget_and_keeps_history_paired():
    calls = []

    def read_context(value):
        calls.append(value)
        return f"result-{value}"

    agent, events, messages = make_agent(
        [],
        tools=[tool("remote_read")],
        handlers={"remote_read": read_context},
        context_tools=lambda _: [
            {"name": "remote_read", "input": {"value": 1}},
            {"name": "remote_read", "input": {"value": 2}},
        ],
        read_only_tools=["remote_read"],
        limits=RunLimits(max_tool_calls=1),
    )
    history = [{"role": "user", "content": "load two references"}]

    agent.run(history)

    assert calls == [1]
    assert messages.requests == []
    assert [message["role"] for message in history] == ["user", "assistant", "user"]
    use = history[1]["content"][0]
    result = history[2]["content"][0]
    assert result["tool_use_id"] == use.id
    assert result["is_error"] is False
    assert events[-1]["outcome"] == "incomplete"
    assert events[-1]["stop_reason"] == "max_tool_calls"
    assert events[-1]["tool_calls"] == 1


def test_interrupting_context_prefetch_prevents_model_request_and_run_leakage():
    entered = threading.Event()
    handler_runs = []

    def blocking_read(method, owner, repo, issue_number, _cancel_token=None, _run_id=None):
        handler_runs.append(_run_id)
        entered.set()
        deadline = time.monotonic() + 2
        while not _cancel_token.is_cancelled and time.monotonic() < deadline:
            time.sleep(0.005)
        return ToolResult("context cancelled", is_error=True, cancelled=True)

    agent, events, messages = make_agent(
        [response("Second run completed.")],
        tools=[tool(ISSUE_TOOL)],
        handlers={ISSUE_TOOL: blocking_read},
        context_tools=linked_requests,
        read_only_tools=[ISSUE_TOOL],
    )
    first_history = [{
        "role": "user",
        "content": "Cancel https://github.com/openai/codex/issues/9",
    }]
    thread = threading.Thread(
        target=lambda: agent.run(first_history, run_id="cancelled-context")
    )

    thread.start()
    assert entered.wait(timeout=1)
    assert agent.interrupt() is True
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert messages.requests == []
    assert handler_runs == ["cancelled-context"]
    assert first_history[2]["content"][0]["is_error"] is True
    assert first_history[2]["content"][0]["tool_use_id"] == first_history[1]["content"][0].id
    first_results = [event for event in events if event["type"] == "tool_result"]
    assert len(first_results) == 1
    assert first_results[0]["run_id"] == "cancelled-context"
    assert events[-1]["stop_reason"] == "user_interrupt"

    second_history = [{"role": "user", "content": "Continue without a link"}]
    agent.run(second_history, run_id="next-run")
    assert len(messages.requests) == 1
    assert [message["role"] for message in messages.requests[0]["messages"]] == ["user"]
    assert not [
        event for event in events
        if event["type"] == "tool_result" and event["run_id"] == "next-run"
    ]


def test_context_tool_failure_is_an_error_in_the_first_model_request():
    def failing_read(method, owner, repo, issue_number):
        raise RuntimeError("GitHub unavailable")

    agent, events, messages = make_agent(
        [response("The issue could not be loaded.")],
        tools=[tool(ISSUE_TOOL)],
        handlers={ISSUE_TOOL: failing_read},
        context_tools=linked_requests,
        read_only_tools=[ISSUE_TOOL],
    )
    history = [{
        "role": "user",
        "content": "Inspect https://github.com/openai/codex/issues/404",
    }]

    agent.run(history)

    history_result = history[2]["content"][0]
    assert history_result["is_error"] is True
    assert "GitHub unavailable" in history_result["content"]
    request_results = [
        part
        for message in messages.requests[0]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert len(request_results) == 1
    assert request_results[0]["is_error"] is True
    assert "GitHub unavailable" in request_results[0]["content"]
    result_event = next(event for event in events if event["type"] == "tool_result")
    assert result_event["is_error"] is True


def test_load_context_false_skips_prefetch_and_still_requests_the_model():
    parsed_contents = []
    handler_calls = []

    def parse_context(content):
        parsed_contents.append(content)
        return linked_requests(content)

    def read_issue(method, owner, repo, issue_number):
        handler_calls.append((method, owner, repo, issue_number))
        return "unexpected"

    agent, events, messages = make_agent(
        [response()],
        tools=[tool(ISSUE_TOOL)],
        handlers={ISSUE_TOOL: read_issue},
        context_tools=parse_context,
        read_only_tools=[ISSUE_TOOL],
    )
    history = [{
        "role": "user",
        "content": "Execute the plan for https://github.com/openai/codex/issues/123",
    }]

    agent.run(history, load_context=False)

    assert parsed_contents == []
    assert handler_calls == []
    assert len(messages.requests) == 1
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert not [
        event for event in events
        if event["type"] in {"tool_start", "tool_result"}
    ]
    assert events[-1]["tool_calls"] == 0


def test_plan_execute_disables_context_prefetch(monkeypatch):
    captured = []

    def start_run(*args, **kwargs):
        captured.append((args, kwargs))
        return {"run_id": "run-1", "session_id": "session-1"}, None, 200

    monkeypatch.setattr(chat_routes, "_require_agent", lambda: None)
    monkeypatch.setattr(chat_routes, "_start_run", start_run)

    response_ = api_server.app.test_client().post("/api/plan/execute", json={
        "session_id": "session-1",
        "plan_content": "Use https://github.com/openai/codex/issues/123 as prior context.",
        "todos": [{"status": "pending", "content": "Apply the approved change"}],
    })

    assert response_.status_code == 200
    assert response_.get_json()["data"]["status"] == "executing"
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args[0] == "session-1"
    assert "<plan-execute>" in args[1]
    assert args[2:] == ("auto",)
    assert kwargs == {"plan_mode": False, "prefetch_context": False}
