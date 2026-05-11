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
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

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
from core.loop import AgentLoop
from tools import get_all_tools, create_all_handlers, get_core_tools, get_tools_by_categories, create_handlers_for_categories, get_all_categories
from tools.base import set_workdir
from core.subagent import run_subagent

# ====== Flask & SocketIO ======
app = Flask(__name__)
CORS(app, origins="*")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ====== 全局状态 ======
_lock = threading.Lock()
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

# ====== 工具审批状态 ======
_pending_approvals: dict = {}   # tool_use_id -> (threading.Event, [bool])
_approval_lock = threading.Lock()
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


def _should_approve(tool_name: str, tool_input: dict = None) -> bool:
    """根据当前模式和工具类型判断是否需要审批"""
    if tool_name in _SAFE_TOOLS:
        return False  # 安全工具永远不需要审批

    # bash / background_run 检查是否存在危险命令
    if tool_name in _COMMAND_TOOLS:
        cmd = (tool_input or {}).get("command", "")
        if _is_dangerous_command(cmd):
            return True  # 高危命令始终需要审批

    if _current_mode == "auto":
        return False  # Auto 模式：非高危操作全自动
    if _current_mode == "plan":
        return False  # Plan 执行阶段：非高危操作全自动

    # Manual 模式：写操作和命令都需要审批
    if _current_mode == "manual":
        return tool_name in _WRITE_TOOLS or tool_name in _COMMAND_TOOLS

    return False


def _push_event(event: dict):
    """将 AgentLoop 事件推送到所有 SocketIO 客户端"""
    socketio.emit("agent_event", event)


def _approval_callback(tool_use_id: str, name: str, inp: dict) -> bool:
    """工具执行前请求用户审批，根据模式和工具类型决定是否阻塞"""
    if not _should_approve(name, inp):
        return True  # 不需要审批，直接通过

    # 真正需要审批时才推送事件给前端
    socketio.emit("agent_event", {
        "type": "tool_approval_request",
        "name": name,
        "input": inp,
        "tool_use_id": tool_use_id,
        "ts": time.time(),
    })

    event = threading.Event()
    result = [True]
    with _approval_lock:
        _pending_approvals[tool_use_id] = (event, result)

    # 每秒轮询一次，以便响应中断信号
    deadline = time.time() + 120
    while time.time() < deadline:
        if event.wait(timeout=1.0):
            break
        if agent_instance and agent_instance._interrupt_flag.is_set():
            result[0] = False
            break

    with _approval_lock:
        _pending_approvals.pop(tool_use_id, None)
    return result[0]


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


def init_agent(workdir: str = None):
    """初始化所有组件"""
    global agent_instance, conversation_history
    global memory_manager, safety_manager, error_manager
    global todo_manager, task_manager, team_manager, bg_manager, agent_config
    global _cfg, _skills

    cfg = Config(workdir=workdir)
    _cfg = cfg  # 缓存供动态重建 system prompt 使用
    set_workdir(str(cfg.workdir))  # 同步 WORKDIR 到工具模块
    client = Anthropic(
        api_key=os.getenv("ANTHROPIC_AUTH_TOKEN"),
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
    )
    model = os.getenv("MODEL_ID", "deepseek-v4-flash")

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
    conversation_history = []


# ====== Helper ======

def _ok(data=None):
    return jsonify({"success": True, "data": data, "ts": time.time()})


def _err(msg, code=400):
    return jsonify({"success": False, "error": str(msg), "ts": time.time()}), code


def _require_agent():
    if not agent_instance:
        return _err("Agent not initialized", 503)


# ====== 系统 ======

