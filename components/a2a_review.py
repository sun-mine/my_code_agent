"""Durable synchronous client for the independent A2A patch reviewer."""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import math
import os
import re
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, ClientCallContext, ClientConfig, create_client
from a2a.client.errors import A2AClientTimeoutError, AgentCardResolutionError
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)
from google.protobuf import json_format, struct_pb2

from components.review_contract import IDENTITY_FIELDS, validate_package, validate_review


_PUBLIC_FIELDS = (
    "status", *IDENTITY_FIELDS, "task_id", "context_id", "agent_url",
    "message_id", "artifact", "error", "started_at", "finished_at",
    "duration", "usage",
)
_FINAL_STATES = {"completed", "failed", "cancelled", "unknown", "cancel_unknown"}
_TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}
_POLL_INTERVAL = 0.25
_CANCEL_BUDGET = 3.0
_ERROR_LIMIT = 1_000


class _CancelRequested(Exception):
    pass


class A2AReviewClient:
    """Submit one review package and durably follow its A2A task."""

    def __init__(self, url: str, timeout: float = 120, token: str = ""):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("A2A review timeout must be a positive finite number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A2A review timeout must be a positive finite number")
        if not isinstance(token, str):
            raise ValueError("A2A review token must be a string")
        self.url = self._validate_url(url, "A2A review URL").rstrip("/")
        self.timeout = float(timeout)
        self._token = token
        self._origin = self._url_origin(self.url)

    def review(
        self,
        package: dict,
        record_path: Path,
        cancellation_token=None,
        on_update: Callable[[dict], None] | None = None,
    ) -> dict:
        """Run the async SDK lifecycle in one loop and return a durable status."""
        package = validate_package(package)
        record_path = Path(record_path)
        record = self._load_or_create(record_path, package)
        if record["status"] == "completed":
            record["artifact"] = validate_review(record["artifact"], package)
            return self._public(record)
        if (
            record["status"] == "failed"
            and record["submit_attempts"] > 0
            and not record.get("task_id")
        ):
            return self._public(record)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("A2AReviewClient.review() cannot run inside an event loop")

        cancel_event = threading.Event()
        if cancellation_token is not None and getattr(cancellation_token, "is_cancelled", False):
            cancel_event.set()
        closer = None
        register = getattr(cancellation_token, "register_closer", None)
        if callable(register):
            closer = register(cancel_event.set)

        record["status"] = "unknown"
        record["finished_at"] = None

        def save() -> None:
            end = record["finished_at"] if record["finished_at"] is not None else time.time()
            record["duration"] = max(0.0, end - record["started_at"])
            self._atomic_write(record_path, record)
            if on_update is not None:
                with suppress(Exception):
                    on_update(self._public(record))

        save()  # Durable submit intent precedes discovery and message:send.
        try:
            if self._cancelled(cancel_event, cancellation_token) and not record.get("task_id"):
                status = "cancelled" if record["submit_attempts"] == 0 else "cancel_unknown"
                error = (
                    None if status == "cancelled"
                    else "Review submission may have reached the remote agent"
                )
                return self._finish(record, save, status, error)
            return asyncio.run(
                self._review_async(package, record, save, cancel_event, cancellation_token)
            )
        finally:
            unregister = getattr(cancellation_token, "unregister_closer", None)
            if closer is not None and callable(unregister):
                unregister(closer)

    async def _review_async(self, package, record, save, cancel_event, cancel_token):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        client = None
        http_client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=False,
            timeout=httpx.Timeout(min(5.0, self.timeout)),
        )
        try:
            resolver = A2ACardResolver(http_client, self.url)
            discovery_timeout = min(5.0, self._remaining(deadline))
            card = await asyncio.wait_for(
                resolver.get_agent_card(http_kwargs={"timeout": discovery_timeout}),
                timeout=discovery_timeout,
            )
            idempotent = self._validate_card(card)
            record["idempotent_review"] = idempotent
            save()

            config = ClientConfig(
                streaming=False,
                polling=True,
                httpx_client=http_client,
                supported_protocol_bindings=["JSONRPC"],
                use_client_preference=True,
                accepted_output_modes=["application/json"],
            )
            client = await create_client(card, client_config=config)

            if self._cancelled(cancel_event, cancel_token):
                if record.get("task_id"):
                    return await self._cancel_and_confirm(client, package, record, save)
                status = "cancelled" if record["submit_attempts"] == 0 else "cancel_unknown"
                error = (
                    None if status == "cancelled"
                    else "Review cancelled before a task ID was known"
                )
                return self._finish(record, save, status, error)

            if record.get("task_id"):
                task = await self._interruptible(
                    lambda: client.get_task(
                        GetTaskRequest(id=record["task_id"]),
                        context=self._context(deadline),
                    ),
                    deadline,
                    cancel_event,
                    cancel_token,
                )
            else:
                task = await self._submit(
                    client, package, record, save, deadline, cancel_event, cancel_token,
                )
                if task is None:
                    return self._public(record)

            while True:
                self._accept_task_identity(task, record, save)
                if task.status.state in _TERMINAL_TASK_STATES:
                    return self._finish_from_task(task, package, record, save)
                await self._poll_pause(deadline, cancel_event, cancel_token)
                task = await self._interruptible(
                    lambda: client.get_task(
                        GetTaskRequest(id=record["task_id"]),
                        context=self._context(deadline),
                    ),
                    deadline,
                    cancel_event,
                    cancel_token,
                )
        except _CancelRequested:
            if client is not None and record.get("task_id"):
                return await self._cancel_and_confirm(client, package, record, save)
            status = "cancelled" if record["submit_attempts"] == 0 else "cancel_unknown"
            error = (
                None if status == "cancelled"
                else "Review cancellation could not be confirmed"
            )
            return self._finish(record, save, status, error)
        except Exception as exc:
            if self._cancelled(cancel_event, cancel_token):
                status = (
                    "cancelled"
                    if record["submit_attempts"] == 0 and not record.get("task_id")
                    else "cancel_unknown"
                )
            elif self._is_uncertain(exc):
                status = "unknown"
            else:
                status = "failed"
            error = None if status == "cancelled" else self._safe_error(exc)
            return self._finish(record, save, status, error)
        finally:
            if client is not None:
                with suppress(Exception):
                    await client.close()
            if not http_client.is_closed:
                with suppress(Exception):
                    await http_client.aclose()

    async def _submit(self, client, package, record, save, deadline, cancel_event, cancel_token):
        if record["submit_attempts"] >= 1 and not record.get("idempotent_review"):
            self._finish(
                record, save, "unknown",
                record.get("error") or "Previous submission outcome is unknown and the agent does not declare idempotent review",
            )
            return None
        if record["submit_attempts"] >= 2:
            self._finish(
                record, save, "unknown",
                record.get("error") or "Review submission outcome remains unknown after one idempotent retry",
            )
            return None

        value = json_format.ParseDict(package, struct_pb2.Value())
        request = SendMessageRequest(message=Message(
            message_id=record["message_id"],
            role=Role.ROLE_USER,
            parts=[Part(data=value)],
        ))

        while record["submit_attempts"] < 2:
            record["submit_attempts"] += 1
            record["error"] = None
            save()
            try:
                return await self._interruptible(
                    lambda: self._send_for_task(client, request, deadline),
                    deadline,
                    cancel_event,
                    cancel_token,
                )
            except _CancelRequested:
                raise
            except Exception as exc:
                record["error"] = self._safe_error(exc)
                retry = (
                    record.get("idempotent_review")
                    and record["submit_attempts"] == 1
                    and self._is_uncertain(exc)
                    and self._remaining(deadline) > 0
                )
                if not retry:
                    status = "unknown" if self._is_uncertain(exc) else "failed"
                    self._finish(record, save, status, record["error"])
                    return None
                save()
        return None

    async def _send_for_task(self, client, request, deadline):
        async for response in client.send_message(request, context=self._context(deadline)):
            if response.HasField("task"):
                return response.task
            if response.HasField("message"):
                raise ValueError("A2A reviewer returned a message instead of a task")
        raise ValueError("A2A reviewer returned no task")

    async def _cancel_and_confirm(self, client, package, record, save):
        deadline = asyncio.get_running_loop().time() + _CANCEL_BUDGET
        last_error = None
        try:
            task = await asyncio.wait_for(
                client.cancel_task(
                    CancelTaskRequest(id=record["task_id"]),
                    context=self._context(deadline),
                ),
                timeout=self._remaining(deadline),
            )
            self._accept_task_identity(task, record, save)
            if task.status.state in _TERMINAL_TASK_STATES:
                return self._finish_from_task(task, package, record, save)
        except Exception as exc:
            last_error = exc

        while self._seconds_left(deadline) > 0:
            try:
                await asyncio.sleep(min(_POLL_INTERVAL, self._seconds_left(deadline)))
                task = await asyncio.wait_for(
                    client.get_task(
                        GetTaskRequest(id=record["task_id"]),
                        context=self._context(deadline),
                    ),
                    timeout=self._remaining(deadline),
                )
                self._accept_task_identity(task, record, save)
                if task.status.state in _TERMINAL_TASK_STATES:
                    return self._finish_from_task(task, package, record, save)
            except Exception as exc:
                last_error = exc
                break
        detail = self._safe_error(last_error) if last_error else "Remote cancellation was not confirmed within 3 seconds"
        return self._finish(record, save, "cancel_unknown", detail)

    async def _interruptible(self, operation, deadline, cancel_event, cancel_token):
        if self._cancelled(cancel_event, cancel_token):
            raise _CancelRequested
        task = asyncio.create_task(operation())
        cancelled = asyncio.create_task(self._wait_cancel(cancel_event, cancel_token))
        try:
            done, _ = await asyncio.wait(
                {task, cancelled},
                timeout=self._remaining(deadline),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return task.result()
            if cancelled in done:
                raise _CancelRequested
            raise TimeoutError("A2A review timed out")
        finally:
            for pending in (task, cancelled):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, cancelled, return_exceptions=True)

    async def _poll_pause(self, deadline, cancel_event, cancel_token):
        remaining = self._remaining(deadline)
        try:
            await asyncio.wait_for(
                self._wait_cancel(cancel_event, cancel_token),
                timeout=min(_POLL_INTERVAL, remaining),
            )
        except TimeoutError:
            if self._seconds_left(deadline) <= 0:
                raise TimeoutError("A2A review timed out") from None
            return
        raise _CancelRequested

    @staticmethod
    async def _wait_cancel(cancel_event, cancel_token):
        while not A2AReviewClient._cancelled(cancel_event, cancel_token):
            await asyncio.sleep(0.05)

    def _validate_card(self, card) -> bool:
        skills = [skill for skill in card.skills if skill.id == "review_patch"]
        if len(skills) != 1:
            raise ValueError("A2A agent must advertise exactly one review_patch skill")
        interfaces = [
            interface for interface in card.supported_interfaces
            if interface.protocol_binding == "JSONRPC"
        ]
        if not interfaces:
            raise ValueError("A2A review agent does not advertise a JSONRPC interface")
        for interface in interfaces:
            endpoint = self._validate_url(interface.url, "A2A JSONRPC interface URL")
            if self._url_origin(endpoint) != self._origin:
                raise ValueError("A2A JSONRPC interface must use the configured agent origin")

        requirements = skills[0].security_requirements or card.security_requirements
        bearer_names = {
            name for name, scheme in card.security_schemes.items()
            if scheme.HasField("http_auth_security_scheme")
            and scheme.http_auth_security_scheme.scheme.lower() == "bearer"
        }
        supports_bearer = any(
            requirement.schemes
            and all(name in bearer_names for name in requirement.schemes)
            for requirement in requirements
        )
        if requirements and (not self._token or not supports_bearer):
            raise ValueError("A2A review agent requires unsupported or missing authentication")
        if self._token and not supports_bearer:
            raise ValueError("A2A review token was configured but the agent does not require Bearer authentication")
        return "idempotent_review=true" in skills[0].tags

    def _accept_task_identity(self, task, record, save):
        if not task.id:
            raise ValueError("A2A reviewer returned a task without an ID")
        if record.get("task_id") and record["task_id"] != task.id:
            raise ValueError("A2A reviewer changed the task ID")
        if record.get("context_id") and task.context_id and record["context_id"] != task.context_id:
            raise ValueError("A2A reviewer changed the context ID")
        record["task_id"] = task.id
        record["context_id"] = task.context_id or record.get("context_id")
        save()

    def _finish_from_task(self, task, package, record, save):
        state = task.status.state
        if state == TaskState.TASK_STATE_CANCELED:
            return self._finish(record, save, "cancelled", None)
        if state != TaskState.TASK_STATE_COMPLETED:
            name = TaskState.Name(state).removeprefix("TASK_STATE_").lower()
            return self._finish(record, save, "failed", f"A2A review task ended as {name}")

        candidates = []
        for artifact in task.artifacts:
            if artifact.name != "review.json":
                continue
            candidates.extend(
                json_format.MessageToDict(part.data)
                for part in artifact.parts
                if part.WhichOneof("content") == "data"
            )
        if len(candidates) != 1:
            return self._finish(
                record, save, "failed",
                "Completed A2A task must contain exactly one review.json DataPart",
            )
        try:
            review = validate_review(candidates[0], package)
        except Exception as exc:
            return self._finish(record, save, "failed", self._safe_error(exc))
        record["artifact"] = review
        record["usage"] = review["usage"]
        return self._finish(record, save, "completed", None)

    def _finish(self, record, save, status, error):
        record["status"] = status
        record["error"] = error
        record["finished_at"] = time.time()
        record["duration"] = max(0.0, record["finished_at"] - record["started_at"])
        save()
        return self._public(record)

    def _load_or_create(self, path: Path, package: dict) -> dict:
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid A2A review record: {type(exc).__name__}") from None
            if not isinstance(record, dict):
                raise ValueError("Invalid A2A review record")
            expected = {key: package[key] for key in IDENTITY_FIELDS}
            actual = {key: record.get(key) for key in IDENTITY_FIELDS}
            if actual != expected or record.get("agent_url") != self.url:
                raise ValueError("A2A review record belongs to another run, snapshot, or agent")
            if record.get("message_id") != package["invocation_id"]:
                raise ValueError("A2A review record message identity mismatch")
            if record.get("status") not in _FINAL_STATES:
                raise ValueError("Invalid A2A review record status")
            attempts = record.get("submit_attempts")
            if type(attempts) is not int or not 0 <= attempts <= 2:
                raise ValueError("Invalid A2A review submission record")
            return record

        now = time.time()
        return {
            "record_version": 1,
            "status": "unknown",
            **{key: package[key] for key in IDENTITY_FIELDS},
            "task_id": None,
            "context_id": None,
            "agent_url": self.url,
            "message_id": package["invocation_id"],
            "artifact": None,
            "error": None,
            "started_at": now,
            "finished_at": None,
            "duration": 0.0,
            "usage": {"input_tokens": None, "output_tokens": None},
            "submit_intent": True,
            "submit_attempts": 0,
            "idempotent_review": False,
        }

    @staticmethod
    def _atomic_write(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def _safe_error(self, exc) -> str:
        if exc is None:
            return "Unknown A2A review error"
        message = f"{type(exc).__name__}: {exc}"
        if self._token:
            message = message.replace(self._token, "[redacted]")
        message = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", message)
        return message[:_ERROR_LIMIT]

    @staticmethod
    def _is_uncertain(exc) -> bool:
        current = exc
        while current is not None:
            if isinstance(current, (TimeoutError, A2AClientTimeoutError, httpx.TimeoutException)):
                return True
            if isinstance(current, httpx.HTTPStatusError):
                return current.response.status_code >= 500
            if isinstance(current, httpx.RequestError):
                return True
            if isinstance(current, json.JSONDecodeError):
                return True
            current = current.__cause__
        if isinstance(exc, AgentCardResolutionError):
            status = getattr(exc, "status_code", None)
            return status is not None and status >= 500
        text = str(exc).lower()
        return "network communication error" in text or "json decode error" in text

    @staticmethod
    def _cancelled(event, token) -> bool:
        return event.is_set() or bool(token is not None and getattr(token, "is_cancelled", False))

    @staticmethod
    def _remaining(deadline) -> float:
        remaining = A2AReviewClient._seconds_left(deadline)
        if remaining <= 0:
            raise TimeoutError("A2A review timed out")
        return remaining

    @staticmethod
    def _seconds_left(deadline) -> float:
        return max(0.0, deadline - asyncio.get_running_loop().time())

    @classmethod
    def _context(cls, deadline):
        return ClientCallContext(timeout=min(5.0, cls._remaining(deadline)))

    @staticmethod
    def _public(record: dict) -> dict:
        return copy.deepcopy({key: record.get(key) for key in _PUBLIC_FIELDS})

    @staticmethod
    def _validate_url(url: str, label: str) -> str:
        if not isinstance(url, str) or not url:
            raise ValueError(f"{label} must be an HTTP(S) URL")
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"{label} has an invalid port") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is not None and not 0 < port < 65536
        ):
            raise ValueError(f"{label} must not contain credentials, query, or fragment")
        if parsed.scheme.lower() == "http":
            loopback = parsed.hostname.lower() == "localhost"
            with suppress(ValueError):
                loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
            if not loopback:
                raise ValueError(f"{label} must use HTTPS except on loopback")
        return url

    @staticmethod
    def _url_origin(url: str):
        parsed = urlsplit(url)
        default = 443 if parsed.scheme.lower() == "https" else 80
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or default
