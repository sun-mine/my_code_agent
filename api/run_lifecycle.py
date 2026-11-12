"""一次 agent run 的启动、执行与收尾。"""
import hashlib
import json
import threading
import time
import uuid

import api_server
from api import callbacks
from api.session_helpers import (
    _resolve_session, _store_key, _make_title,
)
from components.session_store import to_jsonable
from components.run_report import RunReportRecorder
from api.reviews import run_review, attach_review, is_review_resuming


def interrupt_owned_run(context) -> bool:
    """Stop either coding or review under the caller's ownership lock."""
    if context.phase == "finishing":
        return False
    context.review_token.cancel()
    if context.phase in {"reviewing", "review_pending"}:
        callbacks._push_run_event(context, {"type": "agent_state", "state": "cancelling"})
        return True
    return context.agent.interrupt() is not False


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


def _reported_outcome(context, terminal_raw: dict) -> str:
    explicit = terminal_raw.get("outcome")
    if explicit in {"failed", "interrupted", "incomplete"}:
        return explicit
    if any(event.get("type") == "interrupted" for event in context.events):
        return "interrupted"
    if any(event.get("type") == "error" for event in context.events):
        return "failed"
    if explicit == "completed":
        return explicit
    if context.pending_run_end is None:
        return "incomplete"
    return "completed"


def _with_delivery_report(record: dict, report: dict) -> None:
    reports = [
        item for item in (record.get("delivery_reports") or [])
        if isinstance(item, dict) and item.get("run_id") != report.get("run_id")
    ]
    reports.append(report)
    reports.sort(key=lambda item: (item.get("finished_at", 0), item.get("started_at", 0)))
    record["delivery_reports"] = reports[-5:]


def _history_run_ids(context) -> list:
    """Resolve message ownership without putting transport metadata in model input."""
    preserved = {
        id(message): owner
        for message, owner in context.history_owners
    }
    return [preserved.get(id(message), context.run_id) for message in context.history]


def _finish_run(context):
    _persist_unfinished_stream(context)
    record = context.store.get(context.session_id) or {
        "id": context.session_id,
        "created_at": time.time(),
    }
    terminal_raw = context.pending_run_end or {"type": "run_end", "ts": time.time()}
    outcome = _reported_outcome(context, terminal_raw)
    if context.run_reporter:
        report = context.run_reporter.finalize(outcome)
    else:
        report = {
            "run_id": context.run_id,
            "session_id": context.session_id,
            "outcome": "incomplete" if outcome == "completed" else outcome,
            "started_at": time.time(),
            "finished_at": time.time(),
            "base_sha": None,
            "files": [],
            "diff": "",
            "commands": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "warnings": ["Run report recorder was unavailable"],
            "validation": "unknown",
        }
    report = attach_review(context, report)
    context.delivery_report = report
    delivery_seq = context.event_seq + 1
    terminal_seq = context.event_seq + 2
    delivery = _owned_terminal_event(context, {
        "type": "delivery_report",
        "report": report,
        "ts": report["finished_at"],
    }, delivery_seq)
    terminal_raw = dict(terminal_raw)
    terminal_raw["outcome"] = report["outcome"]
    terminal = _owned_terminal_event(context, terminal_raw, terminal_seq)
    _with_delivery_report(record, report)
    record.update({
        "history": context.history,
        "history_run_ids": _history_run_ids(context),
        "title": context.title,
        "workdir": context.workdir,
        "mode": context.mode,
        "event_seq": terminal_seq,
    })

    persistence_error = None
    try:
        context.store.save(record)
        context.event_seq = terminal_seq
    except Exception as exc:
        api_server.app.logger.exception("Failed to persist session %s", context.session_id)
        if context.run_reporter:
            report = context.run_reporter.revise(
                report,
                f"Session persistence failed: {type(exc).__name__}: {exc}",
            )
        elif report["outcome"] == "completed":
            report["outcome"] = "incomplete"
        context.delivery_report = report
        delivery["report"] = report
        terminal["outcome"] = report["outcome"]
        _with_delivery_report(record, report)
        persistence_error = _owned_terminal_event(context, {
            "type": "error",
            "message": f"Failed to save session: {exc}",
            "ts": time.time(),
        }, context.event_seq + 2)
        context.event_seq += 3
        record["event_seq"] = context.event_seq
        terminal["event_seq"] = context.event_seq
        context.store.retain(record)

    final_events = [delivery]
    if persistence_error:
        final_events.append(persistence_error)
    final_events.append(terminal)
    if context.run_reporter and not context.run_reporter.record_events(final_events):
        report = context.run_reporter.revise(
            report,
            "Final run events could not be persisted to events.jsonl",
        )
        context.delivery_report = report
        delivery["report"] = report
        terminal["outcome"] = report["outcome"]
        _with_delivery_report(record, report)
        try:
            context.store.save(record)
        except Exception as exc:
            api_server.app.logger.exception(
                "Failed to persist degraded delivery report for %s", context.session_id,
            )
            report = context.run_reporter.revise(
                report,
                f"Degraded report session persistence failed: {type(exc).__name__}: {exc}",
            )
            context.delivery_report = report
            delivery["report"] = report
            terminal["outcome"] = report["outcome"]
            _with_delivery_report(record, report)
            context.store.retain(record)

    with api_server._lock:
        if api_server._running_ctx is context:
            api_server._running_ctx = None
            api_server._is_running = False
            api_server._run_thread = None
        if (api_server._active_session_id == context.session_id
                and api_server._active_store_key == _store_key(context.workdir)):
            api_server.conversation_history = context.history

    api_server.socketio.emit("agent_event", delivery)
    if persistence_error:
        api_server.socketio.emit("agent_event", persistence_error)
    api_server.socketio.emit("agent_event", terminal)


