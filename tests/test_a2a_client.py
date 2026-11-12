import json

import httpx
import pytest

pytest.importorskip("a2a")
from a2a import types
from google.protobuf import json_format, struct_pb2

from components import a2a_review as review_client
from components.command_runner import CancellationToken
from components.review_contract import seal_package


BASE_URL = "http://127.0.0.1"


def _package():
    return seal_package({
        "session_id": "session-1",
        "run_id": "run-1",
        "requirement": "review the patch",
        "diff": "+value = 1\n",
        "snippets": [{"path": "app.py", "start_line": 1, "content": "value = 1"}],
        "test_results": [{"command": "pytest", "exit_code": 0}],
        "context_limits": ["bounded input"],
    })


def _artifact(package):
    return {
        "schema_version": 1,
        **{key: package[key] for key in (
            "session_id", "run_id", "invocation_id", "patch_hash", "snapshot_hash",
        )},
        "verdict": "no_findings",
        "findings": [],
        "context_limits": [],
        "usage": {"input_tokens": 21, "output_tokens": 4},
    }


def _card(*, endpoint=BASE_URL + "/", idempotent=True, bearer=False):
    tags = ["review"]
    if idempotent:
        tags.append("idempotent_review=true")
    kwargs = {}
    if bearer:
        kwargs.update(
            security_schemes={
                "bearer": types.SecurityScheme(
                    http_auth_security_scheme=types.HTTPAuthSecurityScheme(scheme="bearer")
                )
            },
            security_requirements=[types.SecurityRequirement(schemes={"bearer": {}})],
        )
    card = types.AgentCard(
        name="Review agent",
        description="Reviews a bounded patch",
        version="1",
        supported_interfaces=[types.AgentInterface(
            url=endpoint, protocol_binding="JSONRPC", protocol_version="1.0",
        )],
        capabilities=types.AgentCapabilities(),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[types.AgentSkill(
            id="review_patch", name="Review patch", description="Review a patch", tags=tags,
        )],
        **kwargs,
    )
    return json_format.MessageToDict(card)


def _task(state, package, *, task_id="task-1", include_artifact=False):
    artifacts = []
    if include_artifact:
        value = json_format.ParseDict(_artifact(package), struct_pb2.Value())
        artifacts.append(types.Artifact(
            artifact_id="review-1", name="review.json", parts=[types.Part(data=value)],
        ))
    task = types.Task(
        id=task_id,
        context_id="context-opaque",
        status=types.TaskStatus(state=state),
        artifacts=artifacts,
    )
    return json_format.MessageToDict(task)


def _rpc(request, result):
    payload = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})


def _install_transport(monkeypatch, handler):
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(review_client.httpx, "AsyncClient", factory)


def test_real_sdk_datapart_round_trip_and_completed_artifact(monkeypatch, tmp_path):
    package = _package()
    observed = {}
    record_path = tmp_path / "a2a-review.json"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        payload = json.loads(request.content)
        assert payload["method"] == "SendMessage"
        intent = json.loads(record_path.read_text())
        assert intent["submit_intent"] is True
        assert intent["submit_attempts"] == 1
        assert intent["message_id"] == package["invocation_id"]
        observed.update(payload["params"]["message"])
        return _rpc(request, {"task": _task(
            types.TaskState.TASK_STATE_COMPLETED, package, include_artifact=True,
        )})

    _install_transport(monkeypatch, handler)
    updates = []
    result = review_client.A2AReviewClient(BASE_URL).review(
        package, record_path, on_update=updates.append,
    )

    assert result["status"] == "completed"
    assert result["artifact"]["schema_version"] == 1
    assert result["usage"] == {"input_tokens": 21, "output_tokens": 4}
    assert observed["messageId"] == package["invocation_id"]
    # Protobuf Value uses doubles; contract normalization must restore strict ints.
    assert observed["parts"][0]["data"]["schema_version"] == 1.0
    assert observed["parts"][0]["data"]["snippets"][0]["start_line"] == 1.0
    saved = json.loads(record_path.read_text())
    assert saved["submit_intent"] is True
    assert saved["submit_attempts"] == 1
    assert saved["task_id"] == "task-1"
    assert updates[-1]["status"] == "completed"


