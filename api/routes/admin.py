"""系统状态、工作目录、任务、团队、记忆、安全、计划、错误等管理路由。"""
import json
import time
from pathlib import Path

from flask import Blueprint, request

import api_server
from api.agent_core import _require_agent
from api.session_helpers import _ok, _err, _session_snapshot
from components.compactor import estimate_tokens

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/api/status")
def get_status():
    r = _require_agent()
    if r:
        return r
    snapshot = _session_snapshot()
    active_history = snapshot["history"] if snapshot else api_server.conversation_history
    # Token usage estimate
    estimated = estimate_tokens(active_history)
    threshold = api_server.agent_instance.token_threshold if api_server.agent_instance else 100000

    # Token breakdown by category
    system_len = api_server.agent_config.get("system_prompt_len", 0) if api_server.agent_config else 0
    tool_len = api_server.agent_config.get("tool_definitions_len", 0) if api_server.agent_config else 0
    # Recalculate tool tokens directly from tools for accuracy
    tool_count = 0
    if api_server.agent_instance:
        tools_json = json.dumps(api_server.agent_instance.tools, default=str)
        tool_len = len(tools_json)
        tool_count = len(api_server.agent_instance.tools)
    # Rough estimate: ~4 chars per token for prose, ~2 chars per token for JSON
    system_tokens = max(system_len // 4, 1) if system_len > 0 else 0
    # Tool definitions are dense JSON, so ~2 chars per token, plus ~50 tokens overhead per tool
    tool_tokens = max(tool_len // 2, tool_count * 50) if tool_len > 0 else 0
    messages_tokens = estimated
    user_context_tokens = 0
    total = messages_tokens + system_tokens + tool_tokens + user_context_tokens

    token_breakdown = {
        "estimated_tokens": total,
        "threshold": threshold,
        "usage_percent": min(round(total / threshold * 100), 100) if threshold > 0 else 0,
        "system_tokens": system_tokens,
        "tool_tokens": tool_tokens,
        "messages_tokens": messages_tokens,
        "user_context_tokens": user_context_tokens,
    }

    token_usage = {
        "estimated_tokens": total,
        "threshold": threshold,
        "usage_percent": token_breakdown["usage_percent"],
    }

    return _ok({
        "agent_state": api_server.agent_instance.state,
        "run_id": api_server._running_ctx.run_id if api_server._running_ctx else None,
        "session_id": api_server._active_session_id,
        "running_session_id": api_server._running_ctx.session_id if api_server._running_ctx else None,
        "running_run_id": api_server._running_ctx.run_id if api_server._running_ctx else None,
        "workdir": api_server.agent_config["workdir"] if api_server.agent_config else "",
        "model": api_server.agent_config["model"] if api_server.agent_config else "",
        "history_length": len(active_history),
        "token_usage": token_usage,
        "token_breakdown": token_breakdown,
        "memory_stats": api_server.memory_manager.get_memory_stats() if api_server.memory_manager else {},
        "safety_stats": api_server.safety_manager.get_safety_stats() if api_server.safety_manager else {},
    })


@admin_bp.route("/api/workdir")
def get_workdir():
    r = _require_agent()
    if r:
        return r
    return _ok({"workdir": api_server.agent_config["workdir"]})


@admin_bp.route("/api/workdir", methods=["POST"])
def change_workdir():
    """(重)初始化 agent 与工作目录"""
    data = request.get_json(force=True)
    new_dir = (data.get("workdir") or "").strip()
    if not new_dir:
        return _err("workdir required")
    p = Path(new_dir).expanduser().resolve()
    if not p.is_dir():
        return _err(f"directory not found: {p}", 404)
    with api_server._lock:
        if api_server._running_ctx is not None:
            return _err("agent is running, please interrupt first", 409)
        try:
            api_server.ensure_agent_initialized(workdir=str(p))
            return _ok({
                "workdir": str(p),
                "message": "re-initialized",
                "session_id": api_server._active_session_id,
            })
        except Exception as e:
            return _err(str(e), 500)


@admin_bp.route("/api/tasks")
def list_tasks():
    r = _require_agent()
    if r:
        return r
    tasks = []
    for f in sorted(api_server.task_manager.dir.glob("task_*.json")):
        try:
            tasks.append(json.loads(f.read_text()))
        except Exception:
            pass
    return _ok({"tasks": tasks})


@admin_bp.route("/api/tasks", methods=["POST"])
def create_task():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    result = api_server.task_manager.create(data.get("subject", ""), data.get("description", ""))
    return _ok(json.loads(result))


@admin_bp.route("/api/todos")
def list_todos():
    r = _require_agent()
    if r:
        return r
    return _ok({"todos": api_server.todo_manager.items})


@admin_bp.route("/api/team")
def list_team():
    r = _require_agent()
    if r:
        return r
    members = api_server.team_manager.team_config.get("members", [])
    return _ok({
        "team_name": api_server.team_manager.team_config.get("team_name", "default"),
        "members": members,
    })


@admin_bp.route("/api/memory/stats")
def memory_stats():
    r = _require_agent()
    if r:
        return r
    return _ok(api_server.memory_manager.get_memory_stats())


@admin_bp.route("/api/memory/search", methods=["POST"])
def memory_search():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    results = api_server.memory_manager.search_facts(
        data.get("category"), data.get("keyword"), data.get("limit", 10)
    )
    return _ok(results)


@admin_bp.route("/api/memory/store", methods=["POST"])
def memory_store():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    api_server.memory_manager.store_fact(
        data["category"], data["key"], data["value"], data.get("confidence", 1.0)
    )
    return _ok()


@admin_bp.route("/api/safety/checkpoints")
def list_checkpoints():
    r = _require_agent()
    if r:
        return r
    checkpoints = api_server.safety_manager.list_checkpoints(30)
    return _ok({"checkpoints": checkpoints})


@admin_bp.route("/api/safety/checkpoint", methods=["POST"])
def create_checkpoint():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    checkpoint_id = api_server.safety_manager.create_checkpoint(
        data.get("name", "manual"), data.get("description", "")
    )
    return _ok({"checkpoint_id": checkpoint_id})


@admin_bp.route("/api/safety/restore", methods=["POST"])
def restore_checkpoint():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    try:
        msg = api_server.safety_manager.restore_checkpoint(data["checkpoint_id"])
        return _ok({"message": msg})
    except Exception as e:
        return _err(str(e), 400)


@admin_bp.route("/api/safety/stats")
def safety_stats():
    r = _require_agent()
    if r:
        return r
    return _ok(api_server.safety_manager.get_safety_stats())


@admin_bp.route("/api/plan/save", methods=["POST"])
def save_plan():
    """将 plan 内容保存为 .md 文件"""
    data = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    title = (data.get("title") or "plan").strip()
    if not content:
        return _err("content required")
    if api_server.agent_config and api_server.agent_config.get("workdir"):
        p = Path(api_server.agent_config["workdir"]) / ".agent" / "plans"
    else:
        p = Path(".plans")
    p.mkdir(parents=True, exist_ok=True)
    plan_file = p / f"{title[:40].replace(' ', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.md"
    plan_file.write_text(content, encoding="utf-8")
    return _ok({"filename": plan_file.name, "path": str(plan_file)})


@admin_bp.route("/api/errors/recent")
def recent_errors():
    r = _require_agent()
    if r:
        return r
    limit = request.args.get("limit", 20, type=int)
    errors = api_server.error_manager.get_error_history(limit)
    return _ok(errors)
