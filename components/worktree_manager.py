import json, re, subprocess, time
from pathlib import Path
from components.event_bus import EventBus

class WorktreeManager:
    def __init__(self, repo_root: Path, tasks, events: EventBus):
        self.repo = repo_root
        self.tasks = tasks
        self.events = events
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}))
        self.git_ok = self._is_git()
        if not self.git_ok:
            print("Warning: not a git repo, worktree tools disabled.")

    def _is_git(self):
        try:
            subprocess.run(["git", "rev-parse"], cwd=self.repo, capture_output=True, timeout=5)
            return True
        except:
            return False

    def _git(self, args):
        if not self.git_ok:
            raise RuntimeError("Not a git repository")
        r = subprocess.run(["git"] + args, cwd=self.repo, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return (r.stdout + r.stderr).strip()

    def _load_idx(self):
        return json.loads(self.index_path.read_text())

    def _save_idx(self, data):
        self.index_path.write_text(json.dumps(data, indent=2))

    def _find(self, name):
        for w in self._load_idx().get("worktrees", []):
            if w["name"] == name:
                return w
        return None

    def create(self, name, task_id=None, base_ref="HEAD"):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name):
            raise ValueError("Invalid worktree name")
        if self._find(name):
            raise ValueError(f"Worktree '{name}' exists")
        path = self.dir / name
        branch = f"wt/{name}"
        self.events.emit("worktree.create.before", task=None, worktree={"name": name})
        try:
            self._git(["worktree", "add", "-b", branch, str(path), base_ref])
            entry = {"name": name, "path": str(path), "branch": branch, "task_id": task_id, "status": "active"}
            idx = self._load_idx()
            idx["worktrees"].append(entry)
            self._save_idx(idx)
            if task_id:
                self.tasks.bind_worktree(task_id, name)
            self.events.emit("worktree.create.after", worktree=entry)
            return json.dumps(entry, indent=2)
        except Exception as e:
            self.events.emit("worktree.create.failed", worktree={"name": name}, error=str(e))
            raise

    def list_all(self):
        idx = self._load_idx()
        wts = idx.get("worktrees", [])
        if not wts:
            return "No worktrees."
        lines = []
        for wt in wts:
            lines.append(f"[{wt.get('status')}] {wt['name']} -> {wt['path']} ({wt.get('branch')}) task={wt.get('task_id')}")
        return "\n".join(lines)

    def run(self, name, command):
        wt = self._find(name)
        if not wt:
            return f"Unknown worktree '{name}'"
        p = Path(wt["path"])
        if not p.exists():
            return f"Path missing: {p}"
        try:
            r = subprocess.run(command, shell=True, cwd=p, capture_output=True, text=True, timeout=300)
            return (r.stdout + r.stderr).strip()[:50000] or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout"

    def remove(self, name, force=False, complete_task=False):
        wt = self._find(name)
        if not wt:
            return f"Unknown worktree '{name}'"
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(wt["path"])
        self._git(args)
        if complete_task and wt.get("task_id"):
            self.tasks.update(wt["task_id"], status="completed")
        idx = self._load_idx()
        for w in idx["worktrees"]:
            if w["name"] == name:
                w["status"] = "removed"
        self._save_idx(idx)
        return f"Removed '{name}'"

    def keep(self, name):
        idx = self._load_idx()
        for w in idx["worktrees"]:
            if w["name"] == name:
                w["status"] = "kept"
        self._save_idx(idx)
        return f"Kept '{name}'"

    def status(self, name):
        wt = self._find(name)
        if not wt:
            return f"Unknown worktree '{name}'"
        r = subprocess.run(["git", "status", "--short"], cwd=wt["path"], capture_output=True, text=True)
        return r.stdout or "Clean"