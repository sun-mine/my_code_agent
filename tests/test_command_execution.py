import json
import os
import shlex
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from components.background_manager import BackgroundManager
from components.command_runner import CancellationToken
from components.worktree_manager import WorktreeManager
from core.loop import AgentLoop
from core.subagent import run_subagent
from tools.base import run_bash, run_read, set_workdir


def _wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition was not met before timeout")


def _pid_is_gone(pid):
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def _tree_command(pid_file: Path, seconds=60):
    path = shlex.quote(str(pid_file))
    return f"sh -c 'sleep {seconds} & echo $! > {path}; wait'"


def test_nonzero_exit_preserves_structured_result(tmp_path):
    set_workdir(tmp_path)
    result = run_bash("printf stdout; printf stderr >&2; exit 7", timeout=5)

    assert result.is_error is True
    assert result.exit_code == 7
    assert result.stdout == "stdout"
    assert result.stderr == "stderr"
    assert result.timed_out is False
    assert result.cancelled is False
    assert result.duration >= 0
    assert json.loads(result.model_content())["exit_code"] == 7


def test_file_tool_error_is_marked_failed(tmp_path):
    set_workdir(tmp_path)
    result = run_read("missing.txt")

    assert result.is_error is True
    assert AgentLoop._tool_output(result)[2]["is_error"] is True


def test_cancellation_stops_command_process_tree(tmp_path):
    set_workdir(tmp_path)
    token = CancellationToken()
    pid_file = tmp_path / "child.pid"
    captured = []
    thread = threading.Thread(
        target=lambda: captured.append(
            run_bash(_tree_command(pid_file), timeout=30, cancellation_token=token)
        )
    )
    thread.start()
    _wait_for(pid_file.exists)
    child_pid = int(pid_file.read_text())

    token.cancel()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert captured[0].cancelled is True
    assert captured[0].is_error is True
    _wait_for(lambda: _pid_is_gone(child_pid))


def test_timeout_stops_command_process_tree(tmp_path):
    set_workdir(tmp_path)
    pid_file = tmp_path / "timeout-child.pid"

    result = run_bash(_tree_command(pid_file), timeout=0.2)
    child_pid = int(pid_file.read_text())

    assert result.timed_out is True
    assert result.cancelled is False
    assert result.is_error is True
    _wait_for(lambda: _pid_is_gone(child_pid))


def test_worktree_run_preserves_nonzero_result(tmp_path):
    manager = WorktreeManager.__new__(WorktreeManager)
    manager._find = lambda _: {"path": str(tmp_path)}

    result = manager.run("test", "printf wt-error >&2; exit 6")

    assert result.is_error is True
    assert result.exit_code == 6
    assert result.stderr == "wt-error"


def test_background_cancel_is_scoped_and_workdir_is_bound(tmp_path):
    manager = BackgroundManager(tmp_path)
    old_token = CancellationToken()
    first_pid = tmp_path / "first.pid"
    started = manager.run(
        _tree_command(first_pid), cancellation_token=old_token, run_id="run-a"
    )
    first_id = started.split()[2]
    _wait_for(first_pid.exists)

    assert manager.cancel(first_id) == "Cancelled 1 background task(s)"
    _wait_for(lambda: manager.tasks[first_id]["status"] == "cancelled")

    new_token = CancellationToken()
    started = manager.run(
        "sleep 0.1; printf alive > relative.txt",
        cancellation_token=new_token,
        run_id="run-b",
    )
    second_id = started.split()[2]
    old_token.cancel()  # a stale run token must not affect the new task
    _wait_for(lambda: manager.tasks[second_id]["status"] == "completed")

    assert (tmp_path / "relative.txt").read_text() == "alive"
    assert manager.drain("run-b")[0]["run_id"] == "run-b"
    assert manager.drain("run-b") == []
    assert manager.drain("run-a") == []  # stale run-a notification was discarded


class _OneToolMessages:
    def __init__(self, command="exit 9"):
        self.calls = 0
        self.command = command

    def stream(self, **kwargs):
        self.calls += 1
        block = SimpleNamespace(
            type="tool_use", id="bash-1", name="bash",
            input={"command": self.command},
        )
        response = SimpleNamespace(content=[block], stop_reason="tool_use", usage=None)
        if self.calls > 1:
            response = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="done")],
                stop_reason="end_turn",
                usage=None,
            )
        return _FakeStream(response)


class _FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.response


