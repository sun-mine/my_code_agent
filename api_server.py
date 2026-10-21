"""
Web API 服务器 - ClaudeCode 风格前端的后端
提供 RESTful API 和 WebSocket 实时事件推送
"""
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).parent))

from anthropic import Anthropic
from components.config import Config
from components.memory_manager import MemoryManager
from components.safety_manager import SafetyManager
from components.error_recovery import ErrorRecoveryManager, init_error_recovery
from components.todo_manager import TodoManager
from components.task_manager import TaskManager
from components.message_bus import MessageBus
from components.team_manager import TeammateManager
from components.background_manager import BackgroundManager
from components.skill_loader import SkillLoader
from components.compactor import estimate_tokens
from components.session_store import SessionStore, to_jsonable
from core.loop import AgentLoop
from tools import get_all_tools, create_all_handlers, get_core_tools, get_tools_by_categories, create_handlers_for_categories, get_all_categories
from tools.base import set_workdir
from core.subagent import run_subagent

# ====== Flask & SocketIO ======
app = Flask(__name__)
CORS(app, origins="*")
# threading 模式：稳定可靠；断线重连与数据恢复由前端 useAgentSocket 自动处理
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ====== 全局状态 ======
_lock = threading.RLock()
_agent_init_lock = threading.RLock()
agent_instance: AgentLoop = None
conversation_history = []
memory_manager: MemoryManager = None
safety_manager: SafetyManager = None
error_manager: ErrorRecoveryManager = None
todo_manager: TodoManager = None
task_manager: TaskManager = None
team_manager: TeammateManager = None
bg_manager: BackgroundManager = None
agent_config: dict = None
_run_thread: threading.Thread = None
_agent_lock = threading.Lock()   # 防止并发 run
_is_running = False              # Agent 当前是否在运行
_cfg: Config = None              # 缓存 config，用于动态重建 system prompt
_skills: SkillLoader = None      # 缓存 skills，用于动态重建 system prompt
_session_store: Optional[SessionStore] = None
_session_stores: Dict[str, SessionStore] = {}
_active_session_id: Optional[str] = None
_active_store_key: Optional[str] = None


@dataclass
class RunContext:
    session_id: str
    run_id: str
    history: list
    baseline_history: list
    agent: AgentLoop
    mode: str
    workdir: str
    title: str
    store: SessionStore
    baseline_length: int
    event_seq: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    pending_run_end: Optional[Dict[str, Any]] = None


_running_ctx: Optional[RunContext] = None

# ====== 工具审批状态 ======
_pending_approvals: dict = {}   # tool_use_id -> approval metadata
_approval_lock = threading.Lock()
_approval_timeout_seconds = 120.0
_current_mode: str = "auto"     # plan | auto | manual

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
    import re
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

    selected_mode = mode or _current_mode
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
    with _lock:
        context = _running_ctx
    if context:
        _push_run_event(context, event)
        return
    socketio.emit("agent_event", event)


def _push_run_event(context: RunContext, event: dict):
    """Attach immutable run ownership and a monotonic session sequence."""
    owned = dict(to_jsonable(event))
    with _lock:
        if owned.get("type") == "run_end":
            context.pending_run_end = owned
            return
        context.event_seq += 1
        owned["session_id"] = context.session_id
        owned["run_id"] = context.run_id
        owned["event_seq"] = context.event_seq
        context.events.append(owned)
    socketio.emit("agent_event", owned)


def _emit_request_error(message: str, session_id: str = None):
    sid = session_id or _active_session_id
    with _lock:
        if _running_ctx and _running_ctx.session_id == sid:
            context = _running_ctx
        else:
            context = None
            record, store = _resolve_session(sid)
            seq = int(record.get("event_seq", 0)) + 1 if record else 1
            if record:
                record["event_seq"] = seq
                try:
                    store.save(record)
                except Exception:
                    app.logger.exception("Failed to persist request error sequence")
    if context:
        _push_run_event(context, {"type": "error", "message": message, "ts": time.time()})
        return
    socketio.emit("agent_event", {
        "type": "error",
        "message": message,
        "session_id": sid,
        "run_id": None,
        "event_seq": seq,
        "ts": time.time(),
    })


def _approval_callback_for(context: RunContext):
    def callback(tool_use_id: str, name: str, inp: dict) -> bool:
        if not _should_approve(name, inp, context.mode):
            return True

        event = threading.Event()
        result = [False]
        with _approval_lock:
            _pending_approvals[tool_use_id] = {
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

        deadline = time.time() + _approval_timeout_seconds
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0)
            if event.wait(timeout=min(0.1, remaining)):
                break
            if context.agent._interrupt_flag.is_set():
                result[0] = False
                break

        with _approval_lock:
            _pending_approvals.pop(tool_use_id, None)
        return result[0]

    return callback


def _approval_callback(tool_use_id: str, name: str, inp: dict) -> bool:
    """Compatibility callback; normal runs install a context-bound callback."""
    with _lock:
        context = _running_ctx
    if not context:
        return False
    return _approval_callback_for(context)(tool_use_id, name, inp)


