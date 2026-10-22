"""工具审批逻辑与事件推送回调。"""
import re
import threading
import time

import api_server
from components.session_store import to_jsonable

# 工具危险级别
_SAFE_TOOLS = {"read_file", "list_files", "glob", "grep", "TodoWrite", "memory_recall", "memory_store"}
_WRITE_TOOLS = {"write_file", "edit_file", "task", "safety_checkpoint"}
_COMMAND_TOOLS = {"bash", "background_run"}

# 高危命令模式（即使在 auto 模式下也需要审批）
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b", r"\brm\s+--recursive\b", r"\brmdir\b",
    r"\bsudo\b", r"\bchmod\s+.*777\b", r"\bchmod\s+-R\b",
    r"\bgit\s+push\s+.*(--force|-f)\b", r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b", r"\bdd\s+if=\b", r"\bmkfs\b",
    r">\s*/dev/", r"\bchown\b", r"\bkill\s+-9\b",
    r"\bshutdown\b", r"\breboot\b", r"\bdocker\s+rm\b",
    r"\bdocker\s+rmi\b", r"\bdocker\s+system\s+prune\b",
    r"\bDROP\s+TABLE\b", r"\bDELETE\s+FROM\b", r"\bTRUNCATE\b",
    r"\bformat\s+C:\b", r"\bfdisk\b", r"\bparted\b",
]


def _is_dangerous_command(cmd: str) -> bool:
    """检查 bash 命令是否包含高危操作"""
    cmd_lower = cmd.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_lower):
            return True
    return False


def _should_approve(tool_name: str, tool_input: dict = None, mode: str = None) -> bool:
    """根据当前模式和工具类型判断是否需要审批"""
    if tool_name in _SAFE_TOOLS:
        return False  # 安全工具永远不需要审批

    # bash / background_run 检查是否存在危险命令
    if tool_name in _COMMAND_TOOLS:
        cmd = (tool_input or {}).get("command", "")
        if _is_dangerous_command(cmd):
            return True  # 高危命令始终需要审批

    selected_mode = mode or api_server._current_mode
    if selected_mode == "auto":
        return False  # Auto 模式：非高危操作全自动
    if selected_mode == "plan":
        return False  # Plan 执行阶段：非高危操作全自动

    # Manual 模式：写操作和命令都需要审批
    if selected_mode == "manual":
        return tool_name in _WRITE_TOOLS or tool_name in _COMMAND_TOOLS

    return False


def _push_event(event: dict):
    """Fallback callback used before a run-specific callback is installed."""
    with api_server._lock:
        context = api_server._running_ctx
    if context:
        _push_run_event(context, event)
        return
    api_server.socketio.emit("agent_event", event)


def _push_run_event(context, event: dict):
    """Attach immutable run ownership and a monotonic session sequence."""
    owned = dict(to_jsonable(event))
    with api_server._lock:
        if owned.get("type") == "run_end":
            context.pending_run_end = owned
            return
        context.event_seq += 1
        owned["session_id"] = context.session_id
        owned["run_id"] = context.run_id
        owned["event_seq"] = context.event_seq
        context.events.append(owned)
    api_server.socketio.emit("agent_event", owned)


def _emit_request_error(message: str, session_id: str = None):
    from api.session_helpers import _resolve_session
    sid = session_id or api_server._active_session_id
    with api_server._lock:
        if api_server._running_ctx and api_server._running_ctx.session_id == sid:
            context = api_server._running_ctx
        else:
            context = None
            record, store = _resolve_session(sid)
            seq = int(record.get("event_seq", 0)) + 1 if record else 1
            if record:
                record["event_seq"] = seq
                try:
                    store.save(record)
                except Exception:
                    api_server.app.logger.exception("Failed to persist request error sequence")
    if context:
        _push_run_event(context, {"type": "error", "message": message, "ts": time.time()})
        return
    api_server.socketio.emit("agent_event", {
        "type": "error",
        "message": message,
        "session_id": sid,
        "run_id": None,
        "event_seq": seq,
        "ts": time.time(),
    })


def _approval_callback_for(context):
    def callback(tool_use_id: str, name: str, inp: dict) -> bool:
        if not _should_approve(name, inp, context.mode):
            return True

        event = threading.Event()
        result = [False]
        with api_server._approval_lock:
            api_server._pending_approvals[tool_use_id] = {
                "event": event,
                "result": result,
                "session_id": context.session_id,
                "run_id": context.run_id,
            }
        _push_run_event(context, {
            "type": "tool_approval_request",
            "name": name,
            "input": inp,
            "tool_use_id": tool_use_id,
            "ts": time.time(),
        })

        deadline = time.time() + api_server._approval_timeout_seconds
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0)
            if event.wait(timeout=min(0.1, remaining)):
                break
            if context.agent._interrupt_flag.is_set():
                result[0] = False
                break

        with api_server._approval_lock:
            api_server._pending_approvals.pop(tool_use_id, None)
        return result[0]

    return callback


def _approval_callback(tool_use_id: str, name: str, inp: dict) -> bool:
    """Compatibility callback; normal runs install a context-bound callback."""
    with api_server._lock:
        context = api_server._running_ctx
    if not context:
        return False
    return _approval_callback_for(context)(tool_use_id, name, inp)
