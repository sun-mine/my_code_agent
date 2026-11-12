import json
import inspect
import re
import threading
import time
import uuid
from typing import List, Dict, Any, Callable, Optional
from anthropic import Anthropic
from anthropic.types import TextBlock, ToolUseBlock
from components.compactor import microcompact, auto_compact, estimate_tokens
from components.background_manager import BackgroundManager
from components.command_runner import CancellationToken, ToolResult
from components.message_bus import MessageBus
from core.continuation import needs_action_continuation, continuation_prompt, unfinished_notice
from core.run_policy import RunLimits, RunBudgetExceeded
from core.response_language import (
    request_language, response_language_instructions, response_language_reminder,
    with_response_language_reminder,
)

# 需要用户审批才能执行的工具（在 manual 模式下会触发审批回调）
APPROVAL_TOOLS = {"bash", "write_file", "edit_file", "task", "background_run"}

# Tools that are safe/expected during plan mode (don't break the planning flow)
_PLANNING_TOOLS = {"TodoWrite", "load_tools", "read_file", "list_files", "glob", "grep", "lookup_symbol", "memory_recall", "memory_store", "choices"}


class AgentInterrupted(Exception):
    """用户中断执行异常"""
    pass


# Tools where plain text content maps to a specific parameter
_PRIMARY_PARAM: Dict[str, str] = {
    'bash': 'command',
    'background_run': 'command',
    'read_file': 'path',
    'list_files': 'path',
    'glob': 'pattern',
    'grep': 'pattern',
    'lookup_symbol': 'symbol',
    'write_file': 'path',       # plain text → path; multi-param needs key:value
    'edit_file': 'path',
    'task': 'prompt',
    'TodoWrite': 'items',
    'memory_store': 'key',
    'memory_recall': 'query',
    'memory_search': 'query',
    'safety_checkpoint': 'name',
    'task_create': 'subject',
    'task_get': 'task_id',
    'claim_task': 'task_id',
    'load_skill': 'name',
    'broadcast': 'content',
    'send_message': 'content',
    'spawn_teammate': 'prompt',
    'shutdown_request': 'teammate',
    'choices': 'options',
}


def _strip_xml_tools(text: str, known_tools: set) -> str:
    """Remove XML-style tool call tags from text for clean display."""
    if not known_tools:
        return text
    tool_names_pat = '|'.join(re.escape(t) for t in sorted(known_tools, key=len, reverse=True))
    pattern = re.compile(rf'<({tool_names_pat})>.*?</\1>', re.DOTALL)
    return pattern.sub('', text).strip()


def _parse_choices_options(raw: str) -> list:
    """Parse choices XML body into structured options list.

    Format: each non-empty line is 'id: label' or 'id: label\\n description'
    """
    options = []
    lines = raw.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if ':' in line:
            opt_id, label = line.split(':', 1)
            opt_id = opt_id.strip()
            label = label.strip()
        else:
            opt_id = label = line
        description = ''
        i += 1
        # Collect description lines until next ':' line or empty line
        while i < len(lines):
            nl = lines[i].strip()
            if not nl:
                i += 1
                break
            if ':' in nl and nl.split(':', 1)[0].strip() not in ('-', '*'):
                break
            description += nl + '\n'
            i += 1
        options.append({
            "id": opt_id,
            "label": label,
            "description": description.strip() or None,
        })
    return options


def _try_parse_json(value: str):
    """Attempt to parse a string as JSON; return parsed value on success, original string on failure."""
    stripped = value.strip()
    if not stripped or stripped[0] not in '[{':
        return value
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return value


