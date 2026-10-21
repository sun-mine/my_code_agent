import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import api_server
from components.session_store import SessionStore


class ControlledAgent:
    def __init__(self):
        self.event_callback = None
        self.approval_callback = None
        self.started = threading.Event()
        self.release = threading.Event()
        self._interrupt_flag = threading.Event()
        self.state = "idle"
        self.current_run_id = None
        self.token_threshold = 100000
        self.tools = []
        self.model = "claude-test"
        self.system = "test"

    def run(self, history, run_id=None, plan_mode=False):
        self.current_run_id = run_id
        self.state = "thinking"
        self.event_callback({"type": "run_start", "run_id": run_id})
        self.event_callback({"type": "chunk", "text": "early", "run_id": run_id})
        self.started.set()
        self.release.wait(timeout=5)
        if self._interrupt_flag.is_set():
            self.event_callback({"type": "interrupted", "run_id": run_id})
        history.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
            ],
        })
        self.event_callback({"type": "chunk", "text": "late", "run_id": run_id})
        self.event_callback({"type": "run_end", "run_id": run_id})
        self.state = "idle"
        self.current_run_id = None

    def interrupt(self):
        self._interrupt_flag.set()

    def reset_interrupt(self):
        self._interrupt_flag.clear()


class PartialInterruptAgent(ControlledAgent):
    def run(self, history, run_id=None, plan_mode=False):
        self.current_run_id = run_id
        self.state = "thinking"
        self.event_callback({"type": "run_start", "run_id": run_id})
        self.event_callback({"type": "agent_state", "state": "thinking", "run_id": run_id})
        self.event_callback({"type": "thinking", "text": "unfinished reasoning", "run_id": run_id})
        self.event_callback({"type": "chunk", "text": "partial answer", "run_id": run_id})
        self.started.set()
        self._interrupt_flag.wait(timeout=5)
        self.event_callback({"type": "interrupted", "run_id": run_id})
        self.event_callback({"type": "run_end", "run_id": run_id})
        self.state = "idle"
        self.current_run_id = None


def wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


@pytest.fixture
def isolated_server(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(workspace / ".agent" / "sessions")
    first = store.create(workdir=str(workspace), mode="auto")
    agent = ControlledAgent()
    emitted = []

    monkeypatch.setattr(api_server, "_session_store", store)
    monkeypatch.setattr(api_server, "_session_stores", {str(workspace.resolve()): store})
    monkeypatch.setattr(api_server, "_active_session_id", first["id"])
    monkeypatch.setattr(api_server, "_active_store_key", str(workspace.resolve()))
    monkeypatch.setattr(api_server, "conversation_history", [])
    monkeypatch.setattr(api_server, "agent_instance", agent)
    monkeypatch.setattr(api_server, "agent_config", {
        "workdir": str(workspace),
        "model": agent.model,
        "system_prompt_len": 0,
        "tool_definitions_len": 0,
    })
    monkeypatch.setattr(api_server, "_cfg", None)
    monkeypatch.setattr(api_server, "_skills", None)
    monkeypatch.setattr(api_server, "todo_manager", None)
    monkeypatch.setattr(api_server, "task_manager", None)
    monkeypatch.setattr(api_server, "memory_manager", None)
    monkeypatch.setattr(api_server, "safety_manager", None)
    monkeypatch.setattr(api_server, "_running_ctx", None)
    monkeypatch.setattr(api_server, "_run_thread", None)
    monkeypatch.setattr(api_server, "_is_running", False)
    monkeypatch.setattr(api_server, "_current_mode", "auto")
    monkeypatch.setattr(api_server, "_approval_timeout_seconds", 120.0)
    monkeypatch.setattr(api_server.socketio, "emit", lambda name, payload: emitted.append((name, payload)))
    api_server._pending_approvals.clear()
    api_server.app.config.update(TESTING=True)

    yield {
        "workspace": workspace,
        "store": store,
        "first": first,
        "agent": agent,
        "emitted": emitted,
    }

    agent.release.set()
    wait_for(lambda: api_server._running_ctx is None)
    api_server._pending_approvals.clear()


def data(response):
    return response.get_json()["data"]


def test_new_session_during_run_keeps_history_events_and_persistence_isolated(isolated_server):
    state = isolated_server
    client = api_server.app.test_client()
    sid_a = state["first"]["id"]

    started = client.post("/api/chat", json={
        "session_id": sid_a,
        "message": "fix the race",
        "mode": "auto",
    })
    assert started.status_code == 200
    run_id = data(started)["run_id"]
    assert state["agent"].started.wait(timeout=1)

    created = client.post("/api/sessions/new", json={})
    assert created.status_code == 200
    snapshot_b = data(created)
    sid_b = snapshot_b["session_id"]
    assert sid_b != sid_a
    assert snapshot_b["history"] == []
    assert snapshot_b["running"] is False

    snapshot_a = data(client.get(f"/api/history?session_id={sid_a}"))
    assert snapshot_a["running"] is True
    assert snapshot_a["run_id"] == run_id
    assert snapshot_a["history"] == [{"role": "user", "content": "fix the race"}]
    assert [event["type"] for event in snapshot_a["events"]] == ["run_start", "chunk"]
    assert [event["event_seq"] for event in snapshot_a["events"]] == sorted(
        event["event_seq"] for event in snapshot_a["events"]
    )

    loaded_b = data(client.post(f"/api/sessions/{sid_b}/load"))
    assert loaded_b["history"] == []
    assert api_server._running_ctx.history is not api_server.conversation_history
    loaded_a = data(client.post(f"/api/sessions/{sid_a}/load"))
    assert loaded_a["running"] is True
    assert loaded_a["events"] == snapshot_a["events"]

    state["agent"].release.set()
    wait_for(lambda: api_server._running_ctx is None)

    completed_a = data(client.get(f"/api/history?session_id={sid_a}"))
    assert completed_a["running"] is False
    assert completed_a["events"] == []
    assert completed_a["history"][-1]["content"][1] == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "read_file",
        "input": {"path": "README.md"},
    }
    assert data(client.get(f"/api/history?session_id={sid_b}"))["history"] == []

    persisted = json.loads((state["store"].directory / f"{sid_a}.json").read_text())
    assert persisted["history"][-1] == completed_a["history"][-1]
    agent_events = [payload for name, payload in state["emitted"] if name == "agent_event"]
    assert agent_events
    assert all(event["session_id"] == sid_a for event in agent_events)
    assert all(event["run_id"] == run_id for event in agent_events)


