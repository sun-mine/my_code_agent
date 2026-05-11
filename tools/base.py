import subprocess
import os
import fnmatch
from pathlib import Path

CATEGORY = "core"

_workdir = Path.cwd()


def get_workdir() -> Path:
    return _workdir


def set_workdir(path):
    global _workdir
    _workdir = Path(path).resolve()


def safe_path(p: str) -> Path:
    path = (_workdir / p).resolve()
    try:
        path.relative_to(_workdir)
    except ValueError:
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo ", "shutdown", "reboot",
                 "mkfs", "dd if=", ":(){ :|:& };:", "chmod 777 /"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=str(_workdir),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_list_files(path: str = ".", depth: int = 2) -> str:
    try:
        target = safe_path(path) if path != "." else _workdir
        if not target.exists():
            return f"Error: path not found: {path}"
        lines = []
        prefix_len = len(str(_workdir)) + 1
        for root, dirs, files in os.walk(str(target)):
            rel = str(root)[prefix_len:] or "."
            level = rel.count(os.sep)
            if level >= depth:
                dirs.clear()
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(files):
                if not f.startswith("."):
                    lines.append(os.path.join(rel, f))
        return "\n".join(lines[:500]) if lines else "(empty directory)"
    except Exception as e:
        return f"Error: {e}"

def run_glob(pattern: str, path: str = ".") -> str:
    try:
        base = safe_path(path) if path != "." else _workdir
        matches = []
        prefix_len = len(str(_workdir)) + 1
        for root, dirs, files in os.walk(str(base)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not name.startswith(".") and fnmatch.fnmatch(name, pattern):
                    rel = str(Path(root))[prefix_len:] or "."
                    matches.append(os.path.join(rel, name))
        return "\n".join(matches[:200]) if matches else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

def run_grep(pattern: str, path: str = ".", glob: str = None) -> str:
    try:
        import re
        base = safe_path(path) if path != "." else _workdir
        regex = re.compile(pattern)
        results = []
        prefix_len = len(str(_workdir)) + 1
        for root, dirs, files in os.walk(str(base)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in sorted(files):
                if name.startswith("."):
                    continue
                if glob and not fnmatch.fnmatch(name, glob):
                    continue
                fpath = Path(root) / name
                if fpath.stat().st_size > 500 * 1024:
                    continue
                try:
                    for i, line in enumerate(fpath.read_text(errors="replace").splitlines(), 1):
                        if regex.search(line):
                            rel_path = str(fpath)[prefix_len:] or name
                            results.append(f"{rel_path}:{i}: {line.strip()[:200]}")
                            if len(results) >= 100:
                                break
                    if len(results) >= 100:
                        break
                except Exception:
                    continue
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

BASE_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Edit file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "list_files", "description": "List files in a directory.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}}}},
    {"name": "glob", "description": "Find files matching a pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "grep", "description": "Search file contents with a regex pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "load_tools", "description": "Load additional tool categories on demand. Use when you need tools not in the core set. Categories: task, memory, safety, team, worktree, system (compress/skill/background). Can load multiple separated by comma.",
     "input_schema": {"type": "object", "properties": {"categories": {"type": "string", "description": "Comma-separated categories: task,memory,safety,team,worktree,system"}}, "required": ["categories"]}},
]

BASE_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "list_files": lambda **kw: run_list_files(kw.get("path", "."), kw.get("depth", 2)),
    "glob": lambda **kw: run_glob(kw["pattern"], kw.get("path", ".")),
    "grep": lambda **kw: run_grep(kw["pattern"], kw.get("path", "."), kw.get("glob")),
}

def get_tools():
    return BASE_TOOLS

def create_handlers(**ctx):
    return dict(BASE_HANDLERS)