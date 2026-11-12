"""Small Anthropic-shaped client for deterministic Ollama evaluations."""
import copy
import json
import time
import uuid
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from anthropic.types import TextBlock, ToolUseBlock
def post_json(base_url: str, route: str, payload: dict, timeout: float = 90) -> dict:
    """POST JSON once and return a decoded JSON object."""
    url = f"{base_url.rstrip('/')}/{route.lstrip('/')}"; parts = urlsplit(url)
    safe_url = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        exc.close(); raise RuntimeError(f"Ollama HTTP {exc.code} from {safe_url}") from None
    except URLError as exc:
        raise RuntimeError(f"Ollama request to {safe_url} failed ({type(exc.reason).__name__})") from None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama returned invalid JSON from {safe_url}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Ollama returned non-object JSON from {safe_url}")
    return value

def _field(block, name: str, default=None):
    return block.get(name, default) if isinstance(block, dict) else getattr(block, name, default)

def _text(content) -> str:
    if isinstance(content, str): return content
    if content is None: return ""
    if isinstance(content, list):
        return "".join(_text(_field(part, "text", _field(part, "content", part))) for part in content)
    if isinstance(content, (dict, int, float, bool)): return json.dumps(content, ensure_ascii=False)
    return str(content)

class _Stream:
    def __init__(self, client, arguments):
        self.client, self.arguments, self.final = client, arguments, None
    def __enter__(self):
        self.final = self.client._complete(self.arguments)
        return self
    def __exit__(self, *_):
        self.close(); return False
    def __iter__(self):
        if self.final is None: raise RuntimeError("stream must be entered before iteration")
        text = "".join(block.text for block in self.final.content if block.type == "text")
        if text:
            yield SimpleNamespace(type="text", text=text)
    def get_final_message(self):
        if self.final is None: raise RuntimeError("stream must be entered before reading the final message")
        return self.final
    def close(self): pass

class OllamaEvalClient:
    def __init__(self, base_url, model, *, timeout=90, seed=42, records=None):
        self.base_url, self.model = base_url, model
        self.timeout, self.seed = timeout, seed
        self.records = records if records is not None else []
        self._state, self._closed, self.messages = {"tool_names": {}}, False, self
    def with_options(self, *, timeout=None, max_retries=0, **_):
        if max_retries != 0:
            raise ValueError("Ollama evaluation requests do not retry")
        clone = copy.copy(self)
        clone.timeout = self.timeout if timeout is None else timeout
        clone.messages = clone
        return clone
    def stream(self, **arguments): return _Stream(self, arguments)
    def close(self): self._closed = True
    def _history(self, system, messages):
        result = []
        system = _text(system)
        if system: result.append({"role": "system", "content": system})
        for message in messages:
            role, content = message["role"], message.get("content", "")
            blocks = content if isinstance(content, list) else None
            if role == "assistant" and blocks is not None:
                texts, calls = [], []
                for block in blocks:
                    kind = _field(block, "type")
                    if kind == "text":
                        texts.append(_field(block, "text", ""))
                    elif kind == "tool_use":
                        call_id, name = str(_field(block, "id")), _field(block, "name")
                        self._state["tool_names"][call_id] = name
                        calls.append({"function": {"name": name, "arguments": _field(block, "input", {})}})
                    else:
                        raise ValueError(f"unsupported assistant content block: {kind}")
                item = {"role": "assistant", "content": "".join(texts)}
                if calls: item["tool_calls"] = calls
                result.append(item)
            elif role == "user" and blocks is not None:
                pending = []
                for block in blocks:
                    kind = _field(block, "type")
                    if kind == "text":
                        pending.append(_field(block, "text", ""))
                        continue
                    if pending:
                        result.append({"role": "user", "content": "".join(pending)}); pending = []
                    if kind != "tool_result": raise ValueError(f"unsupported user content block: {kind}")
                    call_id = str(_field(block, "tool_use_id"))
                    name = self._state["tool_names"].get(call_id)
                    if not name: raise ValueError(f"tool_result references unknown tool call {call_id}")
                    output = _text(_field(block, "content", ""))
                    if _field(block, "is_error", False): output = f"[tool_error]\n{output}"
                    result.append({"role": "tool", "tool_name": name, "content": output})
                if pending: result.append({"role": "user", "content": "".join(pending)})
            else:
                result.append({"role": role, "content": _text(content)})
        return result
    def _complete(self, arguments):
        if self._closed: raise RuntimeError("Ollama evaluation client is closed")
        requested_model = arguments.get("model", self.model)
        if requested_model != self.model: raise ValueError(f"evaluation model is fixed to {self.model}")
        num_predict = min(700, int(arguments.get("max_tokens", 700)))
        if num_predict <= 0: raise ValueError("max_tokens must be positive")
        payload = {
            "model": self.model, "stream": False, "keep_alive": "5m",
            "messages": self._history(arguments.get("system", ""), arguments.get("messages", [])),
            "options": {"temperature": 0, "seed": self.seed, "num_ctx": 8192, "num_predict": num_predict},
        }
        tools = arguments.get("tools") or []
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": tool["name"], "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object"}),
            }} for tool in tools]
        started = time.monotonic()
        try:
            response = post_json(self.base_url, "/api/chat", payload, timeout=self.timeout)
        except Exception as exc:
            self.records.append({
                "error": type(exc).__name__, "prompt_eval_count": None, "eval_count": None,
                "wall_time_seconds": time.monotonic() - started,
            })
            raise
        metadata = {key: response.get(key) for key in (
            "model", "prompt_eval_count", "eval_count", "total_duration", "load_duration",
            "prompt_eval_duration", "eval_duration", "done_reason",
        )}
        metadata["wall_time_seconds"] = time.monotonic() - started
        usage = (response.get("prompt_eval_count"), response.get("eval_count"))
        missing_usage = any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in usage)
        if missing_usage: metadata["error"] = "missing_token_usage"
        self.records.append(metadata)
        if missing_usage: raise ValueError("Ollama response is missing token usage counts")
        message = response.get("message")
        if not isinstance(message, dict): raise ValueError("Ollama response is missing message")
        content = []
        if message.get("content"): content.append(TextBlock(type="text", text=message["content"]))
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            tool_input = function.get("arguments", {})
            if isinstance(tool_input, str): tool_input = json.loads(tool_input)
            if not isinstance(tool_input, dict) or not function.get("name"): raise ValueError(
                "Ollama returned an invalid tool call")
            call_id = str(call.get("id") or f"ollama_{uuid.uuid4().hex[:12]}")
            self._state["tool_names"][call_id] = function["name"]
            content.append(ToolUseBlock(type="tool_use", id=call_id,
                                        name=function["name"], input=tool_input))
        done_reason = response.get("done_reason")
        if any(block.type == "tool_use" for block in content): stop_reason = "tool_use"
        elif done_reason in {"length", "max_tokens"} or (
                not done_reason and response["eval_count"] >= num_predict): stop_reason = "max_tokens"
        else: stop_reason = "end_turn"
        return SimpleNamespace(
            content=content,
            usage=SimpleNamespace(input_tokens=response["prompt_eval_count"],
                                  output_tokens=response["eval_count"]), stop_reason=stop_reason,
        )