@app.route("/api/status")
def get_status():
    r = _require_agent()
    if r:
        return r
    # Token usage estimate
    estimated = estimate_tokens(conversation_history)
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
        "run_id": agent_instance.current_run_id,
        "workdir": agent_config["workdir"] if agent_config else "",
        "model": agent_config["model"] if agent_config else "",
        "history_length": len(conversation_history),
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
    if _is_running:
        return _err("agent is already running", 409)

    global _current_mode
    new_mode = data.get("mode", "auto")
    if new_mode in ("plan", "auto", "manual"):
        _current_mode = new_mode
        # 动态更新 system prompt 以反映当前模式
        if agent_instance and _cfg and _skills:
            agent_instance.system = _build_system_prompt(_cfg, _skills, _current_mode)

    run_id = str(uuid.uuid4())
    conversation_history.append({"role": "user", "content": message})

    def _run():
        global _is_running
        _is_running = True
        try:
            agent_instance.run(conversation_history, run_id=run_id, plan_mode=(_current_mode == "plan"))
        except Exception as e:
            socketio.emit("agent_event", {
                "type": "error",
                "message": str(e),
                "run_id": run_id,
                "ts": time.time(),
            })
        finally:
            _is_running = False

    global _run_thread
    _run_thread = threading.Thread(target=_run, daemon=True)
    _run_thread.start()

    return _ok({"run_id": run_id, "status": "started"})


# ====== Plan 执行 ======

@app.route("/api/plan/execute", methods=["POST"])
def plan_execute():
    """后端处理计划批准：注入执行指令后切换到 auto 模式运行"""
    r = _require_agent()
    if r:
        return r
    if _is_running:
        return _err("agent is already running", 409)

    data = request.get_json(force=True)
    plan_content = (data.get("plan_content") or "").strip()
    todos = data.get("todos") or []
    if not plan_content:
        return _err("plan_content required")

    global _current_mode
    _current_mode = "auto"
    if agent_instance and _cfg and _skills:
        agent_instance.system = _build_system_prompt(_cfg, _skills, "auto")

    run_id = str(uuid.uuid4())

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
    conversation_history.append({"role": "user", "content": execute_instruction})

    def _run():
        global _is_running
        _is_running = True
        try:
            agent_instance.run(conversation_history, run_id=run_id, plan_mode=False)
        except Exception as e:
            socketio.emit("agent_event", {
                "type": "error",
                "message": str(e),
                "run_id": run_id,
                "ts": time.time(),
            })
        finally:
            _is_running = False

    global _run_thread
    _run_thread = threading.Thread(target=_run, daemon=True)
    _run_thread.start()

    return _ok({"run_id": run_id, "status": "executing"})


@app.route("/api/chat/interrupt", methods=["POST"])
def interrupt_chat():
    r = _require_agent()
    if r:
        return r
    agent_instance.interrupt()
    return _ok({"interrupted": True})


@app.route("/api/chat/tool_approve", methods=["POST"])
def tool_approve():
    """批准或拒绝一个工具调用"""
    data = request.get_json(force=True)
    tool_use_id = (data.get("tool_use_id") or "").strip()
    approved = bool(data.get("approved", True))
    if not tool_use_id:
        return _err("tool_use_id required")
    with _approval_lock:
        entry = _pending_approvals.get(tool_use_id)
    if entry:
        event, result = entry
        result[0] = approved
        event.set()
        return _ok({"tool_use_id": tool_use_id, "approved": approved})
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


def _detect_available_models() -> list:
    """Based on .env configuration, detect which models are available"""
    import os
    models = []
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").lower()

    if "deepseek" in base_url:
        models.extend(_PROVIDER_MODELS["deepseek"])
    elif "openai" in base_url:
        models.extend(_PROVIDER_MODELS["openai"])
    else:
        models.extend(_PROVIDER_MODELS["anthropic"])

    # Always add Anthropic models if a separate Anthropic key exists
    if os.getenv("ANTHROPIC_AUTH_TOKEN") and "deepseek" in base_url:
        # User has DeepSeek as primary, but they can switch
        pass  # Only show detected provider models

    return models


@app.route("/api/models")
def list_models():
    models = _detect_available_models()
    current = os.getenv("MODEL_ID", models[0]["id"] if models else "")
    return _ok({"models": models, "current": current})


