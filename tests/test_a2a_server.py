import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("a2a")
from a2a import types

from a2a_review_server import SQLiteTaskStore, build_app
from components.review_contract import seal_package


def _package(**updates):
    data = {
        "session_id": "session-1",
        "run_id": "run-1",
        "requirement": "Review the change",
        "diff": "",
        "snippets": [],
        "test_results": [],
        "context_limits": ["Only supplied evidence was available."],
    }
    data.update(updates)
    return seal_package(data)


def _artifact(package, *, limits=None):
    return {
        "schema_version": 1,
        "session_id": package["session_id"],
        "run_id": package["run_id"],
        "invocation_id": package["invocation_id"],
        "patch_hash": package["patch_hash"],
        "snapshot_hash": package["snapshot_hash"],
        "verdict": "no_findings",
        "findings": [],
        "context_limits": limits or [],
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


def _send(package, *, immediate=False, request_id=1, message_id="message-1"):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": [{"data": package}],
            },
            "configuration": {"returnImmediately": immediate},
        },
    }


def _rpc(request_id, method, params):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _run(coro):
    return asyncio.run(coro)


def test_remote_cli_requires_https_public_url(monkeypatch, tmp_path):
    import sys
    import a2a_review_server as server
    monkeypatch.setenv("A2A_REVIEW_TOKEN", "local-test-token")
    monkeypatch.setattr(sys, "argv", ["review", "--host", "0.0.0.0", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit, match="https"):
        server.main()


@pytest.fixture
def mock_async_anthropic(monkeypatch):
    """Install an in-process Anthropic client double; no model is contacted."""
    def install(*, response=None, create=None):
        instances = []

        class FakeAsyncAnthropic:
            def __init__(self, **client_args):
                self.client_args = client_args
                self.calls = []
                self.exited = False
                self.messages = SimpleNamespace(create=self._create)
                instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                del exc_type, exc, traceback
                self.exited = True

            async def _create(self, **request):
                self.calls.append(request)
                if create is not None:
                    return await create(**request)
                return response

        monkeypatch.setattr("anthropic.AsyncAnthropic", FakeAsyncAnthropic)
        return instances

    return install


def test_official_jsonrpc_routes_card_artifact_and_idempotency(tmp_path):
    calls = 0

    async def scenario():
        nonlocal calls

        async def reviewer(package):
            nonlocal calls
            calls += 1
            return _artifact(package)

        app = build_app("http://review.test:8100", tmp_path, reviewer=reviewer, token="")
        package = _package()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://review.test:8100",
            headers={"A2A-Version": "1.0"},
        ) as client:
            card = (await client.get("/.well-known/agent-card.json")).json()
            assert card["supportedInterfaces"] == [
                {
                    "url": "http://review.test:8100/",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ]
            assert "idempotent_review=true" in card["skills"][0]["tags"]

            first = (await client.post("/", json=_send(package))).json()["result"]["task"]
            second = (
                await client.post("/", json=_send(package, request_id=2, message_id="message-2"))
            ).json()["result"]["task"]
            fetched = (
                await client.post("/", json=_rpc(3, "GetTask", {"id": first["id"]}))
            ).json()["result"]

        assert first["id"] == second["id"] == fetched["id"]
        assert first["status"]["state"] == "TASK_STATE_COMPLETED"
        assert calls == 1
        artifact = first["artifacts"][0]
        assert artifact["name"] == "review.json"
        assert artifact["parts"][0]["filename"] == "review.json"
        assert artifact["parts"][0]["data"]["invocation_id"] == package["invocation_id"]
        await app.state.review_handler.close()

    _run(scenario())


def test_invalid_package_and_conflicting_invocation_are_rejected(tmp_path, monkeypatch):
    calls = 0

    async def scenario():
        nonlocal calls

        async def reviewer(package):
            nonlocal calls
            calls += 1
            return _artifact(package)

        app = build_app("http://review.test", tmp_path, reviewer=reviewer, token="")
        package = _package()
        bad = copy.deepcopy(package)
        bad["requirement"] = "tampered after sealing"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            invalid = (await client.post("/", json=_send(bad))).json()
            assert invalid["error"]["code"] == -32602
            assert calls == 0

            original = (await client.post("/", json=_send(package, request_id=2))).json()
            assert original["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

            import components.review_contract as contract

            monkeypatch.setattr(contract, "validate_package", lambda value: value)
            conflict = (await client.post("/", json=_send(bad, request_id=3))).json()
            assert conflict["error"]["code"] == -32602
            assert "different review material" in conflict["error"]["message"]
            assert calls == 1
        await app.state.review_handler.close()

    _run(scenario())


def test_reviewer_error_is_a_failed_task_without_artifact(tmp_path):
    async def scenario():
        async def reviewer(package):
            del package
            raise RuntimeError("model endpoint unavailable")

        app = build_app("http://review.test", tmp_path, reviewer=reviewer, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            task = (await client.post("/", json=_send(_package()))).json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "model endpoint unavailable" in task["status"]["message"]["parts"][0]["text"]
        assert "artifacts" not in task
        await app.state.review_handler.close()

    _run(scenario())


def test_cancel_waits_for_that_worker_and_persists_canceled(tmp_path):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def scenario():
        async def reviewer(package):
            del package
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        app = build_app("http://review.test", tmp_path, reviewer=reviewer, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            package = _package()
            submitted = (
                await client.post("/", json=_send(package, immediate=True))
            ).json()["result"]["task"]
            await asyncio.wait_for(started.wait(), timeout=1)
            waiting_send = asyncio.create_task(
                client.post(
                    "/",
                    json=_send(package, request_id=4, message_id="duplicate-waiter"),
                )
            )
            await asyncio.sleep(0)
            canceled = (
                await client.post(
                    "/", json=_rpc(2, "CancelTask", {"id": submitted["id"]})
                )
            ).json()["result"]
            waited = (await waiting_send).json()["result"]["task"]
            fetched = (
                await client.post("/", json=_rpc(3, "GetTask", {"id": submitted["id"]}))
            ).json()["result"]
        assert stopped.is_set()
        assert canceled["status"]["state"] == "TASK_STATE_CANCELED"
        assert waited["status"]["state"] == "TASK_STATE_CANCELED"
        assert fetched["status"]["state"] == "TASK_STATE_CANCELED"
        await app.state.review_handler.close()

    _run(scenario())


def test_terminal_tasks_survive_restart_and_incomplete_tasks_fail(tmp_path):
    async def scenario():
        async def reviewer(package):
            return _artifact(package)

        package = _package()
        first_app = build_app("http://review.test", tmp_path, reviewer=reviewer, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            completed = (await client.post("/", json=_send(package))).json()["result"]["task"]
        await first_app.state.review_handler.close()

        second_app = build_app("http://review.test", tmp_path, demo=True, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            persisted = (
                await client.post("/", json=_rpc(2, "GetTask", {"id": completed["id"]}))
            ).json()["result"]
        assert persisted["status"]["state"] == "TASK_STATE_COMPLETED"
        await second_app.state.review_handler.close()

        pending_dir = tmp_path / "pending"
        store = SQLiteTaskStore(pending_dir)
        pending_package = _package(session_id="session-pending", run_id="run-pending")
        pending = types.Task(id="pending-task", context_id="session-pending")
        pending.status.state = types.TaskState.TASK_STATE_WORKING
        store.reserve(pending_package, pending)
        store.close()

        recovered = SQLiteTaskStore(pending_dir)
        recovered_task = await recovered.get("pending-task", None)
        assert recovered_task.status.state == types.TaskState.TASK_STATE_FAILED
        assert "service restart" in recovered_task.status.message.parts[0].text
        recovered.close()

    _run(scenario())


def test_bearer_auth_is_declared_and_enforced_but_card_is_public(tmp_path):
    async def scenario():
        app = build_app("http://review.test", tmp_path, demo=True, token="secret")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://review.test"
        ) as client:
            card_response = await client.get("/.well-known/agent-card.json")
            denied = await client.post(
                "/",
                headers={"A2A-Version": "1.0"},
                json=_send(_package()),
            )
            allowed = await client.post(
                "/",
                headers={"A2A-Version": "1.0", "Authorization": "Bearer secret"},
                json=_send(_package(), request_id=2),
            )
        card = card_response.json()
        assert "bearerAuth" in card["securitySchemes"]
        assert denied.status_code == 401
        assert allowed.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
        await app.state.review_handler.close()

    _run(scenario())


def test_default_model_path_sends_bounded_evidence_without_tools_and_records_usage(
    tmp_path, monkeypatch, mock_async_anthropic
):
    model_json = json.dumps(
        {
            "verdict": "no_findings",
            "findings": [],
            "context_limits": ["Model inspected only the supplied snapshot."],
        }
    )
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=model_json)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=321, output_tokens=45),
    )
    instances = mock_async_anthropic(response=response)
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("A2A_REVIEW_PROVIDER", "")
    monkeypatch.setenv("A2A_REVIEW_MODEL", "review-model-test")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://model.test/v1")
    monkeypatch.setenv("CUSTOM_API_KEY", "model-secret")

    async def scenario():
        app = build_app(
            "http://review.test",
            tmp_path,
            max_output_tokens=123,
            timeout_seconds=7,
            token="",
        )
        package = _package(
            requirement="Preserve the lock while updating the shared cache",
            diff="@@ -1 +1 @@\n-old_cache()\n+locked_cache()",
            snippets=[
                {
                    "path": "components/cache.py",
                    "start_line": 1,
                    "content": "def locked_cache():\n    return 'safe'",
                }
            ],
            test_results=[{"command": "pytest tests/test_cache.py", "exit_code": 0}],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            task = (await client.post("/", json=_send(package))).json()["result"]["task"]
        await app.state.review_handler.close()
        return package, task

    package, task = _run(scenario())
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    artifact = task["artifacts"][0]["parts"][0]["data"]
    assert artifact["usage"] == {"input_tokens": 321.0, "output_tokens": 45.0}
    assert artifact["context_limits"] == [
        "Only supplied evidence was available.",
        "Model inspected only the supplied snapshot.",
    ]

    assert len(instances) == 1
    model_client = instances[0]
    assert model_client.exited
    assert model_client.client_args == {
        "api_key": "model-secret",
        "base_url": "http://model.test/v1",
        "timeout": 7,
        "max_retries": 0,
    }
    assert len(model_client.calls) == 1
    request = model_client.calls[0]
    assert request["model"] == "review-model-test"
    assert request["max_tokens"] == 123
    assert "tools" not in request
    assert "no tools" in request["system"]
    prompt_package = json.loads(request["messages"][0]["content"].rsplit("\n\n", 1)[1])
    assert prompt_package == package
    assert "Preserve the lock" in request["messages"][0]["content"]
    assert "locked_cache" in request["messages"][0]["content"]
    assert "pytest tests/test_cache.py" in request["messages"][0]["content"]
    assert str(tmp_path) not in request["messages"][0]["content"]


@pytest.mark.parametrize(
    ("text", "stop_reason", "expected_error"),
    [
        ('{"verdict":"no_findings"', "max_tokens", "output was truncated"),
        ("not JSON", "end_turn", "JSONDecodeError"),
    ],
    ids=["truncated", "invalid-json"],
)
def test_default_model_path_rejects_unusable_output_without_artifact(
    tmp_path, mock_async_anthropic, text, stop_reason, expected_error
):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )
    mock_async_anthropic(response=response)

    async def scenario():
        app = build_app("http://review.test", tmp_path, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            task = (await client.post("/", json=_send(_package()))).json()["result"]["task"]
        await app.state.review_handler.close()
        return task

    task = _run(scenario())
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert "artifacts" not in task
    assert expected_error in task["status"]["message"]["parts"][0]["text"]


def test_cancel_propagates_into_default_model_request(
    tmp_path, mock_async_anthropic
):
    async def scenario():
        started = asyncio.Event()
        request_canceled = asyncio.Event()

        async def wait_for_model(**request):
            assert "tools" not in request
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                request_canceled.set()
                raise

        instances = mock_async_anthropic(create=wait_for_model)
        app = build_app("http://review.test", tmp_path, token="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://review.test",
            headers={"A2A-Version": "1.0"},
        ) as client:
            submitted = (
                await client.post("/", json=_send(_package(), immediate=True))
            ).json()["result"]["task"]
            await asyncio.wait_for(started.wait(), timeout=1)
            canceled = (
                await client.post(
                    "/", json=_rpc(2, "CancelTask", {"id": submitted["id"]})
                )
            ).json()["result"]
        assert request_canceled.is_set()
        assert instances[0].exited
        await app.state.review_handler.close()
        return canceled

    canceled = _run(scenario())
    assert canceled["status"]["state"] == "TASK_STATE_CANCELED"
    assert "artifacts" not in canceled
