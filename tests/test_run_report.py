import json
import subprocess
from types import SimpleNamespace

import api_server
from api import callbacks, run_lifecycle
from components.run_report import RunReportRecorder
from components.session_store import SessionStore


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def init_repo(path):
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    (path / "removed.txt").write_text("remove me\n", encoding="utf-8")
    git(path, "add", "tracked.txt", "removed.txt")
    git(path, "commit", "-m", "baseline")


def test_git_report_excludes_dirty_baseline_and_filters_sensitive_untracked(tmp_path):
    init_repo(tmp_path)
    base_sha = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "tracked.txt").write_text("dirty before run\n", encoding="utf-8")
    (tmp_path / "existing.txt").write_text("preexisting\n", encoding="utf-8")
    (tmp_path / "delete-untracked.txt").write_text("delete during run\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-collect\n", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"secret":"do-not-collect"}', encoding="utf-8")

    recorder = RunReportRecorder(str(tmp_path), "run-git", "session-git")

    (tmp_path / "tracked.txt").write_text(
        "dirty before run\nchanged during run\n", encoding="utf-8"
    )
    (tmp_path / "removed.txt").unlink()
    (tmp_path / "delete-untracked.txt").unlink()
    (tmp_path / "new.txt").write_text("created during run\n", encoding="utf-8")
    report = recorder.finalize("completed")

    by_path = {item["path"]: item["status"] for item in report["files"]}
    assert report["base_sha"] == base_sha, report["warnings"]
    assert report["outcome"] == "completed"
    assert by_path == {
        "delete-untracked.txt": "D",
        "new.txt": "??",
        "removed.txt": "D",
        "tracked.txt": "M",
    }
    assert "+changed during run" in report["diff"]
    assert "+dirty before run" not in report["diff"]
    assert "created during run" in report["diff"]
    assert "preexisting" not in report["diff"]
    assert "do-not-collect" not in json.dumps(report)
    assert ".env" not in by_path
    assert "credentials.json" not in by_path
    assert any("deleted untracked" in warning for warning in report["warnings"])


def test_command_evidence_uses_structured_result_and_validation_exit_status(tmp_path):
    init_repo(tmp_path)
    recorder = RunReportRecorder(str(tmp_path), "run-command", "session-command")
    recorder.record_event({
        "type": "tool_start",
        "name": "bash",
        "tool_use_id": "tool-1",
        "purpose": "verification",
        "input": {"command": "pytest -q"},
        "ts": 10.0,
    })
    recorder.record_event({
        "type": "tool_result",
        "name": "bash",
        "tool_use_id": "tool-1",
        "output": "the model-visible string is not evidence",
        "exit_code": 3,
        "duration": 0.25,
        "timed_out": False,
        "cancelled": False,
        "stdout": "one failed\n",
        "stderr": "failure detail\n",
    })
    recorder.record_event({
        "type": "tool_start",
        "name": "read_file",
        "tool_use_id": "tool-2",
        "input": {"path": "tracked.txt"},
    })
    recorder.record_event({"type": "usage", "input_tokens": 11, "output_tokens": 7})
    recorder.record_event({"type": "usage", "input_tokens": 5, "output_tokens": 2})

    report = recorder.finalize("completed")

    assert report["outcome"] == "completed"
    assert report["validation"] == "failed"
    assert report["usage"] == {"input_tokens": 16, "output_tokens": 9}
    assert report["commands"] == [{
        "tool_use_id": "tool-1",
        "command": "pytest -q",
        "exit_code": 3,
        "duration": 0.25,
        "timed_out": False,
        "cancelled": False,
        "stdout": "one failed\n",
        "stderr": "failure detail\n",
    }]
    assert json.loads(recorder.report_path.read_text(encoding="utf-8")) == report
    persisted_events = [
        json.loads(line)
        for line in recorder.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["type"] for event in persisted_events] == [
        "tool_start", "tool_result", "tool_start", "usage", "usage",
    ]