def _parse_xml_tool_calls(text: str, known_tools: set) -> list:
    """Parse XML-style tool calls from DeepSeek text output.

    Handles three formats:
      1. <tool_name>plain text content</tool_name>
      2. <tool_name>key1: value1 key2: value2</tool_name>
      3. <tool_name><param>value</param></tool_name>

    Returns a list of dicts: {"name": str, "input": dict}
    """
    results = []
    tool_names_pat = '|'.join(re.escape(t) for t in sorted(known_tools, key=len, reverse=True))
    if not tool_names_pat:
        return results

    # Find all XML tool blocks: <tool_name>...</tool_name>
    pattern = re.compile(
        rf'<({tool_names_pat})>\s*(.*?)\s*</\1>',
        re.DOTALL
    )

    for match in pattern.finditer(text):
        tool_name = match.group(1)
        body = match.group(2).strip()
        if not body:
            continue
        params: Dict[str, Any] = {}

        # Format 3: nested <param>value</param> tags
        nested = re.findall(r'<(\w+)>(.*?)</\1>', body, re.DOTALL)
        if nested:
            for k, v in nested:
                params[k] = _try_parse_json(v.strip())
            if params:
                results.append({"name": tool_name, "input": params})
                continue

        # Format 2: key:value pairs (e.g. "file_path: /x content: hello")
        kv_pattern = re.compile(r'(\w+):\s*(.+?)(?=\s+\w+:\s|\s*$)', re.DOTALL)
        kv_matches = kv_pattern.findall(body)
        if kv_matches:
            for k, v in kv_matches:
                # Normalize common aliases
                k_norm = {'file_path': 'path', 'command_line': 'command',
                          'search_pattern': 'pattern', 'prompt_text': 'prompt'}.get(k, k)
                params[k_norm] = _try_parse_json(v.strip())
            if params:
                results.append({"name": tool_name, "input": params})
                continue

        # Format 1: plain text → map to primary parameter
        primary = _PRIMARY_PARAM.get(tool_name)
        if primary:
            params[primary] = _try_parse_json(body)
            results.append({"name": tool_name, "input": params})

    return results


class _SyntheticToolUse:
    """Minimal object that mimics anthropic.types.ToolUseBlock for parsed XML tool calls."""
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict):
        self.id = id
        self.name = name
        self.input = input