def _build_environment_context(workdir: str) -> str:
    """动态收集运行环境上下文"""
    import platform
    import subprocess
    cwd = workdir or os.getcwd()
    lines = [f"- Primary working directory: {cwd}"]
    # git info
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, cwd=cwd, timeout=3)
        if r.returncode == 0:
            lines.append("- Is a git repository: true")
            branch = subprocess.run(["git", "branch", "--show-current"],
                                    capture_output=True, text=True, cwd=cwd, timeout=3).stdout.strip()
            lines.append(f"- Current branch: {branch}")
            log = subprocess.run(["git", "log", "--oneline", "-5"],
                                 capture_output=True, text=True, cwd=cwd, timeout=3).stdout.strip()
            lines.append(f"- Recent commits:\n{log}")
            st = subprocess.run(["git", "status", "--short"],
                                capture_output=True, text=True, cwd=cwd, timeout=3).stdout.strip()
            if st:
                lines.append(f"- Git status (snapshot):\n{st}")
    except Exception:
        pass
    lines.append(f"- Platform: {platform.system()}")
    lines.append(f"- OS Version: {platform.release()}")
    lines.append(f"- Shell: {os.environ.get('SHELL', 'unknown')}")
    return "\n".join(lines)


def _build_system_prompt(cfg, skills, mode: str = None) -> str:
    """构建分层 system prompt"""

    block1 = f"""You are Code Agent, an interactive AI that helps users with software engineering tasks at {cfg.workdir}.

## Communication
- Output in the user's language (default Chinese if the user writes in Chinese).
- Keep responses concise: ≤150 words for final answers, ≤30 words between tool calls.
- No emojis unless the user explicitly asks. Do not add emoji to messages.
- End each turn with: what changed (1 sentence), what's next (1 sentence). Nothing more.
- When referencing files or code locations, use file_path:line_number format.

## Code Philosophy
- Prefer editing existing files over creating new ones.
- Don't add features, refactors, or abstractions beyond what the task requires.
- A bug fix doesn't need surrounding cleanup; a one-shot operation doesn't need a helper.
- Don't add error handling for scenarios that can't happen. Trust internal code.
- Don't write comments that describe WHAT the code does — well-named identifiers already do that.
- No backwards-compatibility shims, no unused _var renames, no // removed comments.

## Safety
- Local, reversible actions (reading files, editing code, running tests): proceed directly.
- Destructive actions (rm -rf, deleting branches, dropping tables): ASK before executing.
- Hard-to-reverse actions (force push, git reset --hard, amending published commits): ASK before executing.
- Externally visible actions (pushing code, creating PRs): ASK before executing.
- Never modify .env, credentials, or .gitignore files unless explicitly asked."""

    block2 = """## Tool Selection (strict priority)
- Read a file → use `read_file` (NOT cat/head/tail in bash)
- Search file content / find symbols → use `grep` tool (NOT grep/rg in bash)
- Find files by name → use `list_files` / `glob` tool (NOT find/ls in bash)
- Edit a file with exact replacements → use `edit_file` (NOT sed/awk in bash)
- Write a new file → use `write_file` (NOT echo/cat > in bash)
- Run shell commands → use `bash` ONLY when no dedicated tool covers it
- Long-running commands (>30s) → use `background_run`
- Multi-step complex tasks → use `TodoWrite` to track progress
- Subagent delegation → use `task` for parallel exploration
- Facts worth remembering → use `memory_store` / `memory_recall`
- Before risky bulk changes → use `safety_checkpoint`

## Parallel Execution
- If you need multiple tools with NO dependencies → call ALL in ONE message.
- If tool B depends on tool A's result → call sequentially.

## Git Rules
- Only commit when the user explicitly asks.
- Always create NEW commits (never amend) unless the user explicitly requests amend.
- Never skip hooks (--no-verify). Never force push to main.
- When committing: run git status + git diff + git log in parallel first, then draft message, then commit.
- Commit messages: concise (1-2 sentences), focus on WHY not WHAT."""

    block3 = """## Task Handling
- Simple questions: answer directly, no headers/sections.
- Bug fixes: read code → identify root cause → apply minimal fix → verify.
- New features with one obvious approach: implement directly.
- New features with multiple approaches: describe options in 2-3 sentences each with tradeoffs, ask user to choose.
- Tasks touching >3 files or involving architecture: create a brief numbered plan first, get approval, then execute.
- Exploratory questions ("how could we improve X?"): 2-3 sentences with recommendation and main tradeoff.

## When executing
- Read relevant files before editing them.
- Prefer `edit_file` over rewriting entire files with `write_file`.
- Ensure `old_text` in edit_file exactly matches a segment of the file (including whitespace).
- After changes, verify by reading the modified file or running tests.
- When you encounter an obstacle, diagnose root cause rather than bypassing safety checks."""

    # Mode-specific instructions
    if mode is None:
        mode = os.getenv("AGENT_MODE", "auto")
    if mode == "plan":
        block3 += """

## Plan Mode (Active)
You are in Plan Mode. Work in TWO phases:

**Phase 1: Clarify requirements (MAX ONE ROUND)**
- If the request is already clear, SKIP this phase entirely — go to Phase 2
- Ask at most ONE round of questions — group all questions into a single <choices> block
- When you need the user to choose, use <choices> to present options:
  <choices>
  jwt: 使用 JWT 无状态认证
  session: 服务端 Session 管理
  </choices>
- Each line is 'option_id: description'
- The user will click one option, and you'll receive it as context
- Do NOT use TodoWrite or any other tools in this phase

**Phase 2: Create detailed plan (immediately after requirements are clear)**
- After Phase 1 questions are answered (or skipped), proceed DIRECTLY to Phase 2
- Do NOT ask more questions in this phase — make your best judgment
- Use TodoWrite to register every execution task
- Present the plan to the user in this format:
  - **Overview**: 1-2 sentences
  - **Files to modify**: List each file and what changes
  - **Step-by-step plan**: Numbered steps
  - **Potential risks**: Edge cases or concerns
- After TodoWrite + plan text, STOP — do NOT execute bash/write_file/edit_file/task

The plan will only be finalized for review after you use TodoWrite. Wait for user approval before executing."""
    elif mode == "manual":
        block3 += """

## Manual Mode (Active)
You are in Manual Mode. You MUST:
1. Ask user confirmation before every file edit (write_file, edit_file), bash command, or task delegation
2. Explain WHAT and WHY before each action
3. Wait for explicit approval before executing"""

    block4 = f"""## On-Demand Tools
You start with core tools only. When you need memory, safety, team, worktree, background, or task-board tools, call `load_tools` with the category name(s).
Available categories: {', '.join(f'{k}({len(v)})' for k, v in sorted(get_all_categories().items()))}

## Environment
{_build_environment_context(str(cfg.workdir))}

Skills available: {skills.descriptions()}"""

    return "\n\n".join([block1, block2, block3, block4])