@app.route("/api/model")
def get_model():
    if not agent_config:
        return _ok({"model": os.getenv("MODEL_ID", "claude-sonnet-4-20250514")})
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
    allowed = ("claude-", "deepseek-", "deepseek-v4-")
    if not any(new_model.startswith(p) for p in allowed):
        return _err(f"unsupported model: {new_model}")
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
    global conversation_history, _is_running
    if _is_running:
        return _err("agent is running, please interrupt first", 409)
    data = request.get_json(force=True)
    new_dir = (data.get("workdir") or "").strip()
    if not new_dir:
        return _err("workdir required")
    p = Path(new_dir).expanduser().resolve()
    if not p.is_dir():
        return _err(f"directory not found: {p}", 404)
    try:
        init_agent(workdir=str(p))
        conversation_history = []
        return _ok({"workdir": str(p), "message": "re-initialized"})
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/history")
def get_history():
    serializable = []
    for msg in conversation_history[-200:]:
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for blk in content:
                _val = lambda k, d=None: blk.get(k, d) if isinstance(blk, dict) else getattr(blk, k, d)
                if _val("text") is not None and _val("type") != "thinking":
                    parts.append({"type": "text", "text": _val("text")})
                elif _val("type") == "tool_use":
                    parts.append({"type": "tool_use", "name": _val("name", ""), "tool_use_id": _val("id", ""), "input": _val("input", {})})
                elif _val("type") == "tool_result":
                    parts.append({"type": "tool_result", "tool_use_id": _val("tool_use_id", ""), "content": str(_val("content", ""))[:500]})
                elif _val("type") == "thinking":
                    parts.append({"type": "thinking", "text": _val("thinking", "") or _val("text", "")})
                else:
                    parts.append({"type": _val("type", "unknown")})
            serializable.append({"role": msg["role"], "content": parts})
        else:
            serializable.append({"role": msg["role"], "content": content})
    return _ok({"history": serializable, "total": len(conversation_history)})


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    global conversation_history
    conversation_history = []
    if agent_instance:
        agent_instance.reset_interrupt()
    return _ok()


# ====== 会话管理 ======

def _get_sessions_dir() -> Path:
    """返回 workdir/.agent/sessions/，退化到 CWD/.sessions/"""
    if agent_config and agent_config.get("workdir"):
        p = Path(agent_config["workdir"]) / ".agent" / "sessions"
    else:
        p = Path(".sessions")
    p.mkdir(parents=True, exist_ok=True)
    return p


@app.route("/api/sessions")
def list_sessions():
    sessions = []
    d = _get_sessions_dir()
    for f in sorted(d.glob("session_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "id": data.get("id", f.stem),
                "title": data.get("title", ""),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "message_count": data.get("message_count", 0),
                "workdir": data.get("workdir", ""),
            })
        except Exception:
            pass
    return _ok({"sessions": sessions})