class AgentLoop:
    def __init__(
        self,
        client: Anthropic,
        model: str,
        system: str,
        tools: List[Dict],
        tool_handlers: Dict[str, Callable],
        bg: BackgroundManager,
        bus: MessageBus,
        todo_manager=None,
        token_threshold: int = 100000,
        event_callback: Optional[Callable[[Dict], None]] = None,
        approval_callback: Optional[Callable[[str, str, Dict], bool]] = None,
        tool_loader: Optional[Callable[[list], tuple]] = None,
        limits: Optional[RunLimits] = None,
        verification_command: Optional[str] = None,
        action_continuation: bool = True,
        context_tools: Optional[Callable[[Any], list]] = None,
        read_only_tools=(),
    ):
        self.client = client
        self.model = model
        self.system = system
        self.tools = tools
        self.tool_handlers = tool_handlers
        self.bg = bg
        self.bus = bus
        self.todo = todo_manager
        self.token_threshold = token_threshold
        self.event_callback = event_callback  # 实时事件回调（前端推送用）
        self.approval_callback = approval_callback  # 工具审批回调
        self.tool_loader = tool_loader  # 按需加载工具回调: (categories) -> (new_tools, new_handlers)
        self.limits = limits or RunLimits()
        self.verification_command = verification_command
        self.action_continuation = action_continuation
        self.context_tools = context_tools
        self.read_only_tools = frozenset(read_only_tools)
        self.planning_tools = _PLANNING_TOOLS | self.read_only_tools
        self._budget_reason = None
        self._run_deadline = None
        self._rounds_without_todo = 0
        self._interrupt_flag = threading.Event()  # 中断信号
        self._cancel_lock = threading.RLock()
        self._cancel_token = CancellationToken()
        self._current_run_id: Optional[str] = None
        self._state = "idle"  # idle | thinking | executing_tool

    def _emit(self, event: Dict):
        """发送结构化事件（stdout + 回调）"""
        event_type = event.get("type", "unknown")
        try:
            line = json.dumps(event, ensure_ascii=False)
        except Exception:
            line = str(event)
        if event_type == "chunk":
            print(f"AGENT_CHUNK: {line}")
        else:
            print(f"AGENT_EVENT: {line}")
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception:
                pass

    def _check_interrupt(self):
        if self._interrupt_flag.is_set():
            raise AgentInterrupted("执行被用户中断")
        if self._budget_exhausted():
            raise RunBudgetExceeded(self._budget_reason)

    def _budget_exhausted(self):
        if self._run_deadline is not None and time.monotonic() >= self._run_deadline:
            self._budget_reason = self._budget_reason or "max_seconds"
        return self._budget_reason is not None

    def interrupt(self):
        """外部调用：发起中断请求"""
        with self._cancel_lock:
            if self._current_run_id is None:
                return False
            self._interrupt_flag.set()
            run_id = self._current_run_id
            active = self._state != "idle"
            if active:
                self._state = "cancelling"
                self._emit({
                    "type": "agent_state", "state": "cancelling",
                    "run_id": run_id, "ts": time.time(),
                })
            self._cancel_token.cancel()
            return True

    def prepare_run(self, run_id: Optional[str] = None) -> str:
        """Publish a fresh run cancellation scope before its thread starts."""
        prepared_id = run_id or str(uuid.uuid4())
        with self._cancel_lock:
            if self._current_run_id == prepared_id:
                return prepared_id
            if self._current_run_id is not None:
                raise RuntimeError("Another run is already active")
            self._cancel_token = CancellationToken()
            self._interrupt_flag.clear()
            self._budget_reason = None
            self._run_deadline = None
            self._current_run_id = prepared_id
        return prepared_id

    def release_run(self, run_id: str) -> bool:
        """Release only the matching prepared or finished run."""
        with self._cancel_lock:
            if self._current_run_id != run_id:
                return False
            self._state = "idle"
            self._current_run_id = None
            self._run_deadline = None
            return True

    def _handler_kwargs(self, handler: Callable, tool_input: Dict) -> Dict:
        kwargs = dict(tool_input)
        try:
            params = inspect.signature(handler).parameters.values()
            accepts_extra = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
            names = {p.name for p in params}
        except (TypeError, ValueError):
            accepts_extra, names = False, set()
        if accepts_extra or "_cancel_token" in names:
            kwargs["_cancel_token"] = self._cancel_token
        if accepts_extra or "_run_id" in names:
            kwargs["_run_id"] = self._current_run_id
        return kwargs

    @staticmethod
    def _tool_output(output):
        if isinstance(output, ToolResult):
            return output.model_content(), str(output), output.event_fields()
        text = str(output)
        is_error = text.lstrip().startswith(("Error:", "Failed ", "Unknown tool:"))
        return text, text, {"is_error": is_error}

    def _record_tool_result(self, results: list, block, output, *, purpose=None):
        content, visible, fields = self._tool_output(output)
        results.append({
            "type": "tool_result", "tool_use_id": block.id,
            "content": content, "is_error": fields["is_error"],
        })
        self._emit({
            "type": "tool_result", "name": block.name, "tool_name": block.name,
            "output": visible[:2000], **fields, "tool_use_id": block.id,
            **({"purpose": purpose} if purpose else {}),
            "run_id": self._current_run_id, "ts": time.time(),
        })

    def _load_context(self, messages: list) -> int:
        """Fetch explicit user-linked context through the normal tool event format."""
        if not self.context_tools or not messages or messages[-1].get("role") != "user":
            return 0
        calls = self.context_tools(messages[-1].get("content", ""))
        count = 0
        for call in calls:
            if self._interrupt_flag.is_set() or self._budget_exhausted():
                break
            if count >= self.limits.max_tool_calls:
                self._budget_reason = "max_tool_calls"
                break
            name = call["name"]
            if name not in self.read_only_tools or name not in self.tool_handlers:
                raise ValueError(f"Context tool is not registered as read-only: {name}")
            block = ToolUseBlock(type="tool_use", id=f"context_{uuid.uuid4().hex[:12]}",
                                 name=name, input=call["input"])
            messages.append({"role": "assistant", "content": [block]})
            self._state = "executing_tool"
            self._emit({"type": "tool_start", "name": name, "input": block.input,
                        "purpose": "context", "tool_use_id": block.id,
                        "run_id": self._current_run_id, "ts": time.time()})
            handler = self.tool_handlers[name]
            try:
                if self._interrupt_flag.is_set() or self._budget_exhausted() or self._cancel_token.is_cancelled:
                    output = ToolResult("Error: Context fetch cancelled", is_error=True, cancelled=True)
                else:
                    output = handler(**self._handler_kwargs(handler, block.input))
            except Exception as exc:
                output = ToolResult(f"Error: {exc}", is_error=True)
            results = []
            self._record_tool_result(results, block, output, purpose="context")
            messages.append({"role": "user", "content": results})
            count += 1
        return count

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_run_id(self) -> Optional[str]:
        return self._current_run_id

    def run(self, messages: list, run_id: Optional[str] = None, plan_mode: bool = False,
            load_context: bool = True) -> str:
        """
        运行主循环。
        run_id: 本次运行的唯一标识，用于前端跟踪与中断控制；若不传则自动生成。
        plan_mode: 是否为 plan 模式（先生成计划，暂停等待用户确认）
        返回最终文本响应（供同步场景使用）。
        """
        run_id = self.prepare_run(run_id)
        self._state = "thinking"
        final_text = ""
        plan_text = ""        # Accumulate all assistant text in plan mode
        plan_todo_used = False  # Only fire plan_proposed after TodoWrite was used
        request_system = self.system + "\n\n" + response_language_instructions(messages)
        language_reminder = response_language_reminder(messages)
        language = request_language(messages)
        continuation_attempts = 0
        pending_continuation = None
        rounds = tool_calls = tokens_used = repair_attempts = 0
        outcome, stop_reason = "completed", "end_turn"
        deadline = time.monotonic() + self.limits.max_seconds
        self._run_deadline = deadline
        run_token = self._cancel_token

        def expire():
            with self._cancel_lock:
                if self._current_run_id != run_id:
                    return
                self._budget_reason = "max_seconds"
            run_token.cancel()

        timer = threading.Timer(self.limits.max_seconds, expire)
        timer.daemon = True
        timer.start()

        self._emit({
            "type": "run_start",
            "run_id": self._current_run_id,
            "ts": time.time(),
        })

        try:
            if load_context:
                tool_calls = self._load_context(messages)
            while True:
                self._check_interrupt()
                if rounds >= self.limits.max_rounds:
                    raise RunBudgetExceeded("max_rounds")
                rounds += 1
                microcompact(messages)
                if estimate_tokens(messages) > self.token_threshold:
                    self._emit({"type": "system", "message": "auto-compact", "ts": time.time()})
                    messages[:] = auto_compact(self.client, self.model, messages)

                try:
                    notifs = self.bg.drain(self._current_run_id)
                except TypeError:  # compatibility with simple/custom managers
                    notifs = self.bg.drain()
                if notifs:
                    txt = "\n".join(f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs)
                    messages.append({"role": "user", "content": f"<background-results>\n{txt}\n</background-results>"})

                inbox = self.bus.read_inbox("lead")
                if inbox:
                    messages.append({"role": "user", "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"})

                self._check_interrupt()
                self._state = "thinking"
                self._emit({"type": "agent_state", "state": "thinking", "run_id": self._current_run_id, "ts": time.time()})

                # Plan mode: only expose planning tools to prevent premature execution
                api_tools: List[Dict] = [t for t in self.tools if t['name'] in self.planning_tools] if plan_mode else self.tools

                # Use streaming API — capture text + thinking in real-time, batched
                round_text = ""
                _batch_text = ""
                _batch_thinking = ""
                _last_flush = time.time()
                def _flush():
                    nonlocal _batch_text, _batch_thinking, _last_flush
                    if _batch_thinking:
                        self._emit({
                            "type": "thinking",
                            "text": _batch_thinking,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
                        _batch_thinking = ""
                    if _batch_text:
                        self._emit({
                            "type": "chunk",
                            "text": _batch_text,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
                        _batch_text = ""
                    _last_flush = time.time()

                request_messages = messages
                if pending_continuation:
                    # Keep each streamed assistant round in history, while the
                    # controller's recovery instruction stays request-local.
                    request_messages = [*messages, {"role": "user", "content": pending_continuation}]
                    pending_continuation = None
                try:
                    request_client = self.client
                    if hasattr(request_client, "with_options"):
                        request_client = request_client.with_options(
                            timeout=max(0.01, deadline - time.monotonic()), max_retries=0,
                        )
                    self._check_interrupt()
                    with request_client.messages.stream(
                        model=self.model,
                        system=request_system,
                        messages=with_response_language_reminder(request_messages, language_reminder),
                        tools=api_tools,
                        max_tokens=min(8000, self.limits.max_tokens - tokens_used),
                    ) as stream:
                        closer = self._cancel_token.register_closer(
                            getattr(stream, "close", lambda: None)
                        )
                        try:
                            self._check_interrupt()
                            for event in stream:
                                self._check_interrupt()
                                ev_type = getattr(event, 'type', None)
                                if ev_type == 'text':
                                    chunk = getattr(event, 'text', '')
                                    if chunk:
                                        round_text += chunk
                                        _batch_text += chunk
                                elif ev_type == 'thinking':
                                    chunk = getattr(event, 'thinking', '')
                                    if chunk:
                                        _batch_thinking += chunk
                                if _batch_thinking or _batch_text:
                                    total = len(_batch_thinking) + len(_batch_text)
                                    if total >= 20 or (time.time() - _last_flush) >= 0.08:
                                        _flush()
                            response = stream.get_final_message()
                        finally:
                            self._cancel_token.unregister_closer(closer)
                except Exception:
                    self._check_interrupt()
                    raise
                _flush()  # flush remaining

                if round_text:
                    final_text = round_text
                    if plan_mode:
                        plan_text = (plan_text + "\n\n" + round_text).strip() if plan_text else round_text
                messages.append({"role": "assistant", "content": response.content})
                self._check_interrupt()

                # Emit actual token usage from API response
                if hasattr(response, 'usage') and response.usage:
                    tokens_used += (
                        getattr(response.usage, 'input_tokens', 0)
                        + getattr(response.usage, 'output_tokens', 0)
                    )
                    self._emit({
                        "type": "usage",
                        "input_tokens": getattr(response.usage, 'input_tokens', 0),
                        "output_tokens": getattr(response.usage, 'output_tokens', 0),
                        "run_id": self._current_run_id,
                        "ts": time.time(),
                    })
                    if tokens_used >= self.limits.max_tokens:
                        self._budget_reason = "max_tokens"

                # Check if we have native tool_use blocks
                content_types = [getattr(blk, 'type', 'unknown') for blk in response.content]
                print(f"[DEBUG] response content types: {content_types}, stop_reason={response.stop_reason}")
                native_tool_uses = [blk for blk in response.content if getattr(blk, 'type', None) == 'tool_use']

                known_names = {t['name'] for t in self.tools}

                if native_tool_uses:
                    # Native Anthropic tool use - normal handling below
                    tool_blocks = native_tool_uses
                elif round_text.strip():
                    # Fallback: parse XML-style tool calls from text (DeepSeek compatibility)
                    print(f"[DEBUG] Parsing XML tools, known_names={known_names}")
                    print(f"[DEBUG] round_text preview: {round_text[:300]}")
                    parsed = _parse_xml_tool_calls(round_text, known_names)
                    print(f"[DEBUG] parsed tools: {parsed}")
                    if parsed:
                        # Create synthetic tool_use-like blocks from parsed XML
                        tool_blocks = []
                        for p in parsed:
                            block_id = f"synth_{uuid.uuid4().hex[:12]}"
                            tool_blocks.append(_SyntheticToolUse(block_id, p['name'], p['input']))
                        # Replace the assistant message content with the synthetic blocks
                        # so tool results reference correctly
                        synth_content = []
                        # Include text before first tool call
                        first_tool_match = re.search(
                            rf'<({ "|".join(re.escape(t["name"]) for t in parsed)})>',
                            round_text
                        )
                        if first_tool_match:
                            prefix = round_text[:first_tool_match.start()].strip()
                            if prefix:
                                synth_content.append(TextBlock(text=prefix, type='text'))
                        synth_content.extend(tool_blocks)
                        messages[-1] = {"role": "assistant", "content": synth_content}
                    else:
                        tool_blocks = []
                else:
                    tool_blocks = []

                verification_round = False
                verification_result = None
                if (not tool_blocks and self.verification_command and not plan_mode
                        and response.stop_reason == "end_turn" and not self._budget_reason):
                    verification_round = True
                    block = ToolUseBlock(
                        type="tool_use", id=f"verify_{uuid.uuid4().hex[:12]}",
                        name="bash", input={"command": self.verification_command},
                    )
                    messages[-1]["content"] = [*messages[-1]["content"], block]
                    tool_blocks = [block]

                if not tool_blocks:
                    self._check_interrupt()
                    if response.stop_reason not in {"end_turn", "tool_use"}:
                        outcome = "incomplete"
                        stop_reason = "model_output_limit" if response.stop_reason == "max_tokens" else "model_stopped"
                    if (self.action_continuation and not plan_mode and api_tools and response.stop_reason == "end_turn"
                            and needs_action_continuation(round_text)):
                        # For ambiguous user prose, preserve the language the
                        # assistant already chose for this round.
                        language = language or request_language([{"role": "user", "content": round_text}])
                        if continuation_attempts < 1:
                            continuation_attempts += 1
                            pending_continuation = continuation_prompt(language)
                            self._check_interrupt()
                            continue
                        notice = unfinished_notice(language)
                        outcome, stop_reason = "incomplete", "action_not_executed"
                        messages[-1]["content"] = [*response.content, TextBlock(type="text", text=notice)]
                        final_text = round_text + notice
                        self._emit({
                            "type": "chunk", "text": notice,
                            "run_id": self._current_run_id, "ts": time.time(),
                        })
                    # No tool calls - end of turn
                    if not round_text.strip():
                        outcome, stop_reason = "incomplete", "empty_response"
                        # 模型返回了空内容：给前端一个明确提示，避免"无反应"
                        fallback = "（模型未返回内容，请重试或换一种问法）"
                        self._emit({
                            "type": "chunk",
                            "text": fallback,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
                        final_text = fallback
                    if plan_mode and plan_text.strip() and plan_todo_used:
                        self._emit({
                            "type": "plan_proposed",
                            "run_id": self._current_run_id,
                            "content": _strip_xml_tools(plan_text, known_names),
                            "ts": time.time(),
                        })
                    break

                results = []
                used_todo = False
                choices_used = False
                all_choices_options: list = []  # Accumulate options across choices blocks
                manual_compress = False
                plan_only = plan_mode  # True if we're in plan mode and this round only uses planning tools

                for block_index, block in enumerate(tool_blocks):
                    if getattr(block, 'type', None) != "tool_use":
                        continue
                    if tool_calls >= self.limits.max_tool_calls:
                        self._budget_reason = "max_tool_calls"
                    if self._interrupt_flag.is_set() or self._budget_exhausted():
                        for pending in tool_blocks[block_index:]:
                            if getattr(pending, 'type', None) == "tool_use":
                                self._record_tool_result(
                                    results, pending,
                                    ToolResult(
                                        f"Error: {self._budget_reason or 'Tool cancelled'}",
                                        is_error=True,
                                        cancelled=self._interrupt_flag.is_set(),
                                    ),
                                )
                        break
                    tool_calls += 1
                    if block.name == "compress":
                        manual_compress = True
                    if plan_mode and block.name not in self.planning_tools:
                        plan_only = False  # Non-planning tool detected

                    self._state = "executing_tool"

                    # 如果该工具需要用户审批，先请求审批（callback 内部决定是否推送事件）
                    if self.approval_callback and block.name in APPROVAL_TOOLS:
                        approved = self.approval_callback(block.id, block.name, block.input)
                        if not approved:
                            self._record_tool_result(
                                results, block,
                                ToolResult("[用户已跳过此操作]", is_error=True),
                            )
                            continue

                    self._emit({
                        "type": "tool_start",
                        "name": block.name,
                        "input": block.input,
                        "purpose": "verification" if verification_round else "agent",
                        "tool_use_id": block.id,
                        "run_id": self._current_run_id,
                        "ts": time.time(),
                    })
                    if self._interrupt_flag.is_set() or self._budget_exhausted() or self._cancel_token.is_cancelled:
                        self._record_tool_result(
                            results, block,
                            ToolResult(
                                f"Error: {self._budget_reason or 'Tool cancelled'}",
                                is_error=True,
                                cancelled=True,
                            ),
                        )
                        continue

                    # Special: choices — parse options and emit options_presented event
                    if block.name == "choices":
                        options_raw = block.input.get("options", "")
                        if not options_raw:
                            # Model used key:value format — reconstruct options string
                            options_raw = "\n".join(
                                f"{k}: {v}" for k, v in block.input.items()
                                if k not in ("options",)
                            )
                        options = _parse_choices_options(options_raw)
                        all_choices_options.extend(options)
                        choices_used = True
                        output = "Options presented to user"
                        self._record_tool_result(results, block, output)
                        continue

                    # Special: load_tools — dynamically load tool categories
                    if block.name == "load_tools" and self.tool_loader:
                        cats = [c.strip() for c in block.input.get("categories", "").split(",") if c.strip()]
                        try:
                            new_tools, new_handlers = self.tool_loader(cats)
                            existing = {t["name"] for t in self.tools}
                            added_names = []
                            for t in new_tools:
                                if t["name"] not in existing:
                                    self.tools.append(t)
                                    added_names.append(t["name"])
                            self.tool_handlers.update(new_handlers)
                            output = f"Loaded {len(added_names)} tools: {', '.join(added_names)}" if added_names else "All requested tools already loaded."
                        except Exception as e:
                            output = f"Failed to load tools: {e}"
                        self._record_tool_result(results, block, output)
                        continue

                    handler = self.tool_handlers.get(block.name)
                    try:
                        tool_input = dict(block.input)
                        if block.name == "bash":
                            tool_input["timeout"] = max(0.01, min(
                                float(tool_input.get("timeout", 120)),
                                deadline - time.monotonic(),
                            ))
                            # Custom handlers need not accept the runner's timeout option.
                            parameters = inspect.signature(handler).parameters if handler else {}
                            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()) and "timeout" not in parameters:
                                tool_input.pop("timeout")
                        output = (
                            handler(**self._handler_kwargs(handler, tool_input))
                            if handler else ToolResult(
                                f"Unknown tool: {block.name}", is_error=True
                            )
                        )
                    except AgentInterrupted:
                        self._interrupt_flag.set()
                        output = ToolResult(
                            "Error: Tool cancelled",
                            is_error=True,
                            cancelled=True,
                        )
                    except Exception as e:
                        output = ToolResult(f"Error: {e}", is_error=True)

                    self._record_tool_result(results, block, output)
                    if verification_round:
                        verification_result = self._tool_output(output)[2]
                    if block.name == "TodoWrite":
                        used_todo = True
                        plan_todo_used = True

                if self.todo and hasattr(self.todo, 'has_open_items'):
                    self._rounds_without_todo = 0 if used_todo else self._rounds_without_todo + 1
                    if self.todo.has_open_items() and self._rounds_without_todo >= 3:
                        results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})

                messages.append({"role": "user", "content": results})
                self._check_interrupt()

                if verification_round:
                    if verification_result is None:
                        outcome, stop_reason = "incomplete", "verification_not_executed"
                        break
                    verified = bool(verification_result) and (
                        verification_result.get("exit_code") == 0
                        and not verification_result.get("is_error")
                    )
                    self._emit({
                        "type": "verification", "state": "passed" if verified else "failed",
                        "command": self.verification_command, "attempt": repair_attempts,
                        "run_id": run_id, "ts": time.time(),
                    })
                    if verified:
                        stop_reason = "verification_passed"
                        break
                    if repair_attempts >= self.limits.max_repair_attempts:
                        outcome, stop_reason = "failed", "verification_failed"
                        self._emit({
                            "type": "system", "message": "验证未通过，已达到修复次数上限。",
                            "run_id": run_id, "ts": time.time(),
                        })
                        break
                    repair_attempts += 1
                    pending_continuation = (
                        f"Verification failed. Repair attempt {repair_attempts}/"
                        f"{self.limits.max_repair_attempts}. Diagnose the command result above, "
                        "make a minimal source fix, and preserve the tests. "
                        "Do not claim success; the controller will rerun verification."
                    )
                    continue

                # In plan mode: if TodoWrite was used and this round was planning-only
                if plan_only and plan_text.strip() and plan_todo_used:
                    self._emit({
                        "type": "plan_proposed",
                        "run_id": self._current_run_id,
                        "content": _strip_xml_tools(plan_text, known_names),
                        "ts": time.time(),
                    })
                    break

                # In plan mode: if choices were used, emit all options and break to wait for user input
                if choices_used:
                    if all_choices_options:
                        self._emit({
                            "type": "options_presented",
                            "options": all_choices_options,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
                    break

                if manual_compress:
                    self._emit({"type": "system", "message": "manual-compact", "ts": time.time()})
                    messages[:] = auto_compact(self.client, self.model, messages)
                    break

        except AgentInterrupted:
            outcome, stop_reason = "interrupted", "user_interrupt"
            self._cancel_token.cancel()
            self._emit({
                "type": "interrupted",
                "run_id": self._current_run_id,
                "ts": time.time(),
            })
        except RunBudgetExceeded as exc:
            outcome, stop_reason = "incomplete", str(exc)
            self._cancel_token.cancel()
            self._emit({
                "type": "system", "message": f"运行达到预算上限：{exc}，任务尚未完成。",
                "run_id": run_id, "ts": time.time(),
            })
        except Exception as e:
            outcome, stop_reason = "failed", "error"
            # 非中断异常：先发 error 事件，再由 finally 发 run_end
            self._cancel_token.cancel()
            import traceback
            traceback.print_exc()  # 完整堆栈进服务日志，便于排查
            self._emit({
                "type": "error",
                "message": str(e),
                "run_id": self._current_run_id,
                "ts": time.time(),
            })
        finally:
            timer.cancel()
            timer.join()
            # A deadline/provider error can arrive after publishing tool_use but
            # before its handlers run. Keep the next request's history valid.
            if messages and messages[-1].get("role") == "assistant":
                dangling = [block for block in messages[-1].get("content", [])
                            if getattr(block, "type", None) == "tool_use"]
                if dangling:
                    messages.append({"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": block.id,
                         "content": f"Tool not completed: {stop_reason}", "is_error": True}
                        for block in dangling
                    ]})
            _final_run_id = run_id
            self.release_run(run_id)
            self._emit({
                "type": "run_end",
                "run_id": _final_run_id,
                "outcome": outcome, "stop_reason": stop_reason,
                "rounds": rounds, "tool_calls": tool_calls,
                "repair_attempts": repair_attempts,
                "ts": time.time(),
            })

        return final_text
