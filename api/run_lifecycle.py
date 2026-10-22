"""一次 agent run 的启动、执行与收尾。"""
import threading
import time
import uuid

import api_server
from api import callbacks
from api.session_helpers import (
    _resolve_session, _store_key, _make_title,
)
from components.session_store import to_jsonable


def _persist_unfinished_stream(context):
    """Materialize only streamed assistant rounds not already appended by AgentLoop."""
    groups = []
    current = None
    for event in context.events:
        if event.get("type") == "agent_state" and event.get("state") == "thinking":
            current = {"text": [], "thinking": []}
            groups.append(current)
        elif event.get("type") in {"chunk", "thinking"}:
            if current is None:
                current = {"text": [], "thinking": []}
                groups.append(current)
            value = event.get("text", "")
            if value:
                key = "text" if event["type"] == "chunk" else "thinking"
                current[key].append(value)

    completed_assistant_rounds = sum(
        1 for message in context.history[context.baseline_length:]
        if message.get("role") == "assistant"
    )
    unfinished = groups[completed_assistant_rounds:]
    partial_text = "".join(
        part for group in unfinished for part in group["text"]
    )
    partial_thinking = "".join(
        part for group in unfinished for part in group["thinking"]
    )
    if not partial_text and not partial_thinking:
        return

    content = []
    if partial_thinking:
        content.append({
            "type": "text",
            "text": f"[Partial thinking before interruption]\n{partial_thinking}",
        })
    if partial_text:
        content.append({"type": "text", "text": partial_text})
    context.history.append({"role": "assistant", "content": content})


def _owned_terminal_event(context, event: dict, event_seq: int):
    owned = dict(to_jsonable(event))
    owned["session_id"] = context.session_id
    owned["run_id"] = context.run_id
    owned["event_seq"] = event_seq
    owned.setdefault("ts", time.time())
    return owned


def _finish_run(context):
    _persist_unfinished_stream(context)
    record = context.store.get(context.session_id) or {
        "id": context.session_id,
        "created_at": time.time(),
    }
    terminal_raw = context.pending_run_end or {"type": "run_end", "ts": time.time()}
    terminal_seq = context.event_seq + 1
    record.update({
        "history": context.history,
        "title": context.title,
        "workdir": context.workdir,
        "mode": context.mode,
        "event_seq": terminal_seq,
    })

    persistence_error = None
    try:
        context.store.save(record)
        context.event_seq = terminal_seq
        terminal = _owned_terminal_event(context, terminal_raw, terminal_seq)
    except Exception as exc:
        api_server.app.logger.exception("Failed to persist session %s", context.session_id)
        persistence_error = _owned_terminal_event(context, {
            "type": "error",
            "message": f"Failed to save session: {exc}",
            "ts": time.time(),
        }, context.event_seq + 1)
        context.event_seq += 2
        record["event_seq"] = context.event_seq
        context.store.retain(record)
        terminal = _owned_terminal_event(context, terminal_raw, context.event_seq)

    with api_server._lock:
        if api_server._running_ctx is context:
            api_server._running_ctx = None
            api_server._is_running = False
            api_server._run_thread = None
        if (api_server._active_session_id == context.session_id
                and api_server._active_store_key == _store_key(context.workdir)):
            api_server.conversation_history = context.history

    if persistence_error:
        api_server.socketio.emit("agent_event", persistence_error)
    api_server.socketio.emit("agent_event", terminal)


def _execute_run(context, plan_mode: bool):
    try:
        context.agent.run(context.history, run_id=context.run_id, plan_mode=plan_mode)
    except Exception as exc:
        api_server.app.logger.exception("Agent run failed")
        callbacks._push_run_event(context, {
            "type": "error",
            "message": str(exc),
            "ts": time.time(),
        })
    finally:
        _finish_run(context)


def _start_run(
    session_id: str,
    content: str,
    mode: str,
    plan_mode: bool,
    title_message: str = "",
):
    """Atomically admit one run and bind every mutable dependency to it."""
    selected_mode = mode if mode in {"plan", "auto", "manual"} else "auto"
    with api_server._lock:
        if api_server._running_ctx is not None:
            return None, "agent is already running", 409
        record, store = _resolve_session(session_id or api_server._active_session_id)
        if record is None:
            return None, "session not found", 404
        sid = record["id"]
        workdir = record.get("workdir") or (api_server.agent_config or {}).get("workdir", "")

        current_workdir = (api_server.agent_config or {}).get("workdir", "")
        if workdir and workdir != current_workdir:
            try:
                api_server.ensure_agent_initialized(workdir=workdir, session_id=sid)
            except Exception as exc:
                api_server.app.logger.exception("Agent initialization failed")
                return None, f"Agent initialization failed: {exc}", 503
            record, store = _resolve_session(sid, workdir)

        history = list(record.get("history", []))
        had_user_message = any(item.get("role") == "user" for item in history)
        history.append({"role": "user", "content": content})
        title = record.get("title", "")
        if title_message and not had_user_message:
            title = _make_title(title_message)

        run_id = str(uuid.uuid4())
        context = api_server.RunContext(
            session_id=sid,
            run_id=run_id,
            history=history,
            baseline_history=to_jsonable(history),
            agent=api_server.agent_instance,
            mode=selected_mode,
            workdir=workdir,
            title=title,
            store=store,
            baseline_length=len(history),
            event_seq=int(record.get("event_seq", 0)),
        )
        try:
            record.update({
                "history": history,
                "title": title,
                "workdir": workdir,
                "mode": selected_mode,
                "event_seq": context.event_seq,
            })
            store.save(record)
        except Exception as exc:
            api_server.app.logger.exception("Failed to save session before run")
            return None, f"Failed to save session: {exc}", 500

        api_server._active_session_id = sid
        api_server._active_store_key = _store_key(workdir)
        api_server._session_store = store
        api_server._current_mode = selected_mode
        api_server.conversation_history = history
        context.agent.event_callback = lambda event: callbacks._push_run_event(context, event)
        context.agent.approval_callback = callbacks._approval_callback_for(context)
        if api_server._cfg and api_server._skills and hasattr(context.agent, "system"):
            context.agent.system = api_server._build_system_prompt(
                api_server._cfg, api_server._skills, selected_mode
            )

        api_server._running_ctx = context
        api_server._is_running = True
        thread = threading.Thread(
            target=_execute_run,
            args=(context, plan_mode),
            daemon=True,
        )
        api_server._run_thread = thread
        try:
            thread.start()
        except Exception as exc:
            api_server._running_ctx = None
            api_server._is_running = False
            api_server._run_thread = None
            return None, f"Failed to start agent: {exc}", 500

    return {"run_id": run_id, "session_id": sid, "status": "started"}, None, 200