def test_ack_loss_retries_once_with_same_message_id_when_card_declares_idempotency(
    monkeypatch, tmp_path,
):
    package = _package()
    message_ids = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card(idempotent=True))
        payload = json.loads(request.content)
        assert payload["method"] == "SendMessage"
        message_ids.append(payload["params"]["message"]["messageId"])
        if len(message_ids) == 1:
            raise httpx.ReadTimeout("ack lost", request=request)
        return _rpc(request, {"task": _task(
            types.TaskState.TASK_STATE_COMPLETED, package, include_artifact=True,
        )})

    _install_transport(monkeypatch, handler)
    record_path = tmp_path / "a2a-review.json"
    result = review_client.A2AReviewClient(BASE_URL).review(package, record_path)

    assert result["status"] == "completed"
    assert message_ids == [package["invocation_id"], package["invocation_id"]]
    assert json.loads(record_path.read_text())["submit_attempts"] == 2


def test_ack_loss_stays_unknown_without_idempotency_declaration(monkeypatch, tmp_path):
    package = _package()
    sends = 0

    def handler(request):
        nonlocal sends
        if request.method == "GET":
            return httpx.Response(200, json=_card(idempotent=False))
        sends += 1
        raise httpx.ReadTimeout("ack lost", request=request)

    _install_transport(monkeypatch, handler)
    result = review_client.A2AReviewClient(BASE_URL).review(
        package, tmp_path / "a2a-review.json",
    )

    assert result["status"] == "unknown"
    assert sends == 1


def test_explicit_send_rejection_is_failed_and_never_resubmitted(monkeypatch, tmp_path):
    package = _package()
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        posts += 1
        return httpx.Response(400, json={"error": "invalid"})

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    client = review_client.A2AReviewClient(BASE_URL)
    first = client.review(package, path)
    second = client.review(package, path)

    assert first["status"] == second["status"] == "failed"
    assert posts == 1


def test_resume_queries_saved_task_without_sending_again(monkeypatch, tmp_path):
    package = _package()
    sends = 0
    gets = 0

    def handler(request):
        nonlocal sends, gets
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        payload = json.loads(request.content)
        if payload["method"] == "SendMessage":
            sends += 1
            return _rpc(request, {"task": _task(types.TaskState.TASK_STATE_SUBMITTED, package)})
        assert payload["method"] == "GetTask"
        gets += 1
        if gets == 1:
            raise httpx.ConnectError("offline", request=request)
        return _rpc(request, _task(
            types.TaskState.TASK_STATE_COMPLETED, package, include_artifact=True,
        ))

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    client = review_client.A2AReviewClient(BASE_URL)
    first = client.review(package, path)
    second = client.review(package, path)

    assert first["status"] == "unknown"
    assert first["task_id"] == "task-1"
    assert second["status"] == "completed"
    assert sends == 1
    assert gets == 2


def test_cancellation_calls_remote_and_only_marks_confirmed_canceled(monkeypatch, tmp_path):
    package = _package()
    token = CancellationToken()
    methods = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        payload = json.loads(request.content)
        methods.append(payload["method"])
        if payload["method"] == "SendMessage":
            return _rpc(request, {"task": _task(types.TaskState.TASK_STATE_WORKING, package)})
        if payload["method"] == "CancelTask":
            return _rpc(request, _task(types.TaskState.TASK_STATE_CANCELED, package))
        raise AssertionError(payload["method"])

    def update(value):
        if value["task_id"]:
            token.cancel()

    _install_transport(monkeypatch, handler)
    result = review_client.A2AReviewClient(BASE_URL).review(
        package, tmp_path / "a2a-review.json", token, update,
    )

    assert result["status"] == "cancelled"
    assert methods == ["SendMessage", "CancelTask"]


def test_pre_cancel_is_confirmed_locally_without_http_or_submission(monkeypatch, tmp_path):
    package = _package()
    token = CancellationToken()
    token.cancel()
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        raise AssertionError("pre-cancelled review must not perform HTTP")

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    result = review_client.A2AReviewClient(BASE_URL).review(package, path, token)

    assert result["status"] == "cancelled"
    assert result["error"] is None
    assert requests == 0
    assert json.loads(path.read_text())["submit_attempts"] == 0


