"""文件系统与文件内容路由。"""
import difflib
import os
import subprocess
from pathlib import Path

from flask import Blueprint, request

import api_server
from api.agent_core import _require_agent
from api.session_helpers import _ok, _err

files_bp = Blueprint("files", __name__)


@files_bp.route("/api/files/upload", methods=["POST"])
def upload_file():
    r = _require_agent()
    if r:
        return r
    if 'file' not in request.files:
        return _err("file required")
    file = request.files['file']
    if not file.filename:
        return _err("no file selected")
    root = Path(api_server.agent_config["workdir"])
    dest = root / file.filename
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(dest))
        return _ok({"path": str(dest.relative_to(root)), "size": dest.stat().st_size})
    except Exception as e:
        return _err(str(e))


@files_bp.route("/api/fs/ls")
def fs_ls():
    """列出服务器端任意目录（用于文件夹选择器）"""
    path = request.args.get("path", "").strip() or os.path.expanduser("~")
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return _err("directory not found", 404)
    exclude = {"__pycache__", ".git", "node_modules"}
    items = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if item.name.startswith(".") or item.name in exclude:
                continue
            if item.is_dir():
                items.append({"name": item.name, "path": str(item)})
    except PermissionError:
        pass
    parent = str(target.parent) if target.parent != target else None
    return _ok({"path": str(target), "parent": parent, "items": items})


@files_bp.route("/api/fs/mkdir", methods=["POST"])
def fs_mkdir():
    """Create a child directory from the folder picker."""
    data = request.get_json(force=True) or {}
    parent = Path((data.get("path") or "").strip()).expanduser().resolve()
    name = (data.get("name") or "").strip()
    if not parent.is_dir():
        return _err("当前目录不存在")
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name or "\x00" in name:
        return _err("请输入有效的文件夹名称")
    target = parent / name
    try:
        target.mkdir()
    except FileExistsError:
        return _err("同名文件夹已存在")
    except OSError as e:
        return _err(str(e))
    return _ok({"path": str(target.resolve()), "name": name})


@files_bp.route("/api/fs/touch", methods=["POST"])
def fs_touch():
    """Create an empty file from the folder picker without overwriting."""
    data = request.get_json(force=True) or {}
    parent = Path((data.get("path") or "").strip()).expanduser().resolve()
    name = (data.get("name") or "").strip()
    if not parent.is_dir():
        return _err("当前目录不存在")
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name or "\x00" in name:
        return _err("请输入有效的文件名")
    target = parent / name
    try:
        with target.open("x", encoding="utf-8"):
            pass
    except FileExistsError:
        return _err("同名文件或文件夹已存在")
    except OSError as e:
        return _err(str(e))
    return _ok({"path": str(target.resolve()), "name": name})


@files_bp.route("/api/files/tree")
def file_tree():
    r = _require_agent()
    if r:
        return r
    root = Path(api_server.agent_config["workdir"])
    max_depth = int(request.args.get("depth", 3))
    start_path = request.args.get("path", "").strip()
    exclude = {".git", "__pycache__", "node_modules", ".agent",
               "dist", ".venv"}

    def _walk(path: Path, depth: int):
        if depth == 0:
            return None
        try:
            children = []
            for item in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name)):
                if item.name in exclude or item.name.startswith("."):
                    continue
                if item.is_dir():
                    node = _walk(item, depth - 1)
                    if node:
                        children.append(node)
                else:
                    children.append({
                        "name": item.name,
                        "type": "file",
                        "path": str(item.relative_to(root)),
                        "size": item.stat().st_size,
                    })
            return {"name": path.name, "type": "dir",
                    "path": str(path.relative_to(root)), "children": children}
        except PermissionError:
            return None

    if start_path:
        start_dir = (root / start_path).resolve()
        try:
            start_dir.relative_to(root)
        except ValueError:
            return _err("invalid path", 403)
        if not start_dir.exists() or not start_dir.is_dir():
            return _err("directory not found", 404)
        tree = _walk(start_dir, max_depth)
    else:
        tree = _walk(root, max_depth)

    return _ok({"tree": tree})


@files_bp.route("/api/files/list")
def list_directory():
    """列出指定目录的直接子项（用于按需加载文件树）"""
    r = _require_agent()
    if r:
        return r
    rel = request.args.get("path", "").strip()
    root = Path(api_server.agent_config["workdir"])
    if rel:
        target = (root / rel).resolve()
    else:
        target = root
    try:
        target.relative_to(root)
    except ValueError:
        return _err("invalid path", 403)
    if not target.exists() or not target.is_dir():
        return _err("directory not found", 404)

    exclude = {".git", "__pycache__", "node_modules", ".agent",
               "dist", ".venv"}
    items = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if item.name in exclude or item.name.startswith("."):
                continue
            entry = {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "path": str(item.relative_to(root)),
            }
            if item.is_file():
                entry["size"] = item.stat().st_size
            items.append(entry)
    except PermissionError:
        pass

    return _ok({"items": items, "path": rel or str(root.relative_to(root))})


@files_bp.route("/api/files/content")
def file_content():
    r = _require_agent()
    if r:
        return r
    rel = request.args.get("path", "")
    if not rel:
        return _err("path required")
    root = Path(api_server.agent_config["workdir"])
    target = (root / rel).resolve()
    # 安全：防止目录穿越
    try:
        target.relative_to(root)
    except ValueError:
        return _err("invalid path", 403)
    if not target.exists() or not target.is_file():
        return _err("file not found", 404)
    if target.stat().st_size > 500 * 1024:
        return _err("file too large (>500KB)", 413)
    try:
        content = target.read_text(errors="replace")
    except Exception as e:
        return _err(str(e))
    ext = target.suffix.lstrip(".")
    return _ok({"path": rel, "content": content, "ext": ext})


@files_bp.route("/api/files/content", methods=["PUT"])
def save_file_content():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    rel = (data.get("path") or "").strip()
    new_content = data.get("content", "")
    if not rel:
        return _err("path required")
    root = Path(api_server.agent_config["workdir"])
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return _err("invalid path", 403)
    if not target.exists():
        return _err("file not found", 404)
    if target.stat().st_size > 2 * 1024 * 1024:
        return _err("file too large (>2MB) to edit via UI", 413)
    try:
        # 先备份再写
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_bytes(target.read_bytes())
        target.write_text(new_content, encoding="utf-8")
        backup.unlink(missing_ok=True)
        return _ok({"path": rel, "bytes_written": len(new_content.encode())})
    except Exception as e:
        return _err(str(e))


@files_bp.route("/api/files/diff", methods=["POST"])
def file_diff():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    rel = (data.get("path") or "").strip()
    if not rel:
        return _err("path required")
    root = Path(api_server.agent_config["workdir"])
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return _err("invalid path", 403)
    if not target.exists():
        return _err("file not found", 404)

    try:
        current = target.read_text(errors="replace")
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", rel],
            capture_output=True, text=True, cwd=str(root), timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _ok({"path": rel, "diff": result.stdout})
        diff_lines = difflib.unified_diff(
            [], current.splitlines(keepends=True),
            fromfile="/dev/null", tofile=rel,
        )
        return _ok({"path": rel, "diff": "".join(diff_lines)})
    except Exception as e:
        return _err(str(e))
