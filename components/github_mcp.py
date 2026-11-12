"""Small, read-only adapter for GitHub's hosted MCP server."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Callable
from urllib.parse import urlsplit

from components.command_runner import ToolResult


GITHUB_MCP_ENDPOINT = "https://api.githubcopilot.com/mcp/readonly"
GITHUB_TOOL_NAMES = (
    "issue_read",
    "pull_request_read",
    "search_issues",
    "get_file_contents",
)
_REQUIRED_CONTEXT_TOOLS = frozenset(("issue_read", "pull_request_read"))
_CONTENT_LIMIT = 40_000
_GITHUB_LINK = re.compile(r"https://github\.com[^\s<>\]\[(){}\"']*", re.IGNORECASE)
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, (list, tuple)) else (content,)
    texts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            block_type, text = block.get("type"), block.get("text")
        else:
            block_type, text = getattr(block, "type", None), getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def linked_requests(content: Any) -> list[dict[str, Any]]:
    """Turn up to two explicit GitHub issue/PR links into read-only tool calls."""
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for match in _GITHUB_LINK.finditer(_user_text(content)):
        parsed = urlsplit(match.group().rstrip(".,;:!?"))
        if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com":
            continue
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 4 or not _OWNER.fullmatch(parts[0]) or not _REPO.fullmatch(parts[1]):
            continue
        owner, repo, kind, number_text, *tail = parts
        kind = kind.lower()
        if kind not in ("issues", "pull") or not number_text.isdigit():
            continue
        if tail and (
            kind != "pull" or len(tail) != 1
            or tail[0] not in ("files", "commits", "checks")
        ):
            continue
        number = int(number_text)
        key = (owner.lower(), repo.lower(), kind, number)
        if key in seen:
            continue
        seen.add(key)
        tool, number_key = (
            ("issue_read", "issue_number") if kind == "issues"
            else ("pull_request_read", "pullNumber")
        )
        calls.append({
            "name": f"github__{tool}",
            "input": {
                "method": "get", "owner": owner, "repo": repo, number_key: number,
            },
        })
        if len(calls) == 2:
            break
    return calls


def _safe_error(exc: BaseException | str, token: str) -> str:
    message = str(exc).strip() or type(exc).__name__
    if token:
        message = message.replace(token, "[redacted]")
    message = re.sub(r"(?i)Bearer\s+[^\s,;}'\"]+", "Bearer [redacted]", message)
    message = re.sub(
        r"(?i)(Authorization\s*[:=]\s*)[^\s,;}'\"]+",
        r"\1[redacted]",
        message,
    )
    return message


def _truncate(content: str, limit: int = _CONTENT_LIMIT) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    omitted = len(content)
    while True:
        marker = f"\n... [truncated {omitted} characters]"
        keep = max(0, limit - len(marker))
        if len(content) - keep == omitted:
            return f"{content[:keep]}{marker[:limit]}", True
        omitted = len(content) - keep


def _limit_payload(content: str, structured: Any) -> tuple[str, Any, bool]:
    if structured is None:
        content, truncated = _truncate(content)
        return content, None, truncated
    encoded = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    if len(content) + len(encoded) <= _CONTENT_LIMIT:
        return content, structured, False
    summary = {"truncated": True, "original_characters": len(encoded)}
    summary_size = len(json.dumps(summary, separators=(",", ":")))
    content, _ = _truncate(content, _CONTENT_LIMIT - summary_size)
    return content, summary, True


class GitHubMCP:
    """Synchronous facade over one short-lived MCP session per operation."""

    def __init__(self, token: str, timeout: float = 20):
        if not isinstance(token, str) or not token.strip():
            raise ValueError("GitHub MCP token must be a non-empty string")
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("GitHub MCP timeout must be a finite positive number") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("GitHub MCP timeout must be finite and positive")
        self._token = token.strip()
        self.timeout = timeout
        self._schemas: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def _session(self):
        """Open one SDK client; imports stay lazy when GitHub MCP is disabled."""
        try:
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError(
                "GitHub MCP dependencies are unavailable; install requirements-mcp.txt"
            ) from exc

        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-MCP-Readonly": "true",
            "X-MCP-Tools": ",".join(GITHUB_TOOL_NAMES),
        }
        async with httpx2.AsyncClient(headers=headers, timeout=self.timeout) as http_client:
            transport = streamable_http_client(
                GITHUB_MCP_ENDPOINT,
                http_client=http_client,
            )
            async with Client(transport, read_timeout_seconds=self.timeout) as client:
                yield client

    async def _bounded(self, operation: Callable[[], Any], cancel_token=None):
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        closer = None
        if cancel_token is not None and task is not None:
            closer = cancel_token.register_closer(
                lambda: loop.call_soon_threadsafe(task.cancel)
            )
        try:
            return await asyncio.wait_for(operation(), timeout=self.timeout)
        finally:
            if closer is not None:
                cancel_token.unregister_closer(closer)

    def _run(self, operation: Callable[[], Any], cancel_token=None):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("GitHubMCP's synchronous API cannot run inside an event loop")
        return asyncio.run(self._bounded(operation, cancel_token))

    async def _discover_remote(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        async with self._session() as client:
            cursor = None
            seen_cursors: set[str] = set()
            while True:
                page = await client.list_tools(cursor=cursor)
                for tool in page.tools:
                    if tool.name in GITHUB_TOOL_NAMES:
                        found.setdefault(tool.name, tool)
                cursor = page.next_cursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise RuntimeError("GitHub MCP returned a repeated tools/list cursor")
                seen_cursors.add(cursor)
        return found

    def discover(self) -> tuple[list[dict[str, Any]], dict[str, Callable[..., ToolResult]]]:
        """Discover the fixed allowlist and build namespaced synchronous handlers."""
        self._schemas = {}
        try:
            found = self._run(self._discover_remote)
        except TimeoutError:
            raise RuntimeError(
                f"GitHub MCP discovery timed out after {self.timeout:g}s"
            ) from None
        except Exception as exc:
            message = _safe_error(exc, self._token)
            raise RuntimeError(f"GitHub MCP discovery failed: {message}") from None

        if not found:
            raise RuntimeError("GitHub MCP discovery returned none of the allowed tools")
        missing = _REQUIRED_CONTEXT_TOOLS.difference(found)
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(f"GitHub MCP discovery is incomplete; missing required tools: {names}")

        tools: list[dict[str, Any]] = []
        handlers: dict[str, Callable[..., ToolResult]] = {}
        for remote_name in GITHUB_TOOL_NAMES:
            remote = found.get(remote_name)
            if remote is None:
                continue
            local_name = f"github__{remote_name}"
            schema = remote.input_schema
            self._schemas[remote_name] = schema
            tools.append({
                "name": local_name,
                "description": remote.description or "",
                "input_schema": schema,
            })

            def make_handler(remote_tool: str):
                def handler(_cancel_token=None, _run_id=None, **arguments) -> ToolResult:
                    return self.call(
                        remote_tool, arguments,
                        _cancel_token=_cancel_token, _run_id=_run_id,
                    )

                return handler

            handlers[local_name] = make_handler(remote_name)
        return tools, handlers

    async def _call_remote(self, name: str, arguments: dict[str, Any]):
        async with self._session() as client:
            return await client.call_tool(name, arguments)

    def _result(
        self,
        content: str,
        *,
        name: str,
        started: float,
        is_error: bool,
        structured_content: Any = None,
        cancelled: bool = False,
        timed_out: bool = False,
    ) -> ToolResult:
        content, structured_content, truncated = _limit_payload(
            content, structured_content
        )
        result = ToolResult(
            content,
            is_error=is_error,
            structured_content=structured_content,
            server=GITHUB_MCP_ENDPOINT,
            remote_tool=name,
            duration=round(time.monotonic() - started, 3),
            cancelled=cancelled,
            timed_out=timed_out,
            truncated=truncated,
        )
        # ToolResult reserves the positional ``content`` argument, so attach the
        # same text after construction to keep model_content() self-contained.
        result.details["content"] = content
        result.content = content
        return result

    def _error(self, message: str, name: str, started: float, **flags) -> ToolResult:
        return self._result(
            f"Error: {message}", name=name, started=started, is_error=True, **flags
        )

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        _cancel_token=None,
        _run_id=None,
    ) -> ToolResult:
        """Validate and invoke one discovered read-only GitHub tool without retries."""
        del _run_id
        started = time.monotonic()
        if name not in GITHUB_TOOL_NAMES:
            return self._error(f"GitHub MCP tool is not allowed: {name}", name, started)
        if _cancel_token is not None and _cancel_token.is_cancelled:
            return self._error("GitHub MCP call cancelled", name, started, cancelled=True)
        schema = self._schemas.get(name)
        if schema is None:
            return self._error(f"GitHub MCP tool was not discovered: {name}", name, started)
        try:
            from jsonschema import ValidationError, validate

            validate(instance=arguments, schema=schema)
        except ValidationError as exc:
            location = "/".join(str(part) for part in exc.absolute_path) or "input"
            message = _safe_error(exc.message, self._token)
            return self._error(
                f"Invalid GitHub MCP arguments at {location}: {message}", name, started,
            )
        except Exception as exc:
            message = _safe_error(exc, self._token)
            return self._error(
                f"GitHub MCP schema validation failed: {message}", name, started,
            )

        try:
            result = self._run(
                lambda: self._call_remote(name, arguments),
                _cancel_token,
            )
        except asyncio.CancelledError:
            return self._error("GitHub MCP call cancelled", name, started, cancelled=True)
        except TimeoutError:
            return self._error(
                f"GitHub MCP call timed out after {self.timeout:g}s", name, started,
                timed_out=True,
            )
        except Exception as exc:
            message = _safe_error(exc, self._token)
            return self._error(f"GitHub MCP call failed: {message}", name, started)

        text = "\n\n".join(
            block.text for block in result.content
            if getattr(block, "type", None) == "text"
        )
        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content, ensure_ascii=False)
        if not text:
            text = "(GitHub MCP returned no text content)"
        return self._result(
            text,
            name=name,
            started=started,
            is_error=bool(result.is_error),
            structured_content=result.structured_content,
        )
