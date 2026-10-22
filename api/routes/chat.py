"""聊天、计划执行、中断与工具审批路由。"""
from flask import Blueprint, request

import api_server
from api.agent_core import _require_agent
from api.session_helpers import _ok, _err
from api.run_lifecycle import _start_run

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return _err("message required")
    new_mode = data.get("mode", "auto")
    result, error, status = _start_run(
        data.get("session_id"),
        message,
        new_mode,
        plan_mode=(new_mode == "plan"),
        title_message=message,
    )
    if error:
        return _err(error, status)
    return _ok(result)


@chat_bp.route("/api/plan/execute", methods=["POST"])
def plan_execute():
    """后端处理计划批准：注入执行指令后切换到 auto 模式运行"""
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    plan_content = (data.get("plan_content") or "").strip()
    todos = data.get("todos") or []
    if not plan_content:
        return _err("plan_content required")

    # 注入后端指令，不作为用户消息
    todo_lines = "\n".join(
        f"- [{t['status']}] {t['content']}" for t in todos
    ) if todos else "(无)"
    execute_instruction = (
        f"<plan-execute>\n"
        f"用户已批准计划，现在切换到执行模式。\n\n"
        f"执行规则：\n"
        f"1. 按计划步骤顺序执行，每开始一个任务用 TodoWrite 把状态改为 in_progress\n"
        f"2. 每完成一个任务用 TodoWrite 把状态改为 completed\n"
        f"3. 遇到错误及时报告，不要跳过\n\n"
        f"当前任务列表（从 plan 继承）：\n{todo_lines}\n\n"
        f"已批准的计划：\n{plan_content}\n"
        f"</plan-execute>"
    )
    result, error, status = _start_run(
        data.get("session_id"), execute_instruction, "auto", plan_mode=False
    )
    if error:
        return _err(error, status)
    result["status"] = "executing"
    return _ok(result)


@chat_bp.route("/api/chat/interrupt", methods=["POST"])
def interrupt_chat():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    with api_server._lock:
        context = api_server._running_ctx
        if context is None:
            return _err("agent is not running", 409)
        requested_session = data.get("session_id")
        requested_run = data.get("run_id")
        if requested_session and requested_session != context.session_id:
            return _err("session does not own the running agent", 409)
        if requested_run and requested_run != context.run_id:
            return _err("run does not match the running agent", 409)
        context.agent.interrupt()
        return _ok({
            "interrupted": True,
            "session_id": context.session_id,
            "run_id": context.run_id,
        })


@chat_bp.route("/api/chat/tool_approve", methods=["POST"])
def tool_approve():
    """批准或拒绝一个工具调用"""
    data = request.get_json(force=True)
    tool_use_id = (data.get("tool_use_id") or "").strip()
    approved = data.get("approved") is True
    if not tool_use_id:
        return _err("tool_use_id required")
    with api_server._approval_lock:
        entry = api_server._pending_approvals.get(tool_use_id)
    if entry:
        requested_session = data.get("session_id")
        requested_run = data.get("run_id")
        if requested_session and requested_session != entry["session_id"]:
            return _err("session does not own this approval", 409)
        if requested_run and requested_run != entry["run_id"]:
            return _err("run does not own this approval", 409)
        event, result = entry["event"], entry["result"]
        result[0] = approved
        event.set()
        return _ok({
            "tool_use_id": tool_use_id,
            "approved": approved,
            "session_id": entry["session_id"],
            "run_id": entry["run_id"],
        })
    return _err("no pending approval found", 404)