def init_agent(workdir: str = None, session_id: str = None):
    """初始化所有组件"""
    global agent_instance, conversation_history
    global memory_manager, safety_manager, error_manager
    global todo_manager, task_manager, team_manager, bg_manager, agent_config
    global _cfg, _skills, _active_session_id, _active_store_key, _session_store

    cfg = Config(workdir=workdir)
    _cfg = cfg  # 缓存供动态重建 system prompt 使用
    set_workdir(str(cfg.workdir))  # 同步 WORKDIR 到工具模块
    client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url)
    model = cfg.model

    skills = SkillLoader(cfg.skills_dir)
    _skills = skills  # 缓存供动态重建 system prompt 使用
    bus = MessageBus(cfg.team_dir / "inbox")
    task_manager = TaskManager(cfg.tasks_dir)
    bg_manager = BackgroundManager()
    team_manager = TeammateManager(bus, task_manager, client, model, cfg)
    todo_manager = TodoManager()
    memory_manager = MemoryManager(cfg.workdir)
    safety_manager = SafetyManager(cfg.workdir)
    error_manager = ErrorRecoveryManager(cfg.workdir, memory_manager, safety_manager)
    init_error_recovery(error_manager)
    # 跳过初始化快照：避免大目录时卡住

    # Core tools only at startup (≈10 tools, ~2K tokens)
    tools = get_core_tools()
    # Virtual tool: choices — handled inline in loop.py, no real handler needed
    tools.append({
        "name": "choices",
        "description": "Present options for user to choose from during plan mode.",
        "input_schema": {
            "type": "object",
            "properties": {
                "options": {"type": "string", "description": "Options in 'id: label' format, one per line"}
            },
            "required": ["options"]
        }
    })
    # But create ALL handlers upfront so they're ready when tools load on demand
    handlers = create_all_handlers(
        todo_manager=todo_manager,
        skill_loader=skills,
        task_manager=task_manager,
        message_bus=bus,
        team_manager=team_manager,
        background=bg_manager,
        config=cfg,
        memory=memory_manager,
        safety=safety_manager,
        compactor=None,
    )
    handlers["task"] = lambda **kw: run_subagent(
        client, model, kw["prompt"], kw.get("agent_type", "Explore")
    )

    # tool_loader: called when agent invokes load_tools(categories)
    def _load_tool_categories(categories):
        new_tools = get_tools_by_categories(categories)
        new_handlers = create_handlers_for_categories(
            categories,
            todo_manager=todo_manager,
            skill_loader=skills,
            task_manager=task_manager,
            message_bus=bus,
            team_manager=team_manager,
            background=bg_manager,
            config=cfg,
            memory=memory_manager,
            safety=safety_manager,
            compactor=None,
        )
        return new_tools, new_handlers

    system = _build_system_prompt(cfg, skills)

    agent_instance = AgentLoop(
        client=client,
        model=model,
        system=system,
        tools=tools,
        tool_handlers=handlers,
        bg=bg_manager,
        bus=bus,
        todo_manager=todo_manager,
        token_threshold=cfg.token_threshold,
        event_callback=_push_event,
        approval_callback=_approval_callback,
        tool_loader=_load_tool_categories,
    )

    agent_config = {
        "workdir": str(cfg.workdir),
        "model": model,
        "system_prompt_len": len(system),
        "tool_definitions_len": len(json.dumps(tools, default=str)),
        "token_threshold": cfg.token_threshold,
    }
    store = _store_for_workdir(str(cfg.workdir))
    _session_store = store
    store_key = _store_key(str(cfg.workdir))
    selected_id = session_id
    if selected_id is None and _active_store_key == store_key:
        selected_id = _active_session_id
    selected = store.get(selected_id) if selected_id else None
    if selected is None or selected.get("workdir") != str(cfg.workdir):
        selected = store.create(workdir=str(cfg.workdir), mode=_current_mode)
    _active_session_id = selected["id"]
    _active_store_key = store_key
    conversation_history = selected.get("history", [])