def _execute_run(context, plan_mode: bool):
    try:
        options = {"run_id": context.run_id, "plan_mode": plan_mode}
        if not context.prefetch_context and getattr(context.agent, "context_tools", None):
            options["load_context"] = False
        context.agent.run(context.history, **options)
        run_review(context, plan_mode)
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
    content,
    mode: str,
    plan_mode: bool,
    title_message: str = "",
    request_id: str = None,
    prefetch_context: bool = True,
):
    """Atomically admit one run and bind every mutable dependency to it."""
    selected_mode = mode if mode in {"plan", "auto", "manual"} else "auto"
    if request_id is not None and (
        not isinstance(request_id, str) or not request_id or len(request_id) > 128 or not session_id
    ):
        return None, "request_id requires an explicit session_id and 1-128 characters", 400
    fingerprint = hashlib.sha256(json.dumps(
        [content, selected_mode, plan_mode], sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest() if request_id else None
    with api_server._lock:
        record, store = _resolve_session(session_id or api_server._active_session_id)
        if record is None:
            return None, "session not found", 404
        sid = record["id"]
        # Retry a lost acknowledgement before checking whether another run is busy.
        accepted = record.get("requests", {}).get(request_id) if request_id else None
        if accepted:
            if accepted["fingerprint"] != fingerprint:
                return None, "request_id was already used for a different message", 409
            return {"run_id": accepted["run_id"], "session_id": sid, "status": "accepted"}, None, 200
        if api_server._running_ctx is not None:
            return None, "agent is already running", 409
        if is_review_resuming():
            return None, "正在恢复审查，请等待查询结束后启动新任务。", 409
        workdir = record.get("workdir") or (api_server.agent_config or {}).get("workdir", "")

        current_workdir = (api_server.agent_config or {}).get("workdir", "")
        previous_selection = (
            api_server._active_session_id, api_server._active_store_key,
            api_server._session_store, api_server._current_mode, api_server.conversation_history,
        )
        if workdir and workdir != current_workdir:
            try:
                api_server.ensure_agent_initialized(workdir=workdir, session_id=sid)
            except Exception as exc:
                api_server.app.logger.exception("Agent initialization failed")
                return None, f"Agent initialization failed: {exc}", 503
            record, store = _resolve_session(sid, workdir)

        original_record = dict(record)
        history = list(record.get("history", []))
        stored_run_ids = record.get("history_run_ids", [])
        if not isinstance(stored_run_ids, list):
            stored_run_ids = []
        history_owners = [
            (
                message,
                stored_run_ids[index]
                if index < len(stored_run_ids) and isinstance(stored_run_ids[index], str)
                else None,
            )
            for index, message in enumerate(history)
        ]
        had_user_message = any(item.get("role") == "user" for item in history)
        user_message = {"role": "user", "content": content}
        history.append(user_message)
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
            history_owners=[*history_owners, (user_message, run_id)],
            run_reporter=RunReportRecorder(workdir, run_id, sid),
            prefetch_context=prefetch_context,
            review_config={
                "url": getattr(api_server._cfg, "a2a_review_url", ""),
                "timeout": getattr(api_server._cfg, "a2a_review_timeout", "120"),
                "token": getattr(api_server._cfg, "a2a_review_token", ""),
            },
            event_seq=int(record.get("event_seq", 0)),
        )
        try:
            if request_id:
                record["requests"] = {
                    **record.get("requests", {}),
                    request_id: {"run_id": run_id, "fingerprint": fingerprint},
                }
            record.update({
                "history": history,
                "history_run_ids": [
                    *[owner for _message, owner in history_owners],
                    run_id,
                ],
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
            prepare_run = getattr(context.agent, "prepare_run", None)
            if prepare_run:
                prepare_run(run_id)
            thread.start()
        except Exception as exc:
            api_server._running_ctx = None
            api_server._is_running = False
            api_server._run_thread = None
            release_run = getattr(context.agent, "release_run", None)
            if release_run:
                release_run(run_id)
            api_server.conversation_history = original_record.get("history", [])
            try:
                store.save(original_record)
            except Exception:
                store.retain(original_record)
                api_server.app.logger.exception("Failed to roll back unstarted run")
            if current_workdir and workdir != current_workdir:
                api_server.ensure_agent_initialized(workdir=current_workdir, session_id=previous_selection[0])
            (
                api_server._active_session_id, api_server._active_store_key,
                api_server._session_store, api_server._current_mode, api_server.conversation_history,
            ) = previous_selection
            return None, f"Failed to start agent: {exc}", 500

    return {"run_id": run_id, "session_id": sid, "status": "started"}, None, 200