def _bash_agent(messages, events):
    return AgentLoop(
        client=SimpleNamespace(messages=messages),
        model="test",
        system="test",
        tools=[{"name": "bash", "input_schema": {"type": "object"}}],
        tool_handlers={"bash": lambda **kw: run_bash(
            kw["command"], cancellation_token=kw.get("_cancel_token")
        )},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append,
    )


def test_agent_tool_result_marks_shell_failure_for_model_and_event(tmp_path):
    set_workdir(tmp_path)
    events = []
    agent = _bash_agent(_OneToolMessages(), events)
    history = [{"role": "user", "content": "run it"}]

    agent.run(history, run_id="failure-run")

    tool_result = history[2]["content"][0]
    assert tool_result["is_error"] is True
    assert json.loads(tool_result["content"])["exit_code"] == 9
    event = next(event for event in events if event["type"] == "tool_result")
    assert event["is_error"] is True
    assert event["exit_code"] == 9


def test_agent_interrupt_waits_for_command_tree_before_terminal_event(tmp_path):
    set_workdir(tmp_path)
    pid_file = tmp_path / "agent-child.pid"
    events = []
    agent = _bash_agent(_OneToolMessages(_tree_command(pid_file)), events)
    thread = threading.Thread(
        target=lambda: agent.run([{"role": "user", "content": "run it"}], run_id="a")
    )
    thread.start()
    _wait_for(pid_file.exists)
    child_pid = int(pid_file.read_text())

    agent.interrupt()
    thread.join(timeout=3)

    assert not thread.is_alive()
    _wait_for(lambda: _pid_is_gone(child_pid))
    result_event = next(event for event in events if event["type"] == "tool_result")
    assert result_event["cancelled"] is True
    event_types = [event["type"] for event in events]
    assert event_types.index("tool_result") < event_types.index("interrupted")
    assert event_types[-1] == "run_end"


def test_cancelled_subagent_does_not_run_tools_after_provider_returns(tmp_path):
    set_workdir(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class BlockingStream:
        def __enter__(self):
            entered.set()
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            release.wait(timeout=3)
            return iter(())

        def close(self):
            closed.set()
            release.set()

        def get_final_message(self):
            block = SimpleNamespace(
                type="tool_use", id="write-1", name="write_file",
                input={"path": "late.txt", "content": "too late"},
            )
            return SimpleNamespace(content=[block], stop_reason="tool_use")

    token = CancellationToken()
    results = []
    thread = threading.Thread(target=lambda: results.append(run_subagent(
        SimpleNamespace(messages=SimpleNamespace(stream=lambda **_: BlockingStream())),
        "test", "do work", "general", token,
    )))
    thread.start()
    assert entered.wait(timeout=1)

    token.cancel()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert closed.is_set()
    assert results[0].cancelled is True
    assert not (tmp_path / "late.txt").exists()


def test_interrupt_closes_blocked_model_stream():
    entered = threading.Event()
    closed = threading.Event()

    class BlockingStream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            entered.set()
            closed.wait(timeout=3)
            return iter(())

        def close(self):
            closed.set()

        def get_final_message(self):
            return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)

    events = []
    agent = AgentLoop(
        client=SimpleNamespace(messages=SimpleNamespace(stream=lambda **_: BlockingStream())),
        model="test", system="test", tools=[], tool_handlers={},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append,
    )
    thread = threading.Thread(target=lambda: agent.run(
        [{"role": "user", "content": "wait"}], run_id="blocked"
    ))
    thread.start()
    assert entered.wait(timeout=1)

    agent.interrupt()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert closed.is_set()
    assert [event["type"] for event in events][-2:] == ["interrupted", "run_end"]


def test_interrupt_during_stream_enter_closes_immediately_after_publish():
    entering = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    iterated = threading.Event()

    class SlowEnterStream:
        def __enter__(self):
            entering.set()
            release.wait(timeout=3)
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            iterated.set()
            return iter(())

        def close(self):
            closed.set()

    agent = AgentLoop(
        client=SimpleNamespace(messages=SimpleNamespace(stream=lambda **_: SlowEnterStream())),
        model="test", system="test", tools=[], tool_handlers={},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
    )
    thread = threading.Thread(target=lambda: agent.run(
        [{"role": "user", "content": "wait"}], run_id="slow-enter"
    ))
    thread.start()
    assert entering.wait(timeout=1)

    agent.interrupt()
    assert thread.is_alive()  # __enter__ itself is not closeable yet
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert closed.is_set()
    assert not iterated.is_set()


