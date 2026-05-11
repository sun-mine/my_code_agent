import json
import re
import threading
import time
import uuid
from typing import List, Dict, Any, Callable, Optional
from anthropic import Anthropic
from anthropic.types import TextBlock, ToolUseBlock
from components.compactor import microcompact, auto_compact, estimate_tokens
from components.background_manager import BackgroundManager
from components.message_bus import MessageBus

# 需要用户审批才能执行的工具（在 manual 模式下会触发审批回调）
APPROVAL_TOOLS = {"bash", "write_file", "edit_file", "task", "background_run"}

# Tools that are safe/expected during plan mode (don't break the planning flow)
_PLANNING_TOOLS = {"TodoWrite", "load_tools", "read_file", "list_files", "glob", "grep", "memory_recall", "memory_store", "choices"}


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
        self._rounds_without_todo = 0
        self._interrupt_flag = threading.Event()  # 中断信号
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

    def interrupt(self):
        """外部调用：发起中断请求"""
        self._interrupt_flag.set()

    def reset_interrupt(self):
        """重置中断信号"""
        self._interrupt_flag.clear()

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_run_id(self) -> Optional[str]:
        return self._current_run_id

    def run(self, messages: list, run_id: Optional[str] = None, plan_mode: bool = False) -> str:
        """
        运行主循环。
        run_id: 本次运行的唯一标识，用于前端跟踪与中断控制；若不传则自动生成。
        plan_mode: 是否为 plan 模式（先生成计划，暂停等待用户确认）
        返回最终文本响应（供同步场景使用）。
        """
        self._current_run_id = run_id or str(uuid.uuid4())
        self.reset_interrupt()
        self._state = "thinking"
        final_text = ""
        plan_text = ""        # Accumulate all assistant text in plan mode
        plan_todo_used = False  # Only fire plan_proposed after TodoWrite was used

        self._emit({
            "type": "run_start",
            "run_id": self._current_run_id,
            "ts": time.time(),
        })

        try:
            while True:
                self._check_interrupt()
                microcompact(messages)
                if estimate_tokens(messages) > self.token_threshold:
                    self._emit({"type": "system", "message": "auto-compact", "ts": time.time()})
                    messages[:] = auto_compact(self.client, self.model, messages)

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
                api_tools: List[Dict] = [t for t in self.tools if t['name'] in _PLANNING_TOOLS] if plan_mode else self.tools

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

                with self.client.messages.stream(
                    model=self.model,
                    system=self.system,
                    messages=messages,
                    tools=api_tools,
                    max_tokens=8000,
                ) as stream:
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
                        # Flush batches every 80ms or when buffer is 20+ chars
                        if _batch_thinking or _batch_text:
                            total = len(_batch_thinking) + len(_batch_text)
                            if total >= 20 or (time.time() - _last_flush) >= 0.08:
                                _flush()
                    response = stream.get_final_message()
                _flush()  # flush remaining

                # Emit actual token usage from API response
                if hasattr(response, 'usage') and response.usage:
                    self._emit({
                        "type": "usage",
                        "input_tokens": getattr(response.usage, 'input_tokens', 0),
                        "output_tokens": getattr(response.usage, 'output_tokens', 0),
                        "run_id": self._current_run_id,
                        "ts": time.time(),
                    })

                if round_text:
                    final_text = round_text
                    if plan_mode:
                        plan_text = (plan_text + "\n\n" + round_text).strip() if plan_text else round_text

                messages.append({"role": "assistant", "content": response.content})

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

                if not tool_blocks:
                    # No tool calls - end of turn
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

                for block in tool_blocks:
                    if getattr(block, 'type', None) != "tool_use":
                        continue
                    self._check_interrupt()
                    if block.name == "compress":
                        manual_compress = True
                    if plan_mode and block.name not in _PLANNING_TOOLS:
                        plan_only = False  # Non-planning tool detected

                    self._state = "executing_tool"

                    # 如果该工具需要用户审批，先请求审批（callback 内部决定是否推送事件）
                    if self.approval_callback and block.name in APPROVAL_TOOLS:
                        approved = self.approval_callback(block.id, block.name, block.input)
                        if not approved:
                            skipped = "[用户已跳过此操作]"
                            results.append({"type": "tool_result", "tool_use_id": block.id, "content": skipped})
                            self._emit({
                                "type": "tool_result",
                                "name": block.name,
                                "output": skipped,
                                "tool_use_id": block.id,
                                "run_id": self._current_run_id,
                                "ts": time.time(),
                            })
                            continue

                    self._emit({
                        "type": "tool_start",
                        "name": block.name,
                        "input": block.input,
                        "tool_use_id": block.id,
                        "run_id": self._current_run_id,
                        "ts": time.time(),
                    })

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
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                        self._emit({
                            "type": "tool_result",
                            "name": block.name,
                            "output": output,
                            "tool_use_id": block.id,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
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
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                        self._emit({
                            "type": "tool_result",
                            "name": block.name,
                            "output": output,
                            "tool_use_id": block.id,
                            "run_id": self._current_run_id,
                            "ts": time.time(),
                        })
                        continue

                    handler = self.tool_handlers.get(block.name)
                    try:
                        output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                    except AgentInterrupted:
                        raise
                    except Exception as e:
                        output = f"Error: {e}"

                    self._emit({
                        "type": "tool_result",
                        "name": block.name,
                        "output": str(output)[:2000],
                        "tool_use_id": block.id,
                        "run_id": self._current_run_id,
                        "ts": time.time(),
                    })
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                    if block.name == "TodoWrite":
                        used_todo = True
                        plan_todo_used = True

                if self.todo and hasattr(self.todo, 'has_open_items'):
                    self._rounds_without_todo = 0 if used_todo else self._rounds_without_todo + 1
                    if self.todo.has_open_items() and self._rounds_without_todo >= 3:
                        results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})

                messages.append({"role": "user", "content": results})

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
            self._emit({
                "type": "interrupted",
                "run_id": self._current_run_id,
                "ts": time.time(),
            })
        except Exception as e:
            # 非中断异常：先发 error 事件，再由 finally 发 run_end
            self._emit({
                "type": "error",
                "message": str(e),
                "run_id": self._current_run_id,
                "ts": time.time(),
            })
        finally:
            _final_run_id = self._current_run_id  # 先捕获，再清空
            self._state = "idle"
            self._current_run_id = None
            self._emit({
                "type": "run_end",
                "run_id": _final_run_id,
                "ts": time.time(),
            })

        return final_text