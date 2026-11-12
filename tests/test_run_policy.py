import shlex
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock
from anthropic import Anthropic, DefaultHttpxClient

from core.loop import AgentLoop, RunLimits
from tools import base


class ScriptedMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self.responses)

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter(SimpleNamespace(type="text", text=b.text)
                            for b in response.content if b.type == "text")

            def get_final_message(self):
                return response

        return Stream()


def response(*blocks, usage=None):
    return SimpleNamespace(content=list(blocks), usage=usage, stop_reason="end_turn")


def tool(name, call_id="one", **values):
    return ToolUseBlock(type="tool_use", id=call_id, name=name, input=values)


def make_agent(responses, **options):
    events = []
    client = SimpleNamespace(messages=ScriptedMessages(responses))
    agent = AgentLoop(
        client=client, model="offline", system="test", tools=base.BASE_TOOLS,
        tool_handlers=base.BASE_HANDLERS,
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append, **options,
    )
    return agent, events, client.messages


@pytest.fixture
def workspace(tmp_path):
    old = base.get_workdir()
    base.set_workdir(tmp_path)
    yield tmp_path
    base.set_workdir(old)


def test_tool_budget_pairs_all_calls_and_does_not_execute_excess(workspace):
    agent, events, _ = make_agent([
        response(tool("write_file", path="first", content="yes"),
                 tool("write_file", "two", path="second", content="no")),
    ], limits=RunLimits(max_tool_calls=1))
    history = [{"role": "user", "content": "write two files"}]
    agent.run(history)
    assert (workspace / "first").read_text() == "yes"
    assert not (workspace / "second").exists()
    assert [item["tool_use_id"] for item in history[-1]["content"]] == ["one", "two"]
    assert history[-1]["content"][1]["is_error"]
    assert events[-1]["outcome"] == "incomplete"
    assert events[-1]["stop_reason"] == "max_tool_calls"


def test_round_budget_and_usage_budget_stop_new_requests(workspace):
    for limits, usage, expected in (
        (RunLimits(max_rounds=1), None, "max_rounds"),
        (RunLimits(max_tokens=10), SimpleNamespace(input_tokens=8, output_tokens=3), "max_tokens"),
    ):
        agent, events, messages = make_agent([
            response(tool("read_file", path="missing"), usage=usage),
        ], limits=limits)
        history = [{"role": "user", "content": "inspect"}]
        agent.run(history)
        assert len(messages.requests) == 1
        assert events[-1]["stop_reason"] == expected
        assert history[-1]["role"] == "user"


def test_failed_verification_reaches_real_patch_and_pass(workspace):
    (workspace / "solution.py").write_text("answer = 0\n")
    command = f"{shlex.quote(sys.executable)} -c 'from solution import answer; assert answer == 42'"
    agent, events, _ = make_agent([
        response(TextBlock(type="text", text="Done.")),
        response(tool("edit_file", path="solution.py", old_text="0", new_text="42")),
        response(TextBlock(type="text", text="Repaired.")),
    ], verification_command=command)
    history = [{"role": "user", "content": "fix answer"}]
    agent.run(history)
    checks = [e for e in events if e["type"] == "verification"]
    assert [e["state"] for e in checks] == ["failed", "passed"]
    assert [e["exit_code"] for e in events if e["type"] == "tool_result" and e["name"] == "bash"] == [1, 0]
    assert events[-1]["stop_reason"] == "verification_passed"
    assert events[-1]["repair_attempts"] == 1


def test_failed_checks_stop_at_repair_limit(workspace):
    agent, events, messages = make_agent([
        response(TextBlock(type="text", text="Done.")),
        response(TextBlock(type="text", text="Done again.")),
    ], verification_command=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(7)'",
        limits=RunLimits(max_repair_attempts=1))
    agent.run([{"role": "user", "content": "fix"}])
    assert len(messages.requests) == 2
    assert events[-1]["outcome"] == "failed"
    assert events[-1]["repair_attempts"] == 1


def test_rejected_verification_does_not_repeatedly_request_approval(workspace):
    approvals = []
    agent, events, messages = make_agent([
        response(TextBlock(type="text", text="Done.")),
    ], verification_command="exit 0", approval_callback=lambda *args: approvals.append(args) or False)
    agent.run([{"role": "user", "content": "fix"}])
    assert len(approvals) == 1
    assert len(messages.requests) == 1
    assert events[-1]["stop_reason"] == "verification_not_executed"


def test_deadline_cancels_foreground_process_without_user_interrupt(workspace):
    command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(10)'"
    agent, events, _ = make_agent([
        response(tool("bash", command=command)),
    ], limits=RunLimits(max_seconds=0.15))
    agent.run([{"role": "user", "content": "wait"}])
    assert events[-1]["outcome"] == "incomplete"
    assert events[-1]["stop_reason"] == "max_seconds"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["cancelled"] or result["timed_out"]
    assert agent.current_run_id is None


def test_plan_mode_does_not_execute_configured_verification(workspace):
    agent, events, _ = make_agent([
        response(TextBlock(type="text", text="A plan.")),
    ], verification_command="touch unexpected")
    agent.run([{"role": "user", "content": "plan"}], plan_mode=True)
    assert not (workspace / "unexpected").exists()
    assert not any(e["type"] == "verification" for e in events)


def test_truncated_model_response_cannot_be_verified_as_complete(workspace):
    truncated = response(TextBlock(type="text", text="Only part of the repair"))
    truncated.stop_reason = "max_tokens"
    agent, events, _ = make_agent([truncated], verification_command="touch unexpected")
    agent.run([{"role": "user", "content": "fix"}])
    assert events[-1]["outcome"] == "incomplete"
    assert events[-1]["stop_reason"] == "model_output_limit"
    assert not (workspace / "unexpected").exists()


def test_approval_after_deadline_does_not_execute_write(workspace):
    def approve(*args):
        time.sleep(0.1)
        return True

    agent, events, _ = make_agent([
        response(tool("write_file", path="too-late", content="must not be written")),
    ], limits=RunLimits(max_seconds=0.03), approval_callback=approve)
    agent.run([{"role": "user", "content": "write"}])
    assert not (workspace / "too-late").exists()
    assert events[-1]["outcome"] == "incomplete"
    assert events[-1]["stop_reason"] == "max_seconds"


def test_sdk_header_wait_obeys_run_timeout_without_retrying(workspace):
    calls = []

    class DelayedHeaders(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            time.sleep(1)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DelayedHeaders)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    client = Anthropic(
        api_key="test-local", base_url=f"http://127.0.0.1:{server.server_port}",
        max_retries=10, http_client=DefaultHttpxClient(trust_env=False),
    )
    agent, events, _ = make_agent([], limits=RunLimits(max_seconds=0.1))
    agent.client = client
    try:
        started = time.monotonic()
        agent.run([{"role": "user", "content": "inspect"}])
        assert time.monotonic() - started < 0.8
        assert len(calls) == 1
        assert events[-1]["outcome"] == "incomplete"
        assert events[-1]["stop_reason"] == "max_seconds"
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