def test_non_cancel_error_stops_background_work(tmp_path):
    manager = BackgroundManager(tmp_path)

    class ErrorAfterBackground:
        def __init__(self):
            self.calls = 0

        def stream(self, **_):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("provider failed")
            block = SimpleNamespace(
                type="tool_use", id="bg-1", name="background_run",
                input={"command": "sleep 0.3; printf late > late.txt"},
            )
            return _FakeStream(SimpleNamespace(
                content=[block], stop_reason="tool_use", usage=None,
            ))

    agent = AgentLoop(
        client=SimpleNamespace(messages=ErrorAfterBackground()),
        model="test", system="test",
        tools=[{"name": "background_run", "input_schema": {"type": "object"}}],
        tool_handlers={"background_run": lambda **kw: manager.run(
            kw["command"], cancellation_token=kw.get("_cancel_token"),
            run_id=kw.get("_run_id"),
        )},
        bg=manager,
        bus=SimpleNamespace(read_inbox=lambda _: []),
    )

    agent.run([{"role": "user", "content": "start then fail"}], run_id="failed")

    task = next(iter(manager.tasks.values()))
    _wait_for(lambda: task["status"] == "cancelled")
    assert not (tmp_path / "late.txt").exists()


def test_pre_run_interrupt_skips_model_and_does_not_cancel_next_run():
    class CountingMessages:
        def __init__(self):
            self.calls = 0

        def stream(self, **_):
            self.calls += 1
            return _FakeStream(SimpleNamespace(
                content=[], stop_reason="end_turn", usage=None,
            ))

    messages = CountingMessages()
    events = []
    agent = AgentLoop(
        client=SimpleNamespace(messages=messages),
        model="test", system="test", tools=[], tool_handlers={},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append,
    )

    gate = threading.Event()
    agent.prepare_run("first")
    thread = threading.Thread(target=lambda: (
        gate.wait(),
        agent.run([{"role": "user", "content": "first"}], run_id="first"),
    ))
    thread.start()
    agent.interrupt()  # stop arrives after Thread.start but before run() enters
    gate.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert messages.calls == 0
    assert [event["type"] for event in events][-2:] == ["interrupted", "run_end"]
    assert agent.interrupt() is False  # late stop belongs to the finished run

    events.clear()
    agent.run([{"role": "user", "content": "second"}], run_id="second")

    assert messages.calls == 1
    assert not any(event["type"] == "interrupted" for event in events)
    assert events[-1]["type"] == "run_end"


def test_release_prepared_run_allows_next_admission():
    agent = AgentLoop(
        client=SimpleNamespace(messages=None),
        model="test", system="test", tools=[], tool_handlers={},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
    )

    assert agent.prepare_run("failed-start") == "failed-start"
    assert agent.release_run("another-run") is False
    assert agent.current_run_id == "failed-start"
    assert agent.release_run("failed-start") is True
    assert agent.current_run_id is None
    assert agent.prepare_run("next-run") == "next-run"
    assert agent.release_run("next-run") is True


def test_interrupt_between_multiple_tools_completes_results_before_next_run():
    class ScriptedMessages:
        def __init__(self):
            self.calls = 0

        def stream(self, **_):
            self.calls += 1
            if self.calls == 1:
                content = [
                    SimpleNamespace(type="tool_use", id="first-1", name="first", input={}),
                    SimpleNamespace(type="tool_use", id="second-1", name="second", input={}),
                ]
                return _FakeStream(SimpleNamespace(
                    content=content, stop_reason="tool_use", usage=None,
                ))
            return _FakeStream(SimpleNamespace(
                content=[], stop_reason="end_turn", usage=None,
            ))

    messages = ScriptedMessages()
    events = []
    calls = []
    holder = {}

    def first():
        calls.append("first")
        holder["agent"].interrupt()
        return "first completed"

    agent = AgentLoop(
        client=SimpleNamespace(messages=messages),
        model="test", system="test",
        tools=[
            {"name": "first", "input_schema": {"type": "object"}},
            {"name": "second", "input_schema": {"type": "object"}},
        ],
        tool_handlers={"first": first, "second": lambda: calls.append("second")},
        bg=SimpleNamespace(drain=lambda *_: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=events.append,
    )
    holder["agent"] = agent
    history = [{"role": "user", "content": "run both"}]

    agent.run(history, run_id="cancelled")

    assert calls == ["first"]
    result_blocks = history[-1]["content"]
    assert [result["tool_use_id"] for result in result_blocks] == ["first-1", "second-1"]
    assert result_blocks[0]["is_error"] is False
    assert result_blocks[1]["is_error"] is True
    assert json.loads(result_blocks[1]["content"])["cancelled"] is True

    agent.run(history, run_id="continued")

    assert messages.calls == 2
    assert events[-1]["type"] == "run_end"