def ensure_agent_initialized(workdir: str = None, session_id: str = None):
    """Initialize the Agent once, including when Flask is hosted by an app server."""
    with _agent_init_lock:
        if agent_instance is not None and workdir is None:
            return
        init_agent(workdir=workdir, session_id=session_id)


# ====== Helper ======

def _ok(data=None):
    return jsonify({"success": True, "data": data, "ts": time.time()})


def _err(msg, code=400):
    return jsonify({"success": False, "error": str(msg), "ts": time.time()}), code


def _require_agent():
    if not agent_instance:
        try:
            ensure_agent_initialized()
        except Exception as exc:
            app.logger.exception("Agent initialization failed")
            return _err(f"Agent initialization failed: {exc}", 503)
    if not agent_instance:
        return _err("Agent not initialized", 503)


def _store_key(workdir: str) -> str:
    return str(Path(workdir).expanduser().resolve())


def _store_for_workdir(workdir: str) -> SessionStore:
    key = _store_key(workdir)
    store = _session_stores.get(key)
    if store is None:
        store = SessionStore(Path(key) / ".agent" / "sessions")
        _session_stores[key] = store
    return store


def _resolve_session(session_id: str = None, workdir: str = None):
    sid = session_id or _active_session_id
    if not sid:
        return None, None
    if workdir:
        store = _store_for_workdir(workdir)
        return store.get(sid), store
    if _running_ctx and _running_ctx.session_id == sid:
        return _running_ctx.store.get(sid), _running_ctx.store
    if sid == _active_session_id and _active_store_key:
        store = _session_stores.get(_active_store_key)
        if store:
            record = store.get(sid)
            if record is not None:
                return record, store
    for store in list(_session_stores.values()):
        record = store.get(sid)
        if record is not None:
            return record, store
    return None, None


def _get_record(session_id: str = None, workdir: str = None):
    record, _store = _resolve_session(session_id, workdir)
    return record


def _make_title(message: str) -> str:
    return " ".join(message.split())[:80]


def _session_snapshot(session_id: str = None):
    with _lock:
        sid = session_id or _active_session_id
        record = _get_record(sid)
        if record is None:
            return None
        context = _running_ctx if _running_ctx and _running_ctx.session_id == sid else None
        if context:
            history = to_jsonable(context.baseline_history)
            events = to_jsonable(context.events)
            event_seq = context.event_seq
            run_id = context.run_id
        else:
            history = to_jsonable(record.get("history", []))
            events = []
            event_seq = int(record.get("event_seq", 0))
            run_id = None
        return {
            "session_id": sid,
            "history": history,
            "total": len(history),
            "title": record.get("title", ""),
            "workdir": record.get("workdir", ""),
            "mode": record.get("mode", "auto"),
            "running": context is not None,
            "run_id": run_id,
            "events": events,
            "event_seq": event_seq,
        }


def _persist_unfinished_stream(context: RunContext):
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


def _owned_terminal_event(context: RunContext, event: dict, event_seq: int):
    owned = dict(to_jsonable(event))
    owned["session_id"] = context.session_id
    owned["run_id"] = context.run_id
    owned["event_seq"] = event_seq
    owned.setdefault("ts", time.time())
    return owned


def _finish_run(context: RunContext):
    global _running_ctx, _is_running, _run_thread, conversation_history
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
        app.logger.exception("Failed to persist session %s", context.session_id)
        persistence_error = _owned_terminal_event(context, {
            "type": "error",
            "message": f"Failed to save session: {exc}",
            "ts": time.time(),
        }, context.event_seq + 1)
        context.event_seq += 2
        record["event_seq"] = context.event_seq
        context.store.retain(record)
        terminal = _owned_terminal_event(context, terminal_raw, context.event_seq)

    with _lock:
        if _running_ctx is context:
            _running_ctx = None
            _is_running = False
            _run_thread = None
        if (_active_session_id == context.session_id
                and _active_store_key == _store_key(context.workdir)):
            conversation_history = context.history

    if persistence_error:
        socketio.emit("agent_event", persistence_error)
    socketio.emit("agent_event", terminal)