def test_run_admission_is_atomic_for_concurrent_submissions(isolated_server):
    state = isolated_server
    sid = state["first"]["id"]

    def submit(message):
        with api_server.app.test_client() as client:
            return client.post("/api/chat", json={"session_id": sid, "message": message}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, ["one", "two"]))

    assert sorted(statuses) == [200, 409]
    state["agent"].release.set()
    wait_for(lambda: api_server._running_ctx is None)
    history = state["store"].get(sid)["history"]
    assert len([item for item in history if item["role"] == "user"]) == 1


def test_unknown_history_id_returns_404_without_initialization_side_effect(isolated_server):
    state = isolated_server
    client = api_server.app.test_client()
    stores_before = dict(api_server._session_stores)
    active_before = api_server._active_session_id
    files_before = sorted(state["store"].directory.glob("*.json"))

    response = client.get("/api/history?session_id=session_00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert api_server._session_stores == stores_before
    assert api_server._active_session_id == active_before
    assert sorted(state["store"].directory.glob("*.json")) == files_before


def test_partial_stream_is_saved_before_terminal_event_after_switch_and_interrupt(
    isolated_server, monkeypatch
):
    state = isolated_server
    partial_agent = PartialInterruptAgent()
    monkeypatch.setattr(api_server, "agent_instance", partial_agent)
    terminal_snapshots = []

    def capture(name, payload):
        state["emitted"].append((name, payload))
        if name == "agent_event" and payload.get("type") == "run_end":
            terminal_snapshots.append(api_server._session_snapshot(payload["session_id"]))

    monkeypatch.setattr(api_server.socketio, "emit", capture)
    client = api_server.app.test_client()
    sid_a = state["first"]["id"]
    response = client.post("/api/chat", json={
        "session_id": sid_a,
        "message": "stream then stop",
    })
    run_id = data(response)["run_id"]
    assert partial_agent.started.wait(timeout=1)

    sid_b = data(client.post("/api/sessions/new", json={}))["session_id"]
    assert data(client.post(f"/api/sessions/{sid_b}/load"))["history"] == []
    assert data(client.post(f"/api/sessions/{sid_a}/load"))["running"] is True
    assert client.post("/api/chat/interrupt", json={
        "session_id": sid_a,
        "run_id": run_id,
    }).status_code == 200
    wait_for(lambda: api_server._running_ctx is None)
    wait_for(lambda: len(terminal_snapshots) == 1)

    terminal_snapshot = terminal_snapshots[0]
    assert terminal_snapshot["running"] is False
    assert terminal_snapshot["events"] == []
    partial = terminal_snapshot["history"][-1]
    assert partial["role"] == "assistant"
    texts = [block["text"] for block in partial["content"]]
    assert texts == [
        "[Partial thinking before interruption]\nunfinished reasoning",
        "partial answer",
    ]

    client.post(f"/api/sessions/{sid_b}/load")
    loaded_again = data(client.post(f"/api/sessions/{sid_a}/load"))
    assert loaded_again["history"] == terminal_snapshot["history"]
    assert sum(message["role"] == "assistant" for message in loaded_again["history"]) == 1
    run_end = [
        payload for name, payload in state["emitted"]
        if name == "agent_event" and payload.get("type") == "run_end"
    ][0]
    assert run_end["session_id"] == sid_a
    assert run_end["run_id"] == run_id
    assert run_end["event_seq"] == terminal_snapshot["event_seq"]


def test_running_session_guards_and_targeted_interrupt_approval(isolated_server, tmp_path):
    state = isolated_server
    client = api_server.app.test_client()
    sid_a = state["first"]["id"]
    started = client.post("/api/chat", json={
        "session_id": sid_a,
        "message": "manual operation",
        "mode": "manual",
    })
    run_id = data(started)["run_id"]
    assert state["agent"].started.wait(timeout=1)
    sid_b = data(client.post("/api/sessions/new", json={}))["session_id"]

    assert client.delete(f"/api/history?session_id={sid_a}").status_code == 409
    assert client.delete(f"/api/history?session_id={sid_b}").status_code == 200
    assert client.delete(f"/api/sessions/{sid_a}").status_code == 409
    assert client.post("/api/model", json={"model": "claude-test-2"}).status_code == 409

    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    assert client.post("/api/sessions/new", json={"workdir": str(other_workspace)}).status_code == 409

    approval_result = []
    approval_thread = threading.Thread(
        target=lambda: approval_result.append(
            state["agent"].approval_callback("approval-1", "write_file", {"path": "x"})
        )
    )
    approval_thread.start()
    wait_for(lambda: "approval-1" in api_server._pending_approvals)
    assert client.post("/api/chat/tool_approve", json={
        "tool_use_id": "approval-1",
        "session_id": sid_b,
        "run_id": run_id,
    }).status_code == 409
    assert approval_thread.is_alive()
    assert client.post("/api/chat/tool_approve", json={
        "tool_use_id": "approval-1",
        "session_id": sid_a,
        "run_id": run_id,
        "approved": True,
    }).status_code == 200
    approval_thread.join(timeout=1)
    assert approval_result == [True]

    rejected_result = []
    rejected_thread = threading.Thread(
        target=lambda: rejected_result.append(
            state["agent"].approval_callback("approval-reject", "write_file", {"path": "x"})
        )
    )
    rejected_thread.start()
    wait_for(lambda: "approval-reject" in api_server._pending_approvals)
    assert client.post("/api/chat/tool_approve", json={
        "tool_use_id": "approval-reject",
        "session_id": sid_a,
        "run_id": run_id,
        "approved": False,
    }).status_code == 200
    rejected_thread.join(timeout=1)
    assert rejected_result == [False]

    original_timeout = api_server._approval_timeout_seconds
    api_server._approval_timeout_seconds = 0.05
    try:
        assert state["agent"].approval_callback(
            "approval-timeout", "write_file", {"path": "x"}
        ) is False
    finally:
        api_server._approval_timeout_seconds = original_timeout

    interrupted_approval = []
    interrupted_thread = threading.Thread(
        target=lambda: interrupted_approval.append(
            state["agent"].approval_callback("approval-interrupt", "write_file", {"path": "x"})
        )
    )
    interrupted_thread.start()
    wait_for(lambda: "approval-interrupt" in api_server._pending_approvals)

    assert client.post("/api/chat/interrupt", json={"session_id": sid_b}).status_code == 409
    assert not state["agent"]._interrupt_flag.is_set()
    assert client.post("/api/chat/interrupt", json={
        "session_id": sid_a,
        "run_id": run_id,
    }).status_code == 200
    assert state["agent"]._interrupt_flag.is_set()
    interrupted_thread.join(timeout=1)
    assert interrupted_approval == [False]

    state["agent"].event_callback({"type": "error", "message": "owned error"})
    state["agent"].release.set()
    wait_for(lambda: api_server._running_ctx is None)
    events = [payload for name, payload in state["emitted"] if name == "agent_event"]
    selected = [event for event in events if event["type"] in {
        "tool_approval_request", "error", "interrupted"
    }]
    assert {event["type"] for event in selected} == {
        "tool_approval_request", "error", "interrupted"
    }
    assert all(event["session_id"] == sid_a for event in selected)
    assert all(event["run_id"] == run_id for event in selected)
    assert [event["event_seq"] for event in events] == sorted(event["event_seq"] for event in events)


def test_status_and_session_list_expose_active_and_running_ownership(isolated_server):
    state = isolated_server
    client = api_server.app.test_client()
    sid_a = state["first"]["id"]
    client.post("/api/chat", json={"session_id": sid_a, "message": "a useful title"})
    assert state["agent"].started.wait(timeout=1)
    sid_b = data(client.post("/api/sessions/new", json={}))["session_id"]

    status = data(client.get("/api/status"))
    assert status["session_id"] == sid_b
    assert status["running_session_id"] == sid_a
    assert status["running_run_id"]
    sessions = data(client.get("/api/sessions"))["sessions"]
    by_id = {item["id"]: item for item in sessions}
    assert by_id[sid_a]["running"] is True
    assert by_id[sid_a]["title"] == "a useful title"
    assert by_id[sid_b]["running"] is False

    state["agent"].release.set()
    wait_for(lambda: api_server._running_ctx is None)
    status = data(client.get("/api/status"))
    assert status["running_session_id"] is None
    assert status["running_run_id"] is None


def test_workspace_stores_are_separate_and_reloadable_after_restart(
    isolated_server, tmp_path, monkeypatch
):
    state = isolated_server
    client = api_server.app.test_client()
    workspace_a = state["workspace"].resolve()
    workspace_b = (tmp_path / "workspace-b").resolve()
    workspace_b.mkdir()

    def fake_initialize(workdir=None, session_id=None):
        if workdir is None:
            return
        key = str(workspace_b if str(Path(workdir).resolve()) == str(workspace_b) else workspace_a)
        store = api_server._store_for_workdir(key)
        record = store.get(session_id) if session_id else None
        if record is None:
            record = store.create(workdir=key)
        api_server.agent_config["workdir"] = key
        api_server._session_store = store
        api_server._active_store_key = key
        api_server._active_session_id = record["id"]
        api_server.conversation_history = list(record.get("history", []))

    monkeypatch.setattr(api_server, "ensure_agent_initialized", fake_initialize)

    created_b = client.post("/api/sessions/new", json={"workdir": str(workspace_b)})
    assert created_b.status_code == 200
    sid_b = data(created_b)["session_id"]
    store_b = api_server._session_stores[str(workspace_b)]
    record_b = store_b.get(sid_b)
    record_b["history"] = [{"role": "user", "content": "persisted in B"}]
    record_b["title"] = "B session"
    store_b.save(record_b)

    sid_a = state["first"]["id"]
    assert client.post(f"/api/sessions/{sid_a}/load").status_code == 200
    assert api_server.agent_config["workdir"] == str(workspace_a)
    loaded_b = data(client.post(f"/api/sessions/{sid_b}/load"))
    assert loaded_b["history"] == [{"role": "user", "content": "persisted in B"}]
    assert api_server.agent_config["workdir"] == str(workspace_b)

    assert (workspace_a / ".agent" / "sessions" / f"{sid_a}.json").exists()
    assert (workspace_b / ".agent" / "sessions" / f"{sid_b}.json").exists()
    assert not (workspace_a / ".agent" / "sessions" / f"{sid_b}.json").exists()

    api_server._session_stores = {}
    api_server._session_store = None
    api_server._active_store_key = None
    api_server._active_session_id = None
    api_server.conversation_history = []
    api_server.agent_config["workdir"] = str(workspace_b)

    sessions_b = data(client.get(f"/api/sessions?workdir={workspace_b}"))["sessions"]
    assert {item["id"] for item in sessions_b} == {sid_b}
    restored = data(client.post(f"/api/sessions/{sid_b}/load"))
    assert restored["title"] == "B session"
    assert restored["history"] == [{"role": "user", "content": "persisted in B"}]
    sessions_a = data(client.get(f"/api/sessions?workdir={workspace_a}"))["sessions"]
    assert {item["id"] for item in sessions_a} == {sid_a}
