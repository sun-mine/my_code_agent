"""响应封装、会话存储与快照工具。"""
import time
from pathlib import Path

from flask import jsonify

import api_server
from components.session_store import to_jsonable


def _ok(data=None):
    return jsonify({"success": True, "data": data, "ts": time.time()})


def _err(msg, code=400):
    return jsonify({"success": False, "error": str(msg), "ts": time.time()}), code


def _chat_content(value):
    """Validate a text or attachment message and derive its session title."""
    if isinstance(value, str) and value.strip():
        return value.strip(), value.strip()
    if isinstance(value, list) and value and all(
        isinstance(block, dict) and (
            block.get("type") == "text" and isinstance(block.get("text"), str)
            or block.get("type") == "image" and isinstance(block.get("source"), dict)
        ) for block in value
    ):
        title = " ".join(block["text"] for block in value if block["type"] == "text")
        if title.strip() or any(block["type"] == "image" for block in value):
            return value, title.strip() or "图片消息"
    raise ValueError("message required: expected text or text/image blocks")


def _store_key(workdir: str) -> str:
    return str(Path(workdir).expanduser().resolve())


def _store_for_workdir(workdir: str):
    from components.session_store import SessionStore
    key = _store_key(workdir)
    store = api_server._session_stores.get(key)
    if store is None:
        store = SessionStore(Path(key) / ".agent" / "sessions")
        api_server._session_stores[key] = store
    return store


def _resolve_session(session_id: str = None, workdir: str = None):
    sid = session_id or api_server._active_session_id
    if not sid:
        return None, None
    if workdir:
        store = _store_for_workdir(workdir)
        return store.get(sid), store
    if api_server._running_ctx and api_server._running_ctx.session_id == sid:
        return api_server._running_ctx.store.get(sid), api_server._running_ctx.store
    if sid == api_server._active_session_id and api_server._active_store_key:
        store = api_server._session_stores.get(api_server._active_store_key)
        if store:
            record = store.get(sid)
            if record is not None:
                return record, store
    for store in list(api_server._session_stores.values()):
        record = store.get(sid)
        if record is not None:
            return record, store
    return None, None


def _get_record(session_id: str = None, workdir: str = None):
    record, _store = _resolve_session(session_id, workdir)
    return record


def _make_title(message: str) -> str:
    return " ".join(message.split())[:80]


def _aligned_history_run_ids(history: list, values) -> list:
    if not isinstance(values, list):
        values = []
    return [
        values[index]
        if index < len(values) and isinstance(values[index], str)
        else None
        for index in range(len(history))
    ]


def _session_snapshot(session_id: str = None):
    with api_server._lock:
        sid = session_id or api_server._active_session_id
        record = _get_record(sid)
        if record is None:
            return None
        context = api_server._running_ctx if (
            api_server._running_ctx and api_server._running_ctx.session_id == sid
        ) else None
        if context:
            history = to_jsonable(context.baseline_history)
            history_run_ids = _aligned_history_run_ids(
                history, [owner for _message, owner in context.history_owners]
            )
            events = to_jsonable(context.events)
            event_seq = context.event_seq
            run_id = context.run_id
        else:
            history = to_jsonable(record.get("history", []))
            history_run_ids = _aligned_history_run_ids(
                history, record.get("history_run_ids", [])
            )
            events = []
            event_seq = int(record.get("event_seq", 0))
            run_id = None
        stored_reports = to_jsonable(record.get("delivery_reports", [])) or []
        delivery_reports = sorted(
            [item for item in stored_reports if isinstance(item, dict)],
            key=lambda item: (item.get("finished_at", 0), item.get("started_at", 0)),
        )[-5:]
        return {
            "session_id": sid,
            "history": history,
            "history_run_ids": history_run_ids,
            "total": len(history),
            "title": record.get("title", ""),
            "workdir": record.get("workdir", ""),
            "mode": record.get("mode", "auto"),
            "running": context is not None,
            "phase": context.phase if context else None,
            "run_id": run_id,
            "events": events,
            "event_seq": event_seq,
            "delivery_reports": delivery_reports,
            "accepted_requests": {
                key: value["run_id"] for key, value in record.get("requests", {}).items()
            },
        }


def _get_sessions_dir(workdir: str = None) -> Path:
    """Compatibility helper for callers that inspect the session directory."""
    root = workdir or (api_server.agent_config.get("workdir") if api_server.agent_config else None)
    if root:
        return _store_for_workdir(root).directory
    if api_server._session_store is not None:
        return api_server._session_store.directory
    return Path(".").resolve() / ".agent" / "sessions"
