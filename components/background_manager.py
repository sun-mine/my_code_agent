import threading
import uuid
from pathlib import Path
from queue import Empty, Queue

from components.command_runner import CancellationToken, command_error, run_command


class BackgroundManager:
    def __init__(self, workdir: Path = None):
        self.workdir = Path(workdir or Path.cwd()).resolve()
        self.tasks = {}
        self.notifications = Queue()
        self._lock = threading.Lock()

    def run(
        self,
        command: str,
        timeout: int = 120,
        cancellation_token: CancellationToken = None,
        run_id: str = None,
    ) -> str:
        tid = str(uuid.uuid4())[:8]
        token = cancellation_token.child() if cancellation_token else CancellationToken()
        task = {
            "status": "running", "command": command, "result": None,
            "run_id": run_id, "token": token,
        }
        thread = threading.Thread(
            target=self._exec,
            args=(tid, command, timeout, token, run_id),
            daemon=True,
        )
        task["thread"] = thread
        with self._lock:
            self.tasks[tid] = task
        thread.start()
        return f"Background task {tid} started: {command[:80]}"

    def _exec(self, tid, command, timeout, token, run_id):
        try:
            result = run_command(
                command, cwd=self.workdir, timeout=timeout,
                cancellation_token=token,
            )
        except Exception as exc:
            result = command_error(f"Error: {exc}")
        finally:
            token.close()

        if getattr(result, "cancelled", False):
            status = "cancelled"
        elif getattr(result, "timed_out", False):
            status = "timed_out"
        elif result.is_error:
            status = "error"
        else:
            status = "completed"
        with self._lock:
            self.tasks[tid].update({"status": status, "result": result})
        self.notifications.put({
            "task_id": tid, "run_id": run_id, "status": status,
            "result": result.model_content(), **result.event_fields(),
        })

    def cancel(self, tid: str = None) -> str:
        with self._lock:
            tasks = [(tid, self.tasks.get(tid))] if tid else list(self.tasks.items())
            targets = [
                (task_id, task) for task_id, task in tasks
                if task and task["status"] in {"running", "cancelling"}
            ]
            for _, task in targets:
                task["status"] = "cancelling"
        if tid and not tasks[0][1]:
            return f"Unknown: {tid}"
        for _, task in targets:
            task["token"].cancel()
        for _, task in targets:
            task["thread"].join(timeout=5)
        return f"Cancelled {len(targets)} background task(s)"

    def check(self, tid: str = None) -> str:
        with self._lock:
            if tid:
                task = self.tasks.get(tid)
                if not task:
                    return f"Unknown: {tid}"
                return f"[{task['status']}] {task.get('result') or '(running)'}"
            snapshot = list(self.tasks.items())
        return "\n".join(
            f"{task_id}: [{task['status']}] {task['command'][:60]}"
            for task_id, task in snapshot
        ) or "No bg tasks"

    def drain(self, run_id: str = None) -> list:
        matched = []
        while True:
            try:
                notification = self.notifications.get_nowait()
            except Empty:
                break
            if notification.get("run_id") == run_id:
                matched.append(notification)
        return matched