def test_tracked_sensitive_files_are_not_exported(tmp_path):
    init_repo(tmp_path)
    names = (".env", "credentials.json", "private.pem", ".agent/sessions/fixture.json")
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture-before\n")
    git(tmp_path, "add", *names)
    git(tmp_path, "commit", "-m", "sensitive test fixtures")
    recorder = RunReportRecorder(str(tmp_path), "tracked-sensitive", "session")
    for name in names:
        (tmp_path / name).write_text("fixture-after-must-not-export\n")
    report = recorder.finalize("completed")
    assert report["files"] == []
    assert report["diff"] == ""
    assert "fixture-after-must-not-export" not in recorder.report_path.read_text()
    assert any("sensitive" in warning for warning in report["warnings"])


def test_unknown_validation_and_missing_command_result_are_not_reported_as_success(tmp_path):
    recorder = RunReportRecorder(str(tmp_path), "run-unknown", "session-unknown")
    recorder.record_event({
        "type": "tool_start",
        "name": "bash",
        "tool_use_id": "tool-echo",
        "input": {"command": "echo done"},
    })
    recorder.record_event({
        "type": "tool_start",
        "name": "bash",
        "tool_use_id": "tool-not-verification",
        "purpose": "agent",
        "input": {"command": "pytest -q || true"},
    })
    recorder.record_event({
        "type": "tool_result",
        "name": "bash",
        "tool_use_id": "tool-not-verification",
        "exit_code": 0,
        "timed_out": False,
        "cancelled": False,
    })

    report = recorder.finalize("completed")

    assert report["validation"] == "unknown"
    assert report["commands"][0]["exit_code"] is None
    assert report["commands"][1]["exit_code"] == 0
    assert any("no tool_result" in warning for warning in report["warnings"])
    assert report["base_sha"] is None
    assert report["diff"] == ""
    assert any("not a Git repository" in warning for warning in report["warnings"])


def test_interrupted_report_is_persisted_emitted_and_available_in_snapshot(
    tmp_path, monkeypatch
):
    init_repo(tmp_path)
    store = SessionStore(tmp_path / ".agent" / "sessions")
    record = store.create(workdir=str(tmp_path), mode="auto")
    reporter = RunReportRecorder(str(tmp_path), "run-interrupted", record["id"])
    emitted = []
    context = api_server.RunContext(
        session_id=record["id"],
        run_id="run-interrupted",
        history=[{"role": "user", "content": "stop"}],
        baseline_history=[{"role": "user", "content": "stop"}],
        agent=SimpleNamespace(),
        mode="auto",
        workdir=str(tmp_path),
        title="stop",
        store=store,
        baseline_length=1,
        run_reporter=reporter,
    )
    monkeypatch.setattr(api_server, "_session_store", store)
    monkeypatch.setattr(api_server, "_session_stores", {str(tmp_path.resolve()): store})
    monkeypatch.setattr(api_server, "_active_session_id", record["id"])
    monkeypatch.setattr(api_server, "_active_store_key", str(tmp_path.resolve()))
    monkeypatch.setattr(api_server, "_running_ctx", context)
    monkeypatch.setattr(api_server, "_is_running", True)
    monkeypatch.setattr(api_server.socketio, "emit", lambda name, event: emitted.append((name, event)))

    callbacks._push_run_event(context, {"type": "interrupted", "ts": 20.0})
    callbacks._push_run_event(context, {
        "type": "run_end", "outcome": "interrupted", "ts": 21.0,
    })
    run_lifecycle._finish_run(context)

    snapshot = api_server._session_snapshot(record["id"])
    assert snapshot["running"] is False
    assert len(snapshot["delivery_reports"]) == 1
    report = snapshot["delivery_reports"][0]
    assert report["run_id"] == "run-interrupted"
    assert report["outcome"] == "interrupted"
    assert json.loads(reporter.report_path.read_text(encoding="utf-8")) == report
    trace = [
        json.loads(line)
        for line in reporter.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["type"] for event in trace] == [
        "interrupted", "delivery_report", "run_end",
    ]
    delivery = next(
        event for name, event in emitted
        if name == "agent_event" and event["type"] == "delivery_report"
    )
    assert delivery["report"] == report
    assert [event["event_seq"] for _, event in emitted] == [1, 2, 3]

    monkeypatch.setattr(api_server, "agent_instance", SimpleNamespace())
    response = api_server.app.test_client().delete(
        f"/api/history?session_id={record['id']}"
    )
    assert response.status_code == 200
    assert api_server._session_snapshot(record["id"])["delivery_reports"] == []
    assert reporter.report_path.exists()
    assert reporter.events_path.exists()
