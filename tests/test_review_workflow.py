from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import api_server
from api import reviews
from components.command_runner import CancellationToken
from components.review_contract import build_package, seal_package, validate_package, validate_review


def package():
    return seal_package({
        "session_id": "session", "run_id": "run", "requirement": "Fix value",
        "diff": "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-value=0\n+value=1\n",
        "snippets": [{"path": "main.py", "start_line": 1, "content": "value=1"}],
        "test_results": [{"exit_code": 0, "duration": 1.0}],
    })


def artifact(request):
    return {key: request[key] for key in (
        "schema_version", "session_id", "run_id", "invocation_id", "patch_hash", "snapshot_hash"
    )} | {"verdict": "no_findings", "findings": [], "context_limits": [],
         "usage": {"input_tokens": None, "output_tokens": None}}


def test_protobuf_data_numbers_keep_snapshot_identity():
    request = package()
    transported = deepcopy(request)
    transported["schema_version"] = 1.0
    transported["snippets"][0]["start_line"] = 1.0
    transported["test_results"][0]["exit_code"] = 0.0
    assert validate_package(transported) == request


def test_review_rejects_wrong_owner_hash_and_unsupplied_locations():
    request = package()
    wrong = artifact(request) | {"run_id": "other-run"}
    with pytest.raises(ValueError, match="does not belong"):
        validate_review(wrong, request)
    finding = {"severity": "high", "file": "other.py", "line": 100,
               "reason": "error", "evidence": "not supplied", "suggestion": "fix"}
    with pytest.raises(ValueError, match="outside"):
        validate_review(artifact(request) | {"verdict": "changes_requested", "findings": [finding]}, request)
    changed = deepcopy(request)
    changed["test_results"][0]["exit_code"] = 1
    with pytest.raises(ValueError, match="snapshot"):
        validate_package(changed)


def test_build_package_excludes_host_paths_and_sensitive_symlink_targets(tmp_path):
    (tmp_path / "main.py").write_text("value=1\n")
    (tmp_path / "alias.py").symlink_to(tmp_path / "main.py")
    report = {"session_id": "s", "run_id": "r", "diff": "patch", "commands": [],
              "files": [{"path": "main.py"}, {"path": ".env"}, {"path": "alias.py"}]}
    request = build_package(report, [{"role": "user", "content": "fix this"}], str(tmp_path))
    assert [item["path"] for item in request["snippets"]] == ["main.py"]
    assert str(tmp_path) not in json.dumps(request)


def test_stale_review_cannot_mark_delivery_complete():
    request = package()
    saved = []
    context = SimpleNamespace(
        review_result={"status": "completed", "artifact": artifact(request)},
        review_package=request, run_reporter=SimpleNamespace(save=saved.append),
    )
    report = reviews.attach_review(context, {"diff": "a newer patch", "outcome": "completed"})
    assert report["review"]["status"] == "stale"
    assert report["outcome"] == "incomplete"
    assert saved == [report]


@pytest.mark.parametrize("status,outcome", [("cancelled", "interrupted"), ("cancel_unknown", "incomplete"), ("unknown", "incomplete")])
def test_unconfirmed_cancel_does_not_claim_interrupted(status, outcome):
    context = SimpleNamespace(
        review_result={"status": status}, review_package=None,
        run_reporter=SimpleNamespace(save=lambda _: None),
    )
    assert reviews.attach_review(context, {"diff": "", "outcome": "completed"})["outcome"] == outcome


def test_configured_review_is_bound_to_original_run_and_persisted(tmp_path, monkeypatch):
    request = package()
    report = {"session_id": "session", "run_id": "run", "diff": request["diff"],
              "files": [{"path": "main.py"}], "commands": [], "outcome": "completed"}
    (tmp_path / "main.py").write_text("value=1")
    recorder = SimpleNamespace(directory=tmp_path / "run", repo_root=tmp_path,
                               finalize=lambda _: report)
    context = SimpleNamespace(
        review_config={"url": "http://localhost:8100", "token": "", "timeout": "3"},
        pending_run_end={"outcome": "completed"}, run_reporter=recorder,
        review_token=CancellationToken(), phase="running", session_id="session", run_id="run",
        workdir=str(tmp_path), baseline_history=[{"role": "user", "content": "fix"}],
    )
    events = []
    calls = []

    def review(payload, record_path, **options):
        calls.append((payload, record_path))
        options["on_update"]({"status": "working"})
        return {"status": "completed", "artifact": artifact(payload)}

    monkeypatch.setattr(reviews, "_client", lambda _: SimpleNamespace(review=review))
    monkeypatch.setattr(reviews.callbacks, "_push_run_event", lambda owner, event: events.append((owner, event)))
    reviews.run_review(context, False)
    assert calls[0][0]["session_id"] == "session"
    assert calls[0][0]["run_id"] == "run"
    assert calls[0][1].name == "a2a-review.json"
    assert all(owner is context for owner, _ in events)
    assert json.loads((recorder.directory / "review-package.json").read_text())["run_id"] == "run"
    assert context.phase == "finishing"


def test_plan_does_not_submit_review():
    from api.run_lifecycle import interrupt_owned_run
    context = SimpleNamespace(review_config={"url": "http://localhost:8100"}, pending_run_end={"outcome": "completed"}, phase="running")
    reviews.run_review(context, True)
    assert context.phase == "finishing"
    assert interrupt_owned_run(context) is False


