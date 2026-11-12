"""Black-box A2A checks against a separate HTTP service process."""
import contextlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("a2a")
import httpx

from components.a2a_review import A2AReviewClient
from components.command_runner import CancellationToken
from components.review_contract import seal_package

ROOT = Path(__file__).resolve().parents[1]


def package(run_id="run"):
    return seal_package({
        "session_id": "session", "run_id": run_id, "requirement": "Review this patch",
        "diff": "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-value=0\n+value=1\n",
        "snippets": [{"path": "main.py", "start_line": 1, "content": "value=1"}],
    })


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextlib.contextmanager
def service(directory, port, delay=0):
    url = f"http://127.0.0.1:{port}"
    code = "\n".join([
        "import asyncio, uvicorn",
        "from a2a_review_server import build_app, _demo_reviewer",
        "async def reviewer(package):",
        f"    await asyncio.sleep({delay!r})",
        "    return await _demo_reviewer(package)",
        f"app=build_app({url!r},{str(directory)!r},reviewer=reviewer,token='')",
        f"uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')",
    ])
    with (directory.parent / f"service-{port}.log").open("a") as log:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(f"Review service exited: {process.returncode}: {Path(log.name).read_text()}")
                try:
                    if httpx.get(url + "/.well-known/agent-card.json", timeout=.2, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(.05)
            else:
                pytest.fail(f"Independent Review service did not start: {Path(log.name).read_text()}")
            yield url, process
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def task_count(directory):
    with sqlite3.connect(directory / "review_tasks.sqlite3") as database:
        return database.execute("SELECT count(*) FROM tasks").fetchone()[0]


def test_cross_process_discovery_artifact_dedup_and_service_restart(tmp_path):
    port = free_port()
    directory = tmp_path / "server"
    request = package()
    with service(directory, port) as (url, process):
        assert process.pid != __import__("os").getpid()
        card = httpx.get(url + "/.well-known/agent-card.json", trust_env=False).json()
        assert any(skill["id"] == "review_patch" for skill in card["skills"])
        client = A2AReviewClient(url, timeout=5)
        first = client.review(request, tmp_path / "first.json")
        second = client.review(request, tmp_path / "second.json")
        assert first["status"] == second["status"] == "completed"
        assert first["task_id"] == second["task_id"]
        assert first["artifact"]["patch_hash"] == request["patch_hash"]
        assert any("DEMO" in item for item in first["artifact"]["context_limits"])
        assert task_count(directory) == 1
    with service(directory, port) as (url, _):
        recovered = A2AReviewClient(url, timeout=5).review(request, tmp_path / "after-restart.json")
        assert recovered["status"] == "completed"
        assert recovered["task_id"] == first["task_id"]
        assert task_count(directory) == 1


def test_cross_process_timeout_resume_and_cancel_confirmation(tmp_path):
    port = free_port()
    directory = tmp_path / "server"
    with service(directory, port, delay=8) as (url, _):
        request = package("resume")
        record_path = tmp_path / "resume.json"
        unknown = A2AReviewClient(url, timeout=5).review(request, record_path)
        assert unknown["status"] == "unknown"
        assert unknown["task_id"]
        recovered = A2AReviewClient(url, timeout=15).review(request, record_path)
        assert recovered["status"] == "completed"
        assert recovered["task_id"] == unknown["task_id"]
        assert task_count(directory) == 1

        token = CancellationToken()
        task_known = threading.Event()
        results = []

        def update(value):
            if value.get("task_id"):
                task_known.set()

        thread = threading.Thread(target=lambda: results.append(A2AReviewClient(url, timeout=15).review(
            package("cancel"), tmp_path / "cancel.json", cancellation_token=token, on_update=update,
        )))
        thread.start()
        assert task_known.wait(10)
        token.cancel()
        thread.join(10)
        assert not thread.is_alive()
        assert results[0]["status"] == "cancelled"
        stored = json.loads((tmp_path / "cancel.json").read_text())
        assert stored["status"] == "cancelled"
        assert task_count(directory) == 2
