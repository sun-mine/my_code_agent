"""Durable, evidence-based delivery reports for one agent run."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional

from components.session_store import to_jsonable


_REPORT_LIMIT = 200_000
_COMMAND_OUTPUT_LIMIT = 20_000
_COMMAND_LIMIT = 20_000
_FILE_DIFF_LIMIT = 128_000
_FILE_LIMIT = 500
_HASH_LIMIT = 2_000_000
_GIT_TIMEOUT = 20
_SYNC_EVENT_TYPES = {
    "run_start", "run_end", "tool_start", "tool_result", "tool_approval_request",
    "verification", "interrupted", "error", "delivery_report",
}
_SYNC_EVENT_BATCH = 20
_SYNC_EVENT_INTERVAL = 1.0


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return value, False
    marker = b"\n...[truncated]"
    return _decode(raw[: max(0, limit - len(marker))]) + _decode(marker), True


def _is_sensitive_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path).parts]
    if any(part in {".agent", ".ssh", ".aws", "credentials", "secrets"} for part in parts):
        return True
    name = parts[-1] if parts else ""
    if name == ".env" or name.startswith(".env."):
        return True
    if name in {
        ".npmrc", ".pypirc", ".netrc", ".git-credentials",
        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    }:
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")):
        return True
    stem = name.split(".", 1)[0]
    return stem in {
        "credential", "credentials", "secret", "secrets",
        "private-key", "private_key", "service-account", "service_account",
    }


class RunReportRecorder:
    """Capture a run's durable event trace and final workspace evidence."""

    def __init__(self, workdir: str, run_id: str, session_id: str):
        self.workdir = Path(workdir or ".").expanduser().resolve()
        self.run_id = run_id
        self.session_id = session_id
        self.started_at = time.time()
        safe_run_id = run_id if Path(run_id).name == run_id else hashlib.sha256(
            run_id.encode("utf-8", errors="replace")
        ).hexdigest()
        self.directory = self.workdir / ".agent" / "runs" / safe_run_id
        self.events_path = self.directory / "events.jsonl"
        self.report_path = self.directory / "report.json"
        self.warnings: list[str] = []
        self._lock = threading.RLock()
        self._critical_failure = False
        self._commands: list[Dict[str, Any]] = []
        self._command_by_id: Dict[str, Dict[str, Any]] = {}
        self._usage = {"input_tokens": 0, "output_tokens": 0}
        self.repo_root: Optional[Path] = None
        self.base_sha: Optional[str] = None
        self._baseline_ref: Optional[str] = None
        self._baseline_untracked: Dict[str, str] = {}
        self._trace_ready = False
        self._events_since_sync = 0
        self._last_sync = time.monotonic()

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.events_path.touch(exist_ok=True)
            self._trace_ready = True
        except OSError as exc:
            self._fail(f"Event trace initialization failed: {type(exc).__name__}: {exc}")
        self._capture_git_baseline()

    def _fail(self, message: str) -> None:
        self._critical_failure = True
        _append_warning(self.warnings, message)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if self.repo_root is None:
            cwd = self.workdir
        else:
            cwd = self.repo_root
        env = dict(os.environ)
        env["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=env,
        )
        if check and result.returncode != 0:
            detail = _decode(result.stderr).strip() or f"exit {result.returncode}"
            raise RuntimeError(detail)
        return result

    def _capture_git_baseline(self) -> None:
        try:
            root = self._git("rev-parse", "--show-toplevel")
            self.repo_root = Path(_decode(root.stdout).strip()).resolve()
            head = self._git("rev-parse", "--verify", "HEAD", check=False)
            if head.returncode != 0:
                _append_warning(self.warnings, "Git repository has no commit; diff is unsupported")
                return
            self.base_sha = _decode(head.stdout).strip()
            snapshot = self._git("stash", "create", f"agent-run-{self.run_id}")
            self._baseline_ref = _decode(snapshot.stdout).strip() or self.base_sha
            self._baseline_untracked = self._snapshot_untracked()
        except FileNotFoundError:
            _append_warning(self.warnings, "Git is unavailable; diff is unsupported")
        except subprocess.TimeoutExpired:
            self._fail("Git baseline capture timed out; diff is unsupported")
        except Exception as exc:
            message = str(exc)
            if "not a git repository" in message.lower():
                _append_warning(self.warnings, "Workdir is not a Git repository; diff is unsupported")
            else:
                self._fail(f"Git baseline capture failed: {type(exc).__name__}: {exc}")
            self.repo_root = None
            self.base_sha = None
            self._baseline_ref = None

    def _untracked_paths(self) -> list[str]:
        result = self._git("ls-files", "--others", "--exclude-standard", "-z")
        paths = [_decode(item) for item in result.stdout.split(b"\0") if item]
        visible = [
            path for path in paths
            if ".agent" not in {part.lower() for part in PurePosixPath(path).parts}
        ]
        safe = [path for path in visible if not _is_sensitive_path(path)]
        omitted = len(visible) - len(safe)
        if omitted:
            _append_warning(
                self.warnings,
                f"Omitted {omitted} potentially sensitive untracked path(s)",
            )
        return safe

    def _fingerprint(self, path: str) -> str:
        assert self.repo_root is not None
        target = self.repo_root / path
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode):
            return f"symlink:{info.st_size}:{info.st_mtime_ns}"
        if not stat.S_ISREG(info.st_mode):
            return f"other:{info.st_mode}:{info.st_size}:{info.st_mtime_ns}"
        if info.st_size > _HASH_LIMIT:
            return f"large:{info.st_size}:{info.st_mtime_ns}"
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _snapshot_untracked(self) -> Dict[str, str]:
        snapshot = {}
        for path in self._untracked_paths():
            try:
                snapshot[path] = self._fingerprint(path)
            except OSError:
                snapshot[path] = "unreadable"
        return snapshot

    def _ingest(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "usage":
            for key in ("input_tokens", "output_tokens"):
                value = event.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self._usage[key] += value
            return

        tool_name = event.get("name") or event.get("tool_name")
        tool_id = event.get("tool_use_id")
        if event_type == "tool_start" and tool_name == "bash":
            command = event.get("input", {}).get("command") if isinstance(
                event.get("input"), dict
            ) else None
            if not isinstance(tool_id, str) or not isinstance(command, str):
                _append_warning(self.warnings, "Ignored malformed bash tool_start evidence")
                return
            command, truncated = _truncate(command, _COMMAND_LIMIT)
            if truncated:
                _append_warning(self.warnings, f"Command {tool_id} was truncated")
            item = {
                "tool_use_id": tool_id,
                "command": command,
                "exit_code": None,
                "_started_at": event.get("ts"),
                "_purpose": event.get("purpose"),
                "_has_result": False,
            }
            self._commands.append(item)
            self._command_by_id[tool_id] = item
            return

        if event_type != "tool_result" or not isinstance(tool_id, str):
            return
        item = self._command_by_id.get(tool_id)
        if item is None:
            if tool_name == "bash":
                _append_warning(
                    self.warnings,
                    f"Bash result {tool_id} has no matching tool_start; command omitted",
                )
            return
        item["_has_result"] = True
        exit_code = event.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            item["exit_code"] = exit_code
        elif exit_code is not None:
            _append_warning(self.warnings, f"Bash result {tool_id} has an invalid exit_code")
        duration = event.get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            item["duration"] = duration
        for key in ("timed_out", "cancelled"):
            if isinstance(event.get(key), bool):
                item[key] = event[key]
        for key in ("stdout", "stderr"):
            if isinstance(event.get(key), str):
                value, truncated = _truncate(event[key], _COMMAND_OUTPUT_LIMIT)
                item[key] = value
                if truncated:
                    _append_warning(
                        self.warnings,
                        f"Command {tool_id} {key} was truncated",
                    )

    def record_event(self, event: Dict[str, Any]) -> bool:
        return self.record_events([event])

    def record_events(self, events: Iterable[Dict[str, Any]]) -> bool:
        serializable = [dict(to_jsonable(event)) for event in events]
        with self._lock:
            for event in serializable:
                self._ingest(event)
            if not self._trace_ready:
                return False
            try:
                with self.events_path.open("a", encoding="utf-8") as handle:
                    for event in serializable:
                        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                        handle.write("\n")
                    handle.flush()
                    self._events_since_sync += len(serializable)
                    now = time.monotonic()
                    # Stream events are flushed on every append; run/tool/terminal
                    # boundaries fsync immediately, while chunks sync in batches.
                    should_sync = (
                        any(event.get("type") in _SYNC_EVENT_TYPES for event in serializable)
                        or self._events_since_sync >= _SYNC_EVENT_BATCH
                        or now - self._last_sync >= _SYNC_EVENT_INTERVAL
                    )
                    if should_sync:
                        os.fsync(handle.fileno())
                        self._events_since_sync = 0
                        self._last_sync = now
                return True
            except (OSError, TypeError, ValueError) as exc:
                self._trace_ready = False
                self._fail(f"Event trace persistence failed: {type(exc).__name__}: {exc}")
                return False

    def _tracked_files(self) -> list[Dict[str, str]]:
        assert self._baseline_ref
        result = self._git(
            "diff", "--name-status", "-z", "--no-renames",
            self._baseline_ref, "--", ".",
        )
        parts = [_decode(item) for item in result.stdout.split(b"\0") if item]
        files = []
        for index in range(0, len(parts) - 1, 2):
            status_code, path = parts[index], parts[index + 1]
            if _is_sensitive_path(path):
                _append_warning(self.warnings, "Omitted potentially sensitive or internal tracked path(s)")
                continue
            files.append({"path": path, "status": status_code[:1]})
        return files

    def _baseline_size(self, path: str) -> Optional[int]:
        assert self._baseline_ref
        result = self._git("cat-file", "-s", f"{self._baseline_ref}:{path}", check=False)
        if result.returncode != 0:
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    def _tracked_diff(self, files: list[Dict[str, str]]) -> str:
        assert self.repo_root is not None and self._baseline_ref
        chunks = []
        remaining = _REPORT_LIMIT
        for item in files:
            path = item["path"]
            target = self.repo_root / path
            try:
                final_size = target.lstat().st_size if target.exists() or target.is_symlink() else 0
            except OSError:
                final_size = _FILE_DIFF_LIMIT + 1
            base_size = self._baseline_size(path) or 0
            if max(base_size, final_size) > _FILE_DIFF_LIMIT:
                _append_warning(self.warnings, f"Diff omitted for large file: {path}")
                continue
            result = self._git(
                "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--unified=3",
                self._baseline_ref, "--", path,
            )
            chunk = _decode(result.stdout)
            if not chunk:
                continue
            chunk, truncated = _truncate(chunk, remaining)
            chunks.append(chunk)
            remaining -= len(chunk.encode("utf-8", errors="replace"))
            if truncated or remaining <= 0:
                _append_warning(self.warnings, f"Diff truncated at {_REPORT_LIMIT} bytes")
                break
        return "".join(chunks)

    def _untracked_changes(self) -> tuple[list[Dict[str, str]], list[str]]:
        assert self.repo_root is not None
        final = self._snapshot_untracked()
        files = []
        new_paths = []
        for path in sorted(set(self._baseline_untracked) | set(final)):
            before = self._baseline_untracked.get(path)
            after = final.get(path)
            if before == after:
                continue
            if before is None:
                files.append({"path": path, "status": "??"})
                new_paths.append(path)
            elif after is None:
                files.append({"path": path, "status": "D"})
                _append_warning(self.warnings, f"Content diff unavailable for deleted untracked file: {path}")
            else:
                files.append({"path": path, "status": "M"})
                _append_warning(self.warnings, f"Content diff unavailable for modified untracked file: {path}")
        return files, new_paths

    def _new_untracked_diff(self, paths: list[str], remaining: int) -> str:
        assert self.repo_root is not None
        chunks = []
        for path in paths:
            target = self.repo_root / path
            try:
                info = target.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > _FILE_DIFF_LIMIT:
                _append_warning(self.warnings, f"Diff omitted for non-regular or large file: {path}")
                continue
            result = self._git(
                "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--unified=3",
                "--", "/dev/null", path,
                check=False,
            )
            if result.returncode not in {0, 1}:
                _append_warning(self.warnings, f"Could not diff new untracked file: {path}")
                continue
            chunk = _decode(result.stdout)
            chunk, truncated = _truncate(chunk, remaining)
            chunks.append(chunk)
            remaining -= len(chunk.encode("utf-8", errors="replace"))
            if truncated or remaining <= 0:
                _append_warning(self.warnings, f"Diff truncated at {_REPORT_LIMIT} bytes")
                break
        return "".join(chunks)

    def _workspace_evidence(self) -> tuple[list[Dict[str, str]], str]:
        if self.repo_root is None or self._baseline_ref is None:
            return [], ""
        try:
            tracked = self._tracked_files()
            untracked, new_paths = self._untracked_changes()
            files = tracked + untracked
            files.sort(key=lambda item: (item["path"], item["status"]))
            if len(files) > _FILE_LIMIT:
                files = files[:_FILE_LIMIT]
                _append_warning(self.warnings, f"File list truncated at {_FILE_LIMIT} entries")
            tracked_paths = {item["path"] for item in files if item["status"] != "??"}
            tracked_for_diff = [item for item in tracked if item["path"] in tracked_paths]
            diff = self._tracked_diff(tracked_for_diff)
            remaining = max(0, _REPORT_LIMIT - len(diff.encode("utf-8", errors="replace")))
            included_new = [
                path for path in new_paths
                if any(item["path"] == path and item["status"] == "??" for item in files)
            ]
            if remaining and included_new:
                diff += self._new_untracked_diff(included_new, remaining)
            return files, diff
        except subprocess.TimeoutExpired:
            self._fail("Git final evidence collection timed out")
        except Exception as exc:
            self._fail(f"Git final evidence collection failed: {type(exc).__name__}: {exc}")
        return [], ""

    def _public_commands(self) -> list[Dict[str, Any]]:
        commands = []
        for source in self._commands:
            item = {key: value for key, value in source.items() if not key.startswith("_")}
            if not source.get("_has_result"):
                _append_warning(
                    self.warnings,
                    f"Bash command {source['tool_use_id']} has no tool_result; result is unknown",
                )
            commands.append(item)
        return commands

    @staticmethod
    def _validation(commands: list[Dict[str, Any]]) -> str:
        verification = [item for item in commands if item.get("_purpose") == "verification"]
        if not verification:
            return "unknown"
        latest = verification[-1]
        if (
            (latest.get("exit_code") is not None and latest.get("exit_code") != 0)
            or latest.get("timed_out") is True
            or latest.get("cancelled") is True
        ):
            return "failed"
        if latest.get("exit_code") is None:
            return "unknown"
        return "passed"

    def _atomic_report_write(self, report: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.report_path.with_name(f".{self.report_path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(to_jsonable(report), ensure_ascii=False, indent=2)
        try:
            with temp.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.report_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def finalize(self, outcome: str) -> Dict[str, Any]:
        with self._lock:
            files, diff = self._workspace_evidence()
            commands = self._public_commands()
            if self._critical_failure and outcome == "completed":
                outcome = "incomplete"
            report = {
                "run_id": self.run_id,
                "session_id": self.session_id,
                "outcome": outcome,
                "started_at": self.started_at,
                "finished_at": time.time(),
                "base_sha": self.base_sha,
                "files": files,
                "diff": diff,
                "commands": commands,
                "usage": dict(self._usage),
                "warnings": list(self.warnings),
                "validation": self._validation(self._commands),
            }
            try:
                self._atomic_report_write(report)
            except (OSError, TypeError, ValueError) as exc:
                self._fail(f"Report persistence failed: {type(exc).__name__}: {exc}")
                if report["outcome"] == "completed":
                    report["outcome"] = "incomplete"
                report["warnings"] = list(self.warnings)
            return report

    def revise(
        self,
        report: Dict[str, Any],
        warning: str,
        *,
        mark_incomplete: bool = True,
    ) -> Dict[str, Any]:
        with self._lock:
            revised = dict(to_jsonable(report))
            warnings = list(revised.get("warnings", []))
            _append_warning(warnings, warning)
            revised["warnings"] = warnings
            if mark_incomplete and revised.get("outcome") == "completed":
                revised["outcome"] = "incomplete"
            try:
                self._atomic_report_write(revised)
            except (OSError, TypeError, ValueError) as exc:
                _append_warning(
                    revised["warnings"],
                    f"Report persistence failed: {type(exc).__name__}: {exc}",
                )
                if revised.get("outcome") == "completed":
                    revised["outcome"] = "incomplete"
            return revised

    def save(self, report: Dict[str, Any]) -> None:
        """Persist a report enriched by an external review of the same run."""
        with self._lock:
            self._atomic_report_write(report)
