"""Socket.IO 事件处理。"""
import time

from flask_socketio import emit

import api_server
from api.agent_core import ensure_agent_initialized
from api.callbacks import _emit_request_error
from api.run_lifecycle import _start_run, interrupt_owned_run
from api.session_helpers import _session_snapshot, _chat_content


def register(socketio):
    @socketio.on("connect")
    def handle_connect():
        emit("connected", {"message": "Connected to agent", "ts": time.time()})

    @socketio.on("send_message")
    def handle_ws_message(data):
        data = data or {}
        try:
            message, title = _chat_content(data.get("message"))
        except ValueError as exc:
            _emit_request_error(str(exc), data.get("session_id"))
            return {"success": False, "error": str(exc)}
        try:
            ensure_agent_initialized()
        except Exception as exc:
            _emit_request_error(f"Agent initialization failed: {exc}", data.get("session_id"))
            return {"success": False, "error": str(exc)}
        new_mode = data.get("mode", "auto")
        result, error, _status = _start_run(
            data.get("session_id"),
            message,
            new_mode,
            plan_mode=(new_mode == "plan"),
            title_message=title,
            request_id=data.get("request_id"),
        )
        if error:
            _emit_request_error(error, data.get("session_id"))
            return {"success": False, "error": error, "status": _status}
        if result["status"] == "started":
            snapshot = _session_snapshot(result["session_id"])
            emit("run_started", {
                **result,
                "event_seq": snapshot["event_seq"] if snapshot else 0,
            })
        return {"success": True, "data": result}

    @socketio.on("interrupt")
    def handle_interrupt(data=None):
        data = data or {}
        if not data.get("session_id") or not data.get("run_id"):
            return {"success": False, "error": "session_id and run_id required", "status": 400}
        with api_server._lock:
            context = api_server._running_ctx
            if context is None:
                _emit_request_error("agent is not running", data.get("session_id"))
                return
            if data.get("session_id") and data["session_id"] != context.session_id:
                _emit_request_error("session does not own the running agent", data.get("session_id"))
                return
            if data.get("run_id") and data["run_id"] != context.run_id:
                _emit_request_error("run does not match the running agent", context.session_id)
                return
            if not interrupt_owned_run(context):
                return {"success": False, "error": "agent run is already finishing", "status": 409}
            emit("interrupt_requested", {
                "session_id": context.session_id,
                "run_id": context.run_id,
                "event_seq": context.event_seq,
                "ts": time.time(),
            })
