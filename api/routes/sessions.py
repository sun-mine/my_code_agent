"""会话与历史记录路由。"""
import os
from pathlib import Path

from flask import Blueprint, request

import api_server
from api.agent_core import _require_agent
from api.session_helpers import (
    _ok, _err, _resolve_session, _session_snapshot,
    _store_for_workdir, _store_key,
)
from components.session_store import SessionStore, to_jsonable

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.route("/api/history")
def get_history():
    r = _require_agent()
    if r:
        return r
    snapshot = _session_snapshot(request.args.get("session_id"))
    if snapshot is None:
        return _err("session not found", 404)
    return _ok(snapshot)


@sessions_bp.route("/api/history", methods=["DELETE"])
def clear_history():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    sid = data.get("session_id") or request.args.get("session_id") or api_server._active_session_id
    with api_server._lock:
        if api_server._running_ctx and api_server._running_ctx.session_id == sid:
            return _err("cannot clear a running session", 409)
        record, store = _resolve_session(sid, request.args.get("workdir"))
        if record is None:
            return _err("session not found", 404)
        try:
            record["history"] = []
            store.save(record)
        except Exception as exc:
            return _err(f"Failed to clear session: {exc}", 500)
        if sid == api_server._active_session_id:
            api_server.conversation_history = []
    return _ok({"session_id": sid})


@sessions_bp.route("/api/sessions")
def list_sessions():
    r = _require_agent()
    if r:
        return r
    with api_server._lock:
        workdir = request.args.get("workdir") or (api_server.agent_config or {}).get("workdir", os.getcwd())
        store = _store_for_workdir(workdir)
        running_sid = (
            api_server._running_ctx.session_id
            if api_server._running_ctx and api_server._running_ctx.store is store else None
        )
        records = store.list()
    sessions = [{
        "id": item["id"],
        "title": item.get("title", ""),
        "created_at": item.get("created_at", 0),
        "updated_at": item.get("updated_at", 0),
        "message_count": item.get("message_count", len(item.get("history", []))),
        "workdir": item.get("workdir", ""),
        "mode": item.get("mode", "auto"),
        "running": item["id"] == running_sid,
    } for item in records]
    return _ok({"sessions": sessions})


@sessions_bp.route("/api/sessions", methods=["POST"])
def create_session():
    """Legacy save endpoint; never rebind a running session's history."""
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    sid = data.get("id") or api_server._active_session_id or SessionStore.new_id()
    title = (data.get("title") or "").strip()
    with api_server._lock:
        record, store = _resolve_session(sid)
        if store is None:
            store = _store_for_workdir((api_server.agent_config or {}).get("workdir", os.getcwd()))
        try:
            if record is None:
                snapshot = _session_snapshot()
                record = store.create(
                    session_id=sid,
                    title=title,
                    created_at=data.get("created_at"),
                    workdir=(api_server.agent_config or {}).get("workdir", os.getcwd()),
                    mode=api_server._current_mode,
                    history=(snapshot or {}).get("history", []),
                )
            elif title:
                record["title"] = title[:80]
                record = store.save(record)
        except Exception as exc:
            return _err(f"Failed to save session: {exc}", 500)
    return _ok({"id": sid, "title": record.get("title", "")})


@sessions_bp.route("/api/sessions/new", methods=["POST"])
def new_session():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    requested_workdir = data.get("workdir") or (api_server.agent_config or {}).get("workdir", os.getcwd())
    target = Path(requested_workdir).expanduser().resolve()
    if not target.is_dir():
        return _err(f"directory not found: {target}", 404)
    mode = data.get("mode", "auto")
    if mode not in {"auto", "plan", "manual"}:
        mode = "auto"

    with api_server._lock:
        if api_server._running_ctx and str(target) != api_server._running_ctx.workdir:
            return _err("cannot switch workspace while agent is running", 409)
        try:
            store = _store_for_workdir(str(target))
            record = store.create(workdir=str(target), mode=mode)
            if api_server._running_ctx is None and str(target) != (api_server.agent_config or {}).get("workdir", ""):
                api_server.ensure_agent_initialized(workdir=str(target), session_id=record["id"])
            else:
                api_server._active_session_id = record["id"]
                api_server._active_store_key = _store_key(str(target))
                api_server._session_store = store
                api_server.conversation_history = []
                if api_server._running_ctx is None:
                    api_server._current_mode = mode
        except Exception as exc:
            if 'record' in locals():
                store.delete(record["id"])
            api_server.app.logger.exception("Failed to create session")
            return _err(f"Failed to create session: {exc}", 500)
    return _ok(_session_snapshot(record["id"]))


@sessions_bp.route("/api/sessions/<sid>")
def get_session(sid):
    r = _require_agent()
    if r:
        return r
    record, _store = _resolve_session(sid, request.args.get("workdir"))
    if record is None:
        return _err("session not found", 404)
    return _ok(to_jsonable(record))


@sessions_bp.route("/api/sessions/<sid>", methods=["DELETE"])
def delete_session(sid):
    r = _require_agent()
    if r:
        return r
    with api_server._lock:
        if api_server._running_ctx and api_server._running_ctx.session_id == sid:
            return _err("cannot delete a running session", 409)
        record, store = _resolve_session(sid, request.args.get("workdir"))
        if record is None:
            return _err("session not found", 404)
        try:
            store.delete(sid)
            if api_server._active_session_id == sid and api_server._active_store_key == _store_key(record["workdir"]):
                replacement = store.create(
                    workdir=record.get("workdir") or (api_server.agent_config or {}).get("workdir", os.getcwd()),
                    mode=record.get("mode", "auto"),
                )
                api_server._active_session_id = replacement["id"]
                api_server._active_store_key = _store_key(record["workdir"])
                api_server._session_store = store
                api_server.conversation_history = []
        except Exception as exc:
            return _err(f"Failed to delete session: {exc}", 500)
    return _ok({"deleted": sid, "session_id": api_server._active_session_id})


@sessions_bp.route("/api/sessions/<sid>/load", methods=["POST"])
def load_session(sid):
    """Select a session without mutating a different session's active run."""
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    requested_workdir = data.get("workdir") or request.args.get("workdir")
    with api_server._lock:
        record, store = _resolve_session(sid, requested_workdir)
        if record is None:
            return _err("session not found", 404)
        workdir = record.get("workdir", "")
        if api_server._running_ctx and workdir != api_server._running_ctx.workdir:
            return _err("cannot switch workspace while agent is running", 409)
        try:
            if api_server._running_ctx is None and workdir != (api_server.agent_config or {}).get("workdir", ""):
                p = Path(workdir).expanduser().resolve()
                if not p.is_dir():
                    return _err(f"directory not found: {p}", 404)
                api_server.ensure_agent_initialized(workdir=str(p), session_id=sid)
            else:
                api_server._active_session_id = sid
                api_server._active_store_key = _store_key(workdir)
                api_server._session_store = store
                api_server.conversation_history = list(record.get("history", []))
                if api_server._running_ctx is None:
                    api_server._current_mode = record.get("mode", "auto")
                    if api_server.todo_manager:
                        api_server.todo_manager.items.clear()
        except Exception as exc:
            api_server.app.logger.exception("Failed to load session")
            return _err(f"Failed to load session: {exc}", 500)
    return _ok(_session_snapshot(sid))
