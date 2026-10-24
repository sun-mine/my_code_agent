"""Agent 初始化、system prompt 构建与模型探测。"""
import json
import os
import platform
import subprocess

from anthropic import Anthropic

import api_server
from api import callbacks
from api.session_helpers import _store_for_workdir, _store_key
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
from core.loop import AgentLoop
from tools import (
    create_all_handlers, get_core_tools,
    get_tools_by_categories, create_handlers_for_categories, get_all_categories,
)
from tools.base import set_workdir
from core.subagent import run_subagent


def _build_environment_context(workdir: str) -> str:
    """动态收集运行环境上下文"""
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
    base_url = (api_server._cfg.base_url if api_server._cfg else "").lower()
    return "11434" in base_url or "ollama" in base_url


def _detect_available_models() -> list:
    """Based on Config, detect which models are available"""
    base_url = (api_server._cfg.base_url if api_server._cfg else "").lower()

    if _is_ollama():
        # 从本地 Ollama 拉取支持对话的模型
        try:
            import urllib.request
            with urllib.request.urlopen(f"{api_server._cfg.base_url}/api/tags", timeout=2) as r:
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
        return [{"id": api_server._cfg.model, "label": api_server._cfg.model, "provider": "Ollama"}]

    if "deepseek" in base_url:
        return list(_PROVIDER_MODELS["deepseek"])
    if "openai" in base_url:
        return list(_PROVIDER_MODELS["openai"])
    return list(_PROVIDER_MODELS["anthropic"])


def init_agent(workdir: str = None, session_id: str = None):
    """初始化所有组件"""
    cfg = Config(workdir=workdir)
    api_server._cfg = cfg  # 缓存供动态重建 system prompt 使用
    set_workdir(str(cfg.workdir))  # 同步 WORKDIR 到工具模块
    client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url)
    model = cfg.model

    skills = SkillLoader(cfg.skills_dir)
    api_server._skills = skills  # 缓存供动态重建 system prompt 使用
    bus = MessageBus(cfg.team_dir / "inbox")
    api_server.task_manager = TaskManager(cfg.tasks_dir)
    api_server.bg_manager = BackgroundManager()
    api_server.team_manager = TeammateManager(bus, api_server.task_manager, client, model, cfg)
    api_server.todo_manager = TodoManager()
    api_server.memory_manager = MemoryManager(cfg.workdir)
    api_server.safety_manager = SafetyManager(cfg.workdir)
    api_server.error_manager = ErrorRecoveryManager(
        cfg.workdir, api_server.memory_manager, api_server.safety_manager
    )
    init_error_recovery(api_server.error_manager)
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
        todo_manager=api_server.todo_manager,
        skill_loader=skills,
        task_manager=api_server.task_manager,
        message_bus=bus,
        team_manager=api_server.team_manager,
        background=api_server.bg_manager,
        config=cfg,
        memory=api_server.memory_manager,
        safety=api_server.safety_manager,
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
            todo_manager=api_server.todo_manager,
            skill_loader=skills,
            task_manager=api_server.task_manager,
            message_bus=bus,
            team_manager=api_server.team_manager,
            background=api_server.bg_manager,
            config=cfg,
            memory=api_server.memory_manager,
            safety=api_server.safety_manager,
            compactor=None,
        )
        return new_tools, new_handlers

    system = _build_system_prompt(cfg, skills)

    api_server.agent_instance = AgentLoop(
        client=client,
        model=model,
        system=system,
        tools=tools,
        tool_handlers=handlers,
        bg=api_server.bg_manager,
        bus=bus,
        todo_manager=api_server.todo_manager,
        token_threshold=cfg.token_threshold,
        event_callback=callbacks._push_event,
        approval_callback=callbacks._approval_callback,
        tool_loader=_load_tool_categories,
    )

    api_server.agent_config = {
        "workdir": str(cfg.workdir),
        "model": model,
        "system_prompt_len": len(system),
        "tool_definitions_len": len(json.dumps(tools, default=str)),
        "token_threshold": cfg.token_threshold,
    }
    store = _store_for_workdir(str(cfg.workdir))
    api_server._session_store = store
    store_key = _store_key(str(cfg.workdir))
    selected_id = session_id
    if selected_id is None and api_server._active_store_key == store_key:
        selected_id = api_server._active_session_id
    selected = store.get(selected_id) if selected_id else None
    if selected is None or selected.get("workdir") != str(cfg.workdir):
        selected = store.create(workdir=str(cfg.workdir), mode=api_server._current_mode)
    api_server._active_session_id = selected["id"]
    api_server._active_store_key = store_key
    api_server.conversation_history = selected.get("history", [])


def ensure_agent_initialized(workdir: str = None, session_id: str = None):
    """Initialize the Agent once, including when Flask is hosted by an app server."""
    with api_server._agent_init_lock:
        if api_server.agent_instance is not None and workdir is None:
            return
        init_agent(workdir=workdir, session_id=session_id)


def _require_agent():
    if not api_server.agent_instance:
        try:
            api_server.ensure_agent_initialized()
        except Exception as exc:
            api_server.app.logger.exception("Agent initialization failed")
            from api.session_helpers import _err
            return _err(f"Agent initialization failed: {exc}", 503)
    if not api_server.agent_instance:
        from api.session_helpers import _err
        return _err("Agent not initialized", 503)
