from anthropic import Anthropic
from components.command_runner import CancellationToken, ToolResult
from tools.base import BASE_HANDLERS


def _cancelled_result():
    return ToolResult("Error: Subagent cancelled", is_error=True, cancelled=True)


def _request(client, token, **kwargs):
    try:
        with client.messages.stream(**kwargs) as stream:
            closer = token.register_closer(getattr(stream, "close", lambda: None))
            try:
                if token.is_cancelled:
                    return None
                for _ in stream:
                    if token.is_cancelled:
                        return None
                return stream.get_final_message()
            finally:
                token.unregister_closer(closer)
    except Exception:
        if token.is_cancelled:
            return None
        raise


def run_subagent(
    client: Anthropic,
    model: str,
    prompt: str,
    agent_type: str = "Explore",
    cancellation_token: CancellationToken = None,
) -> str:
    token = cancellation_token or CancellationToken()
    sub_tools = [
        {"name": "bash", "description": "Run command.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "read_file", "description": "Read file.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    ]
    if agent_type != "Explore":
        sub_tools += [
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        ]
    sub_handlers = {
        "bash": BASE_HANDLERS["bash"],
        "read_file": lambda **kw: BASE_HANDLERS["read_file"](**kw),
        "write_file": lambda **kw: BASE_HANDLERS["write_file"](**kw),
        "edit_file": lambda **kw: BASE_HANDLERS["edit_file"](**kw),
    }
    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    for _ in range(30):
        if token.is_cancelled:
            return _cancelled_result()
        resp = _request(
            client, token, model=model, messages=sub_msgs,
            tools=sub_tools, max_tokens=8000,
        )
        if resp is None or token.is_cancelled:
            return _cancelled_result()
        sub_msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                if token.is_cancelled:
                    return _cancelled_result()
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                out = h(**b.input, _cancel_token=token)
                content = out.model_content() if isinstance(out, ToolResult) else str(out)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": content[:50000],
                    "is_error": getattr(out, "is_error", False),
                })
        sub_msgs.append({"role": "user", "content": results})
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"
