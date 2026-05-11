import json, time
from pathlib import Path

class EventBus:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def emit(self, event, task=None, worktree=None, error=None):
        record = {"event": event, "ts": time.time(), "task": task or {}, "worktree": worktree or {}}
        if error:
            record["error"] = error
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def list_recent(self, limit=20):
        lines = self.path.read_text().splitlines()[-limit:]
        return json.dumps([json.loads(l) for l in lines], indent=2)