@app.route("/api/sessions", methods=["POST"])
def create_session():
    """保存当前会话到 .sessions/"""
    data = request.get_json(force=True)
    sid = data.get("id") or f"session_{int(time.time())}"
    title = (data.get("title") or "").strip()
    title = title[:80] if title else f"会话 {time.strftime('%m-%d %H:%M')}"

    def _serialize_content(content) -> str:
        """序列化消息内容为可存储的字符串"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for blk in content:
                if isinstance(blk, dict):
                    if blk.get("type") == "text":
                        texts.append(blk.get("text", ""))
                    elif blk.get("type") == "tool_use":
                        texts.append(f"[工具调用: {blk.get('name', 'unknown')}]")
                    elif blk.get("type") == "tool_result":
                        txt = str(blk.get("content", ""))
                        texts.append(txt[:500])
                elif hasattr(blk, "text"):
                    texts.append(blk.text)
                elif hasattr(blk, "type") and getattr(blk, "type", "") == "tool_use":
                    texts.append(f"[工具调用: {getattr(blk, 'name', 'unknown')}]")
            return "\n".join(texts) if texts else "[complex content]"
        return str(content)[:2000]

    session_file = _get_sessions_dir() / f"{sid}.json"
    payload = {
        "id": sid,
        "title": title,
        "created_at": data.get("created_at", time.time()),
        "updated_at": time.time(),
        "message_count": data.get("message_count", 0),
        "workdir": agent_config.get("workdir", "") if agent_config else "",
        "history": [
            {
                "role": m["role"],
                "content": _serialize_content(m["content"]),
            }
            for m in conversation_history[-200:]
        ],
        "mode": _current_mode,
    }
    session_file.write_text(json.dumps(payload, ensure_ascii=False, default=str))
    return _ok({"id": sid, "title": title})


@app.route("/api/sessions/<sid>")
def get_session(sid):
    session_file = _get_sessions_dir() / f"{sid}.json"
    if not session_file.exists():
        return _err("session not found", 404)
    try:
        return _ok(json.loads(session_file.read_text()))
    except Exception as e:
        return _err(str(e))


@app.route("/api/sessions/<sid>", methods=["DELETE"])
def delete_session(sid):
    session_file = _get_sessions_dir() / f"{sid}.json"
    if not session_file.exists():
        return _err("session not found", 404)
    session_file.unlink()
    return _ok({"deleted": sid})


@app.route("/api/sessions/<sid>/load", methods=["POST"])
def load_session(sid):
    """恢复会话历史到当前 agent，隔离会话状态"""
    global conversation_history
    session_file = _get_sessions_dir() / f"{sid}.json"
    if not session_file.exists():
        return _err("session not found", 404)
    try:
        data = json.loads(session_file.read_text())
        history = data.get("history", [])
        conversation_history = history

        # 清空 todo 列表和任务状态，确保会话隔离
        if todo_manager:
            todo_manager.items.clear()
        if task_manager:
            for f in task_manager.dir.glob("task_*.json"):
                try:
                    d = json.loads(f.read_text())
                    if d.get("status") == "in_progress":
                        d["status"] = "pending"
                        f.write_text(json.dumps(d, ensure_ascii=False))
                except Exception:
                    pass

        # 恢复工作目录（如果会话中有记录且不同）
        saved_workdir = data.get("workdir", "")
        if saved_workdir and agent_config and saved_workdir != agent_config.get("workdir", ""):
            p = Path(saved_workdir)
            if p.is_dir():
                try:
                    init_agent(workdir=str(p))
                except Exception:
                    pass

        return _ok({
            "history": history,
            "title": data.get("title", ""),
            "workdir": data.get("workdir", ""),
            "mode": data.get("mode", "auto"),
        })
    except Exception as e:
        return _err(str(e))


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
    target = Path(path).resolve()
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
    global _is_running, _run_thread, _current_mode
    message = (data.get("message") or "").strip()
    if not message or not agent_instance:
        emit("error_event", {"message": "invalid request"})
        return
    if _is_running:
        emit("error_event", {"message": "agent already running"})
        return

    new_mode = data.get("mode", "auto")
    if new_mode in ("plan", "auto", "manual"):
        _current_mode = new_mode

    run_id = str(uuid.uuid4())
    conversation_history.append({"role": "user", "content": message})

    def _run():
        global _is_running
        _is_running = True
        try:
            agent_instance.run(conversation_history, run_id=run_id, plan_mode=(_current_mode == "plan"))
        except Exception as e:
            socketio.emit("agent_event", {
                "type": "error", "message": str(e),
                "run_id": run_id, "ts": time.time(),
            })
        finally:
            _is_running = False

    _run_thread = threading.Thread(target=_run, daemon=True)
    _run_thread.start()
    emit("run_started", {"run_id": run_id})


@socketio.on("interrupt")
def handle_interrupt(_data=None):
    if agent_instance:
        agent_instance.interrupt()
        emit("interrupted", {"ts": time.time()})


# ====== 启动 ======

def run_server(host="0.0.0.0", port=5000, debug=False):
    print("正在初始化 Agent...")
    init_agent()
    print(f"API 服务启动: http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_server(debug=False)
