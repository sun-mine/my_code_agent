import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional


class ToolResult(str):
    """String-compatible tool output with structured result metadata."""

    def __new__(cls, content: str, *, is_error: bool = False, **details):
        obj = super().__new__(cls, content)
        obj.is_error = is_error
        obj.details = details
        for key, value in details.items():
            setattr(obj, key, value)
        return obj

    def model_content(self) -> str:
        if not self.details:
            return str(self)
        return json.dumps(self.details, ensure_ascii=False)

    def event_fields(self) -> Dict:
        return {"is_error": self.is_error, **self.details}


def command_error(message: str, *, cancelled: bool = False) -> ToolResult:
    return ToolResult(
        message,
        is_error=True,
        exit_code=None,
        stdout="",
        stderr=message,
        timed_out=False,
        cancelled=cancelled,
        duration=0.0,
    )


def _group_exists(process_group_id: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(process_group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_termination_lock_guard = threading.Lock()


def terminate_process_tree(process: subprocess.Popen, grace: float = 0.5):
    # The run deadline and communicate(timeout) can cancel the same process.
    with _termination_lock_guard:
        lock = getattr(process, "_agent_termination_lock", None)
        if lock is None:
            lock = threading.Lock()
            process._agent_termination_lock = lock
    with lock:
        _terminate_process_tree(process, grace)


def _terminate_process_tree(process: subprocess.Popen, grace: float):
    """Stop only the process group created for this command."""
    if os.name == "nt":
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
        return

    process_group_id = process.pid  # start_new_session=True makes pid == pgid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        # Another cancellation/timeout may already have reaped this group.
        if process.poll() is not None:
            return
        raise
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if _group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


class CancellationToken:
    """A cancellation scope owning only its registered processes and children."""

    def __init__(self, parent=None):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._processes = set()
        self._closers = set()
        self._children = set()
        self._parent = parent

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def child(self):
        child = CancellationToken(parent=self)
        with self._lock:
            self._children.add(child)
            cancelled = self._event.is_set()
        if cancelled:
            child.cancel()
        return child

    def close(self):
        if self._parent:
            with self._parent._lock:
                self._parent._children.discard(self)
            self._parent = None

    def register(self, process: subprocess.Popen):
        with self._lock:
            self._processes.add(process)
            cancelled = self._event.is_set()
        if cancelled:
            terminate_process_tree(process)

    def unregister(self, process: subprocess.Popen):
        with self._lock:
            self._processes.discard(process)

    def register_closer(self, closer):
        with self._lock:
            cancelled = self._event.is_set()
            if not cancelled:
                self._closers.add(closer)
        if cancelled:
            self._close(closer)
        return closer

    def unregister_closer(self, closer):
        with self._lock:
            self._closers.discard(closer)

    @staticmethod
    def _close(closer):
        try:
            closer()
        except Exception:
            pass

    def cancel(self):
        self._event.set()
        with self._lock:
            processes = list(self._processes)
            closers = list(self._closers)
            children = list(self._children)
        for closer in closers:
            self._close(closer)
        for child in children:
            child.cancel()
        for process in processes:
            terminate_process_tree(process)


def run_command(
    command: str,
    *,
    cwd: Path,
    timeout: Optional[float] = 120,
    cancellation_token: Optional[CancellationToken] = None,
    output_limit: int = 50000,
) -> ToolResult:
    token = cancellation_token or CancellationToken()
    started = time.monotonic()
    if token.is_cancelled:
        return command_error("Error: Command cancelled", cancelled=True)

    popen_options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
    }
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_options,
    )
    token.register(process)
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_tree(process)
            stdout, stderr = process.communicate()
    finally:
        cancelled = token.is_cancelled
        token.unregister(process)

    stdout = (stdout or "")[:output_limit]
    stderr = (stderr or "")[:output_limit]
    duration = round(time.monotonic() - started, 3)
    visible = (stdout + stderr).strip() or "(no output)"
    if cancelled and visible == "(no output)":
        visible = "Error: Command cancelled"
    elif timed_out and visible == "(no output)":
        visible = f"Error: Timeout ({timeout}s)"
    return ToolResult(
        visible,
        is_error=process.returncode != 0 or timed_out or cancelled,
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        cancelled=cancelled,
        duration=duration,
    )