def test_cancelled_handoff_without_diff_remains_interrupted(tmp_path, monkeypatch):
    token = CancellationToken()
    token.cancel()
    context = SimpleNamespace(
        review_config={"url": "http://localhost:8100"},
        pending_run_end={"outcome": "completed"}, review_token=token, review_package=None,
        run_reporter=SimpleNamespace(directory=tmp_path, finalize=lambda _: {"diff": ""}, save=lambda _: None),
    )
    monkeypatch.setattr(reviews, "_client", lambda _: pytest.fail("Cancelled handoff must not contact A2A"))
    reviews.run_review(context, False)
    report = reviews.attach_review(context, {"diff": "", "outcome": "completed"})
    assert report["review"]["status"] == "cancelled"
    assert report["outcome"] == "interrupted"


def test_interruption_during_review_uses_owned_review_token(monkeypatch):
    token = CancellationToken()
    context = SimpleNamespace(session_id="s", run_id="r", phase="reviewing", review_token=token)
    monkeypatch.setattr(api_server, "_running_ctx", context)
    monkeypatch.setattr(reviews.callbacks, "_push_run_event", lambda *_: None)
    import api.routes.chat as chat
    monkeypatch.setattr(chat, "_require_agent", lambda: None)
    client = api_server.app.test_client()
    rejected = client.post("/api/chat/interrupt", json={"session_id": "other", "run_id": "r"})
    assert rejected.status_code == 409
    assert not token.is_cancelled
    accepted = client.post("/api/chat/interrupt", json={"session_id": "s", "run_id": "r"})
    assert accepted.status_code == 200
    assert token.is_cancelled


def test_socket_interrupt_during_review_uses_same_cancellation(monkeypatch):
    token = CancellationToken()
    context = SimpleNamespace(session_id="s", run_id="r", phase="reviewing", review_token=token, event_seq=3)
    monkeypatch.setattr(api_server, "_running_ctx", context)
    monkeypatch.setattr(reviews.callbacks, "_push_run_event", lambda *_: None)
    client = api_server.socketio.test_client(api_server.app)
    try:
        client.emit("interrupt", {"session_id": "other", "run_id": "r"})
        assert not token.is_cancelled
        client.get_received()
        client.emit("interrupt", {"session_id": "s", "run_id": "r"})
        assert token.is_cancelled
        assert any(event["name"] == "interrupt_requested" for event in client.get_received())
    finally:
        client.disconnect()


@pytest.mark.parametrize("changed,status,outcome", [
    (False, "completed", "completed"), (True, "completed", "incomplete"),
    (False, "cancelled", "interrupted"),
])
def test_resume_updates_only_original_report_and_preserves_version(tmp_path, monkeypatch, changed, status, outcome):
    request = package()
    directory = tmp_path / ".agent" / "runs" / "run"
    (tmp_path / "main.py").write_text("value=1")
    reviews._write(directory / "review-package.json", request)
    reviews._write(directory / "review-local.json", {
        "source_root": str(tmp_path), "delivery_outcome": "completed",
        "file_hashes": reviews._fingerprints(str(tmp_path), [{"path": "main.py"}]),
    })
    report = {"session_id": "session", "run_id": "run", "outcome": "incomplete"}
    newer = {"session_id": "session", "run_id": "newer", "outcome": "completed"}
    record = {"workdir": str(tmp_path), "delivery_reports": [report, newer]}
    saved = []
    store = SimpleNamespace(get=lambda _: deepcopy(record), save=saved.append)
    monkeypatch.setattr(reviews, "_resolve_session", lambda sid: (record, store) if sid == "session" else (None, store))
    monkeypatch.setattr(api_server, "_running_ctx", None)
    monkeypatch.setattr(api_server, "_cfg", SimpleNamespace(a2a_review_url="http://localhost:8100"))
    calls = []

    def review(payload, path, **options):
        calls.append((payload, path))
        return {"status": status, "artifact": artifact(payload)}

    monkeypatch.setattr(reviews, "_client", lambda _: SimpleNamespace(review=review))
    if changed:
        (tmp_path / "main.py").write_text("value=2")
    client = api_server.app.test_client()
    assert client.post("/api/reviews/resume", json={"session_id": "other", "run_id": "run"}).status_code == 404
    assert not calls
    response = client.post("/api/reviews/resume", json={"session_id": "session", "run_id": "run"})
    assert response.status_code == 200
    result = response.get_json()["data"]
    assert result["outcome"] == outcome
    assert result["review"]["status"] == ("stale" if changed else status)
    assert calls[0][0] == request
    assert calls[0][1] == directory / "a2a-review.json"
    assert saved[0]["delivery_reports"] == [result, newer]


def test_resume_holds_run_admission_until_query_finishes(monkeypatch):
    import api.run_lifecycle as lifecycle
    monkeypatch.setattr(api_server, "_running_ctx", None)
    monkeypatch.setattr(lifecycle, "_resolve_session", lambda _: ({"id": "s"}, None))
    monkeypatch.setattr(reviews, "_resuming", {("s", "r"): CancellationToken()})
    result, error, status = lifecycle._start_run("s", "edit file", "auto", False)
    assert result is None
    assert status == 409
    assert "恢复审查" in error