def test_cancel_during_card_discovery_is_cancelled_before_send(monkeypatch, tmp_path):
    package = _package()
    token = CancellationToken()
    methods = []

    def handler(request):
        methods.append(request.method)
        assert request.method == "GET"
        token.cancel()
        return httpx.Response(200, json=_card())

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    result = review_client.A2AReviewClient(BASE_URL).review(package, path, token)

    assert result["status"] == "cancelled"
    assert methods == ["GET"]
    assert json.loads(path.read_text())["submit_attempts"] == 0


def test_pre_cancel_after_unknown_send_attempt_remains_cancel_unknown(monkeypatch, tmp_path):
    package = _package()
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        if request.method == "GET":
            return httpx.Response(200, json=_card(idempotent=False))
        raise httpx.ReadTimeout("ack lost", request=request)

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    client = review_client.A2AReviewClient(BASE_URL)
    assert client.review(package, path)["status"] == "unknown"
    before_resume = requests
    token = CancellationToken()
    token.cancel()

    result = client.review(package, path, token)

    assert result["status"] == "cancel_unknown"
    assert requests == before_resume
    assert json.loads(path.read_text())["submit_attempts"] == 1


def test_unconfirmed_cancellation_is_cancel_unknown(monkeypatch, tmp_path):
    package = _package()
    token = CancellationToken()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        payload = json.loads(request.content)
        if payload["method"] == "SendMessage":
            return _rpc(request, {"task": _task(types.TaskState.TASK_STATE_WORKING, package)})
        return _rpc(request, _task(types.TaskState.TASK_STATE_WORKING, package))

    def update(value):
        if value["task_id"]:
            token.cancel()

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(review_client, "_CANCEL_BUDGET", 0.05)
    result = review_client.A2AReviewClient(BASE_URL).review(
        package, tmp_path / "a2a-review.json", token, update,
    )

    assert result["status"] == "cancel_unknown"


def test_timeout_preserves_known_task_as_unknown(monkeypatch, tmp_path):
    package = _package()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        return _rpc(request, {"task": _task(types.TaskState.TASK_STATE_WORKING, package)})

    _install_transport(monkeypatch, handler)
    result = review_client.A2AReviewClient(BASE_URL, timeout=0.05).review(
        package, tmp_path / "a2a-review.json",
    )

    assert result["status"] == "unknown"
    assert result["task_id"] == "task-1"


def test_rejects_cross_origin_card_before_bearer_can_reach_rpc(monkeypatch, tmp_path):
    package = _package()
    requests = []

    def handler(request):
        requests.append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json=_card(
            endpoint="http://evil.test/rpc", bearer=True,
        ))

    _install_transport(monkeypatch, handler)
    result = review_client.A2AReviewClient(BASE_URL, token="top-secret").review(
        package, tmp_path / "a2a-review.json",
    )

    assert result["status"] == "failed"
    assert requests == [("GET", BASE_URL + "/.well-known/agent-card.json", "Bearer top-secret")]
    assert "top-secret" not in json.dumps(result)


@pytest.mark.parametrize("url", [
    "ftp://review.test", "http://user:pw@127.0.0.1", "http://127.0.0.1?q=1",
    "http://127.0.0.1/#fragment", "http://review.test",
])
def test_rejects_unsafe_configured_urls(url):
    with pytest.raises(ValueError):
        review_client.A2AReviewClient(url)


@pytest.mark.parametrize("url", ["http://localhost:8080", "http://127.0.0.2", "http://[::1]:8080"])
def test_allows_http_only_for_loopback(url):
    assert review_client.A2AReviewClient(url).url == url


def test_record_owner_and_agent_are_immutable(monkeypatch, tmp_path):
    package = _package()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        return _rpc(request, {"task": _task(
            types.TaskState.TASK_STATE_COMPLETED, package, include_artifact=True,
        )})

    _install_transport(monkeypatch, handler)
    path = tmp_path / "a2a-review.json"
    review_client.A2AReviewClient(BASE_URL).review(package, path)
    changed = seal_package({
        "session_id": package["session_id"],
        "run_id": "another-run",
        "requirement": package["requirement"],
        "diff": package["diff"],
        "snippets": package["snippets"],
        "test_results": package["test_results"],
        "context_limits": package["context_limits"],
    })
    with pytest.raises(ValueError, match="another run"):
        review_client.A2AReviewClient(BASE_URL).review(changed, path)
    with pytest.raises(ValueError, match="another run"):
        review_client.A2AReviewClient("https://other.test").review(package, path)
