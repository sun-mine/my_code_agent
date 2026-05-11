CATEGORY = "worktree"
import json
from components.worktree_manager import WorktreeManager
from components.event_bus import EventBus

_worktrees = None
_events = None

def init_worktrees(config, tasks):
    global _worktrees, _events
    repo = config.workdir
    # 尝试找到 git 根目录
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True)
        repo = Path(r.stdout.strip())
    except:
        pass
    _events = EventBus(repo / ".worktrees" / "events.jsonl")
    _worktrees = WorktreeManager(repo, tasks, _events)
    return _worktrees

def get_worktrees():
    return _worktrees

def get_events():
    return _events

WORKTREE_TOOLS = [
    {"name": "worktree_create", "description": "Create worktree.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "task_id": {"type": "integer"}, "base_ref": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_list", "description": "List worktrees.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "worktree_status", "description": "Show worktree status.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_run", "description": "Run command in worktree.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "command": {"type": "string"}}, "required": ["name", "command"]}},
    {"name": "worktree_keep", "description": "Keep worktree.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "worktree_remove", "description": "Remove worktree.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "worktree_events", "description": "List lifecycle events.", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
]

WORKTREE_HANDLERS = {
    "worktree_create": lambda **kw: get_worktrees().create(kw["name"], kw.get("task_id"), kw.get("base_ref", "HEAD")),
    "worktree_list": lambda **kw: get_worktrees().list_all(),
    "worktree_status": lambda **kw: get_worktrees().status(kw["name"]),
    "worktree_run": lambda **kw: get_worktrees().run(kw["name"], kw["command"]),
    "worktree_keep": lambda **kw: get_worktrees().keep(kw["name"]),
    "worktree_remove": lambda **kw: get_worktrees().remove(kw["name"], kw.get("force", False), kw.get("complete_task", False)),
    "worktree_events": lambda **kw: get_events().list_recent(kw.get("limit", 20)),
}

def get_tools():
    return WORKTREE_TOOLS

def create_handlers(**ctx):
    return WORKTREE_HANDLERS