def _execute_run(context: RunContext, plan_mode: bool):
    try:
        context.agent.run(context.history, run_id=context.run_id, plan_mode=plan_mode)
    except Exception as exc:
        app.logger.exception("Agent run failed")
        _push_run_event(context, {
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
    global _active_session_id, _active_store_key, _current_mode, _is_running, _running_ctx
    global _run_thread, conversation_history, _session_store

    selected_mode = mode if mode in {"plan", "auto", "manual"} else "auto"
    with _lock:
        if _running_ctx is not None:
            return None, "agent is already running", 409
        record, store = _resolve_session(session_id or _active_session_id)
        if record is None:
            return None, "session not found", 404
        sid = record["id"]
        workdir = record.get("workdir") or (agent_config or {}).get("workdir", "")

        current_workdir = (agent_config or {}).get("workdir", "")
        if workdir and workdir != current_workdir:
            try:
                ensure_agent_initialized(workdir=workdir, session_id=sid)
            except Exception as exc:
                app.logger.exception("Agent initialization failed")
                return None, f"Agent initialization failed: {exc}", 503
            record, store = _resolve_session(sid, workdir)

        history = list(record.get("history", []))
        had_user_message = any(item.get("role") == "user" for item in history)
        history.append({"role": "user", "content": content})
        title = record.get("title", "")
        if title_message and not had_user_message:
            title = _make_title(title_message)

        run_id = str(uuid.uuid4())
        context = RunContext(
            session_id=sid,
            run_id=run_id,
            history=history,
            baseline_history=to_jsonable(history),
            agent=agent_instance,
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
            app.logger.exception("Failed to save session before run")
            return None, f"Failed to save session: {exc}", 500

        _active_session_id = sid
        _active_store_key = _store_key(workdir)
        _session_store = store
        _current_mode = selected_mode
        conversation_history = history
        context.agent.event_callback = lambda event: _push_run_event(context, event)
        context.agent.approval_callback = _approval_callback_for(context)
        if _cfg and _skills and hasattr(context.agent, "system"):
            context.agent.system = _build_system_prompt(_cfg, _skills, selected_mode)

        _running_ctx = context
        _is_running = True
        thread = threading.Thread(
            target=_execute_run,
            args=(context, plan_mode),
            daemon=True,
        )
        _run_thread = thread
        try:
            thread.start()
        except Exception as exc:
            _running_ctx = None
            _is_running = False
            _run_thread = None
            return None, f"Failed to start agent: {exc}", 500

    return {"run_id": run_id, "session_id": sid, "status": "started"}, None, 200


# ====== 系统 ======

@app.route("/api/status")
def get_status():
    r = _require_agent()
    if r:
        return r
    snapshot = _session_snapshot()
    active_history = snapshot["history"] if snapshot else conversation_history
    # Token usage estimate
    estimated = estimate_tokens(active_history)
    threshold = agent_instance.token_threshold if agent_instance else 100000

    # Token breakdown by category
    system_len = agent_config.get("system_prompt_len", 0) if agent_config else 0
    tool_len = agent_config.get("tool_definitions_len", 0) if agent_config else 0
    # Recalculate tool tokens directly from tools for accuracy
    tool_count = 0
    if agent_instance:
        tools_json = json.dumps(agent_instance.tools, default=str)
        tool_len = len(tools_json)
        tool_count = len(agent_instance.tools)
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
        "agent_state": agent_instance.state,
        "run_id": _running_ctx.run_id if _running_ctx else None,
        "session_id": _active_session_id,
        "running_session_id": _running_ctx.session_id if _running_ctx else None,
        "running_run_id": _running_ctx.run_id if _running_ctx else None,
        "workdir": agent_config["workdir"] if agent_config else "",
        "model": agent_config["model"] if agent_config else "",
        "history_length": len(active_history),
        "token_usage": token_usage,
        "token_breakdown": token_breakdown,
        "memory_stats": memory_manager.get_memory_stats() if memory_manager else {},
        "safety_stats": safety_manager.get_safety_stats() if safety_manager else {},
    })


# ====== 聊天 ======

@app.route("/api/chat", methods=["POST"])
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


# ====== Plan 执行 ======

@app.route("/api/plan/execute", methods=["POST"])
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


@app.route("/api/chat/interrupt", methods=["POST"])
def interrupt_chat():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    with _lock:
        context = _running_ctx
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


@app.route("/api/chat/tool_approve", methods=["POST"])
def tool_approve():
    """批准或拒绝一个工具调用"""
    data = request.get_json(force=True)
    tool_use_id = (data.get("tool_use_id") or "").strip()
    approved = data.get("approved") is True
    if not tool_use_id:
        return _err("tool_use_id required")
    with _approval_lock:
        entry = _pending_approvals.get(tool_use_id)
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


# ====== 模型 ======

# Model definitions per provider
_PROVIDER_MODELS = {
    "anthropic": [
        {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4", "provider": "Anthropic"},
        {"id": "claude-opus-4-20250514", "label": "Claude Opus 4", "provider": "Anthropic"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "provider": "Anthropic"},
    ],
    "deepseek": [
        {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "provider": "DeepSeek"},
        {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "provider": "DeepSeek"},
    ],
    "openai": [
        {"id": "gpt-4o", "label": "GPT-4o", "provider": "OpenAI"},
        {"id": "gpt-4o-mini", "label": "GPT-4o Mini", "provider": "OpenAI"},
    ],
}


def _is_ollama() -> bool:
    base_url = (_cfg.base_url if _cfg else "").lower()
    return "11434" in base_url or "ollama" in base_url


def _detect_available_models() -> list:
    """Based on Config, detect which models are available"""
    base_url = (_cfg.base_url if _cfg else "").lower()

    if _is_ollama():
        # 从本地 Ollama 拉取支持对话的模型
        try:
            import urllib.request
            with urllib.request.urlopen(f"{_cfg.base_url}/api/tags", timeout=2) as r:
                data = json.loads(r.read())
            models = [
                {"id": m["name"], "label": m["name"], "provider": "Ollama"}
                for m in data.get("models", [])
                if "completion" in m.get("capabilities", [])
            ]
            if models:
                return models
        except Exception:
            pass
        return [{"id": _cfg.model, "label": _cfg.model, "provider": "Ollama"}]

    if "deepseek" in base_url:
        return list(_PROVIDER_MODELS["deepseek"])
    if "openai" in base_url:
        return list(_PROVIDER_MODELS["openai"])
    return list(_PROVIDER_MODELS["anthropic"])


@app.route("/api/models")
def list_models():
    models = _detect_available_models()
    current = _cfg.model if _cfg else (models[0]["id"] if models else "")
    return _ok({"models": models, "current": current})


@app.route("/api/model")
def get_model():
    if not agent_config:
        return _ok({"model": _cfg.model if _cfg else ""})
    return _ok({"model": agent_config.get("model", "")})


@app.route("/api/model", methods=["POST"])
def set_model():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    new_model = (data.get("model") or "").strip()
    if not new_model:
        return _err("model required")
    # Ollama 允许任意本地模型名；其他服务仍限制前缀
    if not _is_ollama():
        allowed = ("claude-", "deepseek-", "deepseek-v4-")
        if not any(new_model.startswith(p) for p in allowed):
            return _err(f"unsupported model: {new_model}")
    with _lock:
        if _running_ctx is not None:
            return _err("agent is running", 409)
        agent_instance.model = new_model
        agent_config["model"] = new_model
    return _ok({"model": new_model, "message": "model switched"})


# ====== 工作目录 ======

@app.route("/api/workdir")
def get_workdir():
    r = _require_agent()
    if r:
        return r
    return _ok({"workdir": agent_config["workdir"]})


@app.route("/api/workdir", methods=["POST"])
def change_workdir():
    """(重)初始化 agent 与工作目录"""
    data = request.get_json(force=True)
    new_dir = (data.get("workdir") or "").strip()
    if not new_dir:
        return _err("workdir required")
    p = Path(new_dir).expanduser().resolve()
    if not p.is_dir():
        return _err(f"directory not found: {p}", 404)
    with _lock:
        if _running_ctx is not None:
            return _err("agent is running, please interrupt first", 409)
        try:
            ensure_agent_initialized(workdir=str(p))
            return _ok({
                "workdir": str(p),
                "message": "re-initialized",
                "session_id": _active_session_id,
            })
        except Exception as e:
            return _err(str(e), 500)


@app.route("/api/history")
def get_history():
    r = _require_agent()
    if r:
        return r
    snapshot = _session_snapshot(request.args.get("session_id"))
    if snapshot is None:
        return _err("session not found", 404)
    return _ok(snapshot)


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    global conversation_history
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    sid = data.get("session_id") or request.args.get("session_id") or _active_session_id
    with _lock:
        if _running_ctx and _running_ctx.session_id == sid:
            return _err("cannot clear a running session", 409)
        record, store = _resolve_session(sid, request.args.get("workdir"))
        if record is None:
            return _err("session not found", 404)
        try:
            record["history"] = []
            store.save(record)
        except Exception as exc:
            return _err(f"Failed to clear session: {exc}", 500)
        if sid == _active_session_id:
            conversation_history = []
    return _ok({"session_id": sid})


# ====== 会话管理 ======

def _get_sessions_dir(workdir: str = None) -> Path:
    """Compatibility helper for callers that inspect the session directory."""
    root = workdir or (agent_config.get("workdir") if agent_config else None)
    if root:
        return _store_for_workdir(root).directory
    if _session_store is not None:
        return _session_store.directory
    return Path(".").resolve() / ".agent" / "sessions"


@app.route("/api/sessions")
def list_sessions():
    r = _require_agent()
    if r:
        return r
    with _lock:
        workdir = request.args.get("workdir") or (agent_config or {}).get("workdir", os.getcwd())
        store = _store_for_workdir(workdir)
        running_sid = (
            _running_ctx.session_id
            if _running_ctx and _running_ctx.store is store else None
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


@app.route("/api/sessions", methods=["POST"])
def create_session():
    """Legacy save endpoint; never rebind a running session's history."""
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    sid = data.get("id") or _active_session_id or SessionStore.new_id()
    title = (data.get("title") or "").strip()
    with _lock:
        record, store = _resolve_session(sid)
        if store is None:
            store = _store_for_workdir((agent_config or {}).get("workdir", os.getcwd()))
        try:
            if record is None:
                snapshot = _session_snapshot()
                record = store.create(
                    session_id=sid,
                    title=title,
                    created_at=data.get("created_at"),
                    workdir=(agent_config or {}).get("workdir", os.getcwd()),
                    mode=_current_mode,
                    history=(snapshot or {}).get("history", []),
                )
            elif title:
                record["title"] = title[:80]
                record = store.save(record)
        except Exception as exc:
            return _err(f"Failed to save session: {exc}", 500)
    return _ok({"id": sid, "title": record.get("title", "")})


@app.route("/api/sessions/new", methods=["POST"])
def new_session():
    global _active_session_id, _active_store_key, conversation_history, _current_mode
    global _session_store
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    requested_workdir = data.get("workdir") or (agent_config or {}).get("workdir", os.getcwd())
    target = Path(requested_workdir).expanduser().resolve()
    if not target.is_dir():
        return _err(f"directory not found: {target}", 404)
    mode = data.get("mode", "auto")
    if mode not in {"auto", "plan", "manual"}:
        mode = "auto"

    with _lock:
        if _running_ctx and str(target) != _running_ctx.workdir:
            return _err("cannot switch workspace while agent is running", 409)
        try:
            store = _store_for_workdir(str(target))
            record = store.create(workdir=str(target), mode=mode)
            if _running_ctx is None and str(target) != (agent_config or {}).get("workdir", ""):
                ensure_agent_initialized(workdir=str(target), session_id=record["id"])
            else:
                _active_session_id = record["id"]
                _active_store_key = _store_key(str(target))
                _session_store = store
                conversation_history = []
                if _running_ctx is None:
                    _current_mode = mode
        except Exception as exc:
            if 'record' in locals():
                store.delete(record["id"])
            app.logger.exception("Failed to create session")
            return _err(f"Failed to create session: {exc}", 500)
    return _ok(_session_snapshot(record["id"]))


@app.route("/api/sessions/<sid>")
def get_session(sid):
    r = _require_agent()
    if r:
        return r
    record, _store = _resolve_session(sid, request.args.get("workdir"))
    if record is None:
        return _err("session not found", 404)
    return _ok(to_jsonable(record))


@app.route("/api/sessions/<sid>", methods=["DELETE"])
def delete_session(sid):
    global _active_session_id, _active_store_key, conversation_history, _session_store
    r = _require_agent()
    if r:
        return r
    with _lock:
        if _running_ctx and _running_ctx.session_id == sid:
            return _err("cannot delete a running session", 409)
        record, store = _resolve_session(sid, request.args.get("workdir"))
        if record is None:
            return _err("session not found", 404)
        try:
            store.delete(sid)
            if _active_session_id == sid and _active_store_key == _store_key(record["workdir"]):
                replacement = store.create(
                    workdir=record.get("workdir") or (agent_config or {}).get("workdir", os.getcwd()),
                    mode=record.get("mode", "auto"),
                )
                _active_session_id = replacement["id"]
                _active_store_key = _store_key(record["workdir"])
                _session_store = store
                conversation_history = []
        except Exception as exc:
            return _err(f"Failed to delete session: {exc}", 500)
    return _ok({"deleted": sid, "session_id": _active_session_id})


@app.route("/api/sessions/<sid>/load", methods=["POST"])
def load_session(sid):
    """Select a session without mutating a different session's active run."""
    global conversation_history, _active_session_id, _active_store_key, _current_mode
    global _session_store
    r = _require_agent()
    if r:
        return r
    data = request.get_json(silent=True) or {}
    requested_workdir = data.get("workdir") or request.args.get("workdir")
    with _lock:
        record, store = _resolve_session(sid, requested_workdir)
        if record is None:
            return _err("session not found", 404)
        workdir = record.get("workdir", "")
        if _running_ctx and workdir != _running_ctx.workdir:
            return _err("cannot switch workspace while agent is running", 409)
        try:
            if _running_ctx is None and workdir != (agent_config or {}).get("workdir", ""):
                p = Path(workdir).expanduser().resolve()
                if not p.is_dir():
                    return _err(f"directory not found: {p}", 404)
                ensure_agent_initialized(workdir=str(p), session_id=sid)
            else:
                _active_session_id = sid
                _active_store_key = _store_key(workdir)
                _session_store = store
                conversation_history = list(record.get("history", []))
                if _running_ctx is None:
                    _current_mode = record.get("mode", "auto")
                    if todo_manager:
                        todo_manager.items.clear()
        except Exception as exc:
            app.logger.exception("Failed to load session")
            return _err(f"Failed to load session: {exc}", 500)
    return _ok(_session_snapshot(sid))


# ====== 任务 ======

@app.route("/api/tasks")
def list_tasks():
    r = _require_agent()
    if r:
        return r
    tasks = []
    for f in sorted(task_manager.dir.glob("task_*.json")):
        try:
            tasks.append(json.loads(f.read_text()))
        except Exception:
            pass
    return _ok({"tasks": tasks})


@app.route("/api/tasks", methods=["POST"])
def create_task():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    result = task_manager.create(data.get("subject", ""), data.get("description", ""))
    return _ok(json.loads(result))


# ====== Todo ======

@app.route("/api/todos")
def list_todos():
    r = _require_agent()
    if r:
        return r
    return _ok({"todos": todo_manager.items})


# ====== 团队 ======

@app.route("/api/team")
def list_team():
    r = _require_agent()
    if r:
        return r
    members = team_manager.team_config.get("members", [])
    return _ok({
        "team_name": team_manager.team_config.get("team_name", "default"),
        "members": members,
    })


# ====== 记忆 ======

@app.route("/api/memory/stats")
def memory_stats():
    r = _require_agent()
    if r:
        return r
    return _ok(memory_manager.get_memory_stats())


@app.route("/api/memory/search", methods=["POST"])
def memory_search():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    results = memory_manager.search_facts(
        data.get("category"), data.get("keyword"), data.get("limit", 10)
    )
    return _ok(results)


@app.route("/api/memory/store", methods=["POST"])
def memory_store():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    memory_manager.store_fact(
        data["category"], data["key"], data["value"], data.get("confidence", 1.0)
    )
    return _ok()


# ====== 安全/快照 ======

@app.route("/api/safety/checkpoints")
def list_checkpoints():
    r = _require_agent()
    if r:
        return r
    checkpoints = safety_manager.list_checkpoints(30)
    return _ok({"checkpoints": checkpoints})


@app.route("/api/safety/checkpoint", methods=["POST"])
def create_checkpoint():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    checkpoint_id = safety_manager.create_checkpoint(
        data.get("name", "manual"), data.get("description", "")
    )
    return _ok({"checkpoint_id": checkpoint_id})


@app.route("/api/safety/restore", methods=["POST"])
def restore_checkpoint():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    try:
        msg = safety_manager.restore_checkpoint(data["checkpoint_id"])
        return _ok({"message": msg})
    except Exception as e:
        return _err(str(e), 400)


@app.route("/api/safety/stats")
def safety_stats():
    r = _require_agent()
    if r:
        return r
    return _ok(safety_manager.get_safety_stats())


# ====== 文件上传 ======

@app.route("/api/files/upload", methods=["POST"])
def upload_file():
    r = _require_agent()
    if r:
        return r
    if 'file' not in request.files:
        return _err("file required")
    file = request.files['file']
    if not file.filename:
        return _err("no file selected")
    root = Path(agent_config["workdir"])
    dest = root / file.filename
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(dest))
        return _ok({"path": str(dest.relative_to(root)), "size": dest.stat().st_size})
    except Exception as e:
        return _err(str(e))


# ====== Plan 管理 ======

def _get_plans_dir() -> Path:
    """返回 workdir/.agent/plans/，退化到 CWD/.plans/"""
    if agent_config and agent_config.get("workdir"):
        p = Path(agent_config["workdir"]) / ".agent" / "plans"
    else:
        p = Path(".plans")
    p.mkdir(parents=True, exist_ok=True)
    return p


@app.route("/api/plan/save", methods=["POST"])
def save_plan():
    """将 plan 内容保存为 .md 文件"""
    data = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    title = (data.get("title") or "plan").strip()
    if not content:
        return _err("content required")
    plan_file = _get_plans_dir() / f"{title[:40].replace(' ', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.md"
    plan_file.write_text(content, encoding="utf-8")
    return _ok({"filename": plan_file.name, "path": str(plan_file)})


# ====== 文件树 ======

@app.route("/api/fs/ls")
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


@app.route("/api/fs/mkdir", methods=["POST"])
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


@app.route("/api/fs/touch", methods=["POST"])
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


@app.route("/api/files/tree")
def file_tree():
    r = _require_agent()
    if r:
        return r
    root = Path(agent_config["workdir"])
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


@app.route("/api/files/list")
def list_directory():
    """列出指定目录的直接子项（用于按需加载文件树）"""
    r = _require_agent()
    if r:
        return r
    rel = request.args.get("path", "").strip()
    root = Path(agent_config["workdir"])
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


@app.route("/api/files/content")
def file_content():
    r = _require_agent()
    if r:
        return r
    rel = request.args.get("path", "")
    if not rel:
        return _err("path required")
    root = Path(agent_config["workdir"])
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


@app.route("/api/files/content", methods=["PUT"])
def save_file_content():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    rel = (data.get("path") or "").strip()
    new_content = data.get("content", "")
    if not rel:
        return _err("path required")
    root = Path(agent_config["workdir"])
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


# ====== 文件差异 ======

@app.route("/api/files/diff", methods=["POST"])
def file_diff():
    r = _require_agent()
    if r:
        return r
    import difflib
    import subprocess
    data = request.get_json(force=True)
    rel = (data.get("path") or "").strip()
    if not rel:
        return _err("path required")
    root = Path(agent_config["workdir"])
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


# ====== 错误 ======

@app.route("/api/errors/recent")
def recent_errors():
    r = _require_agent()
    if r:
        return r
    limit = request.args.get("limit", 20, type=int)
    errors = error_manager.get_error_history(limit)
    return _ok(errors)


# ====== WebSocket ======

@socketio.on("connect")
def handle_connect():
    emit("connected", {"message": "Connected to agent", "ts": time.time()})


@socketio.on("send_message")
def handle_ws_message(data):
    data = data or {}
    message = (data.get("message") or "").strip()
    try:
        ensure_agent_initialized()
    except Exception as exc:
        _emit_request_error(f"Agent initialization failed: {exc}", data.get("session_id"))
        return
    if not message:
        _emit_request_error("无效请求", data.get("session_id"))
        return
    new_mode = data.get("mode", "auto")
    result, error, _status = _start_run(
        data.get("session_id"),
        message,
        new_mode,
        plan_mode=(new_mode == "plan"),
        title_message=message,
    )
    if error:
        _emit_request_error(error, data.get("session_id"))
        return
    snapshot = _session_snapshot(result["session_id"])
    emit("run_started", {
        **result,
        "event_seq": snapshot["event_seq"] if snapshot else 0,
    })


@socketio.on("interrupt")
def handle_interrupt(data=None):
    data = data or {}
    with _lock:
        context = _running_ctx
        if context is None:
            _emit_request_error("agent is not running", data.get("session_id"))
            return
        if data.get("session_id") and data["session_id"] != context.session_id:
            _emit_request_error("session does not own the running agent", data["session_id"])
            return
        if data.get("run_id") and data["run_id"] != context.run_id:
            _emit_request_error("run does not match the running agent", context.session_id)
            return
        context.agent.interrupt()
        emit("interrupt_requested", {
            "session_id": context.session_id,
            "run_id": context.run_id,
            "event_seq": context.event_seq,
            "ts": time.time(),
        })


# ====== 启动 ======

def run_server(host="0.0.0.0", port=8000, debug=False):
    print("正在初始化 Agent...")
    ensure_agent_initialized()
    print(f"API 服务启动: http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_server(debug=False)
