import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest

from components.command_runner import CancellationToken
from components.github_mcp import GitHubMCP, linked_requests


@pytest.fixture
def local_mcp():
    pytest.importorskip("mcp")
    from mcp import Client
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("Local GitHub protocol fixture")
    calls = []
    entered = threading.Event()
    options = {"block": False, "fail": False, "large": False}

    @server.tool(structured_output=True)
    async def issue_read(method: str, owner: str, repo: str, issue_number: int) -> dict[str, object]:
        """Read an issue from the local test fixture."""
        calls.append((method, owner, repo, issue_number))
        if options["fail"]:
            raise ToolError("Issue fixture unavailable")
        if options["block"]:
            entered.set()
            await asyncio.sleep(30)
        return {"title": "x" * 100_000 if options["large"] else "Issue from SDK", "number": issue_number}

    @server.tool()
    def pull_request_read(method: str, owner: str, repo: str, pullNumber: int) -> dict:
        """Read a pull request."""
        return {"number": pullNumber}

    @server.tool()
    def create_issue(title: str) -> str:
        raise AssertionError("Write tools must never be exposed")

    class LocalGitHub(GitHubMCP):
        @asynccontextmanager
        async def _session(self):
            async with Client(server) as client:
                yield client

    adapter = LocalGitHub("test-only-token", timeout=2)
    return adapter, calls, entered, options


def test_official_sdk_discovery_schema_and_call_preserve_tool_content(local_mcp):
    adapter, calls, _, _ = local_mcp
    tools, handlers = adapter.discover()
    assert {tool["name"] for tool in tools} == {
        "github__issue_read", "github__pull_request_read",
    }
    schema = next(tool["input_schema"] for tool in tools if tool["name"].endswith("issue_read"))
    assert "issue_number" in schema["required"]
    result = handlers["github__issue_read"](
        method="get", owner="owner", repo="repo", issue_number=7,
        _run_id="original-run", _cancel_token=CancellationToken(),
    )
    assert not result.is_error
    assert calls == [("get", "owner", "repo", 7)]
    model_result = json.loads(result.model_content())
    assert "Issue from SDK" in result
    assert model_result["structured_content"]["number"] == 7


def test_allowlist_and_schema_reject_calls_before_execution(local_mcp):
    adapter, calls, _, _ = local_mcp
    adapter.discover()
    assert adapter.call("create_issue", {"title": "blocked"}).is_error
    assert adapter.call("issue_read", {"issue_number": "not-an-integer"}).is_error
    assert calls == []


def test_remote_tool_failure_is_not_reported_as_success(local_mcp):
    adapter, _, _, options = local_mcp
    adapter.discover()
    options["fail"] = True
    result = adapter.call("issue_read", dict(method="get", owner="o", repo="r", issue_number=1))
    assert result.is_error
    assert "unavailable" in result


@pytest.mark.parametrize("cancel", [False, True])
def test_inflight_timeout_and_cancellation_stop_sdk_call(local_mcp, cancel):
    adapter, _, entered, options = local_mcp
    adapter.discover()
    options["block"] = True
    token = CancellationToken()
    if not cancel:
        adapter.timeout = 0.05
    results = []
    thread = threading.Thread(target=lambda: results.append(adapter.call(
        "issue_read", dict(method="get", owner="o", repo="r", issue_number=1),
        _cancel_token=token,
    )))
    thread.start()
    assert entered.wait(1)
    if cancel:
        token.cancel()
    thread.join(2)
    assert not thread.is_alive()
    assert results[0].is_error
    assert getattr(results[0], "cancelled" if cancel else "timed_out")
    assert not token._closers


def test_large_structured_results_cannot_bypass_output_limit(local_mcp):
    adapter, _, _, options = local_mcp
    adapter.discover()
    options["large"] = True
    result = adapter.call("issue_read", dict(method="get", owner="o", repo="r", issue_number=1))
    assert result.truncated
    assert len(result.model_content()) < 90_000


def test_discovery_errors_redact_credentials_and_incomplete_tools_fail():
    class BrokenGitHub(GitHubMCP):
        async def _discover_remote(self):
            raise RuntimeError("Authorization: Bearer secret-token")

    with pytest.raises(RuntimeError) as error:
        BrokenGitHub("secret-token").discover()
    assert "secret-token" not in str(error.value)

    class EmptyGitHub(GitHubMCP):
        async def _discover_remote(self):
            return {}

    with pytest.raises(RuntimeError, match="none of the allowed"):
        EmptyGitHub("test-only-token").discover()


def test_explicit_links_are_deduplicated_bounded_and_keep_pr_argument_case():
    content = [{"type": "text", "text": (
        "https://github.com/Org/Repo/issues/7#issuecomment-1 "
        "https://github.com/org/repo/issues/7 "
        "https://github.com/Org/Repo/pull/8 "
        "https://github.com/Org/Repo/issues/9"
    )}]
    calls = linked_requests(content)
    assert len(calls) == 2
    assert calls[0]["input"]["issue_number"] == 7
    assert calls[1]["input"]["pullNumber"] == 8
    assert not linked_requests("https://github.com.attacker.test/Org/Repo/issues/7")


@pytest.mark.parametrize("suffix", ["?notification_referrer_id=abc", "/", "/files", "/commits", "/checks#check"])
def test_pr_links_from_github_pages_are_recognized(suffix):
    calls = linked_requests(f"https://github.com/Org/Repo/pull/42{suffix}")
    assert calls[0]["input"]["pullNumber"] == 42


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_timeout_must_be_positive_and_finite(timeout):
    with pytest.raises(ValueError):
        GitHubMCP("test-only-token", timeout=timeout)
