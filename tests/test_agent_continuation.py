from copy import deepcopy
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

import api_server
from core.loop import AgentLoop


_persist_unfinished_stream = api_server._persist_unfinished_stream


def _text_block(text):
    return TextBlock(type="text", text=text)


def _text_event(text):
    return SimpleNamespace(type="text", text=text)


def _thinking_event(text):
    return SimpleNamespace(type="thinking", thinking=text)


def _response(content, stop_reason):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=None)


def _text_script(text, *, thinking=None, trailing_events=()):
    events = []
    if thinking:
        events.append(_thinking_event(thinking))
    events.append(_text_event(text))
    events.extend(trailing_events)
    return SimpleNamespace(
        response=_response([_text_block(text)], "end_turn"),
        events=events,
    )


def _tool_script(name, tool_input, *, call_id="call-1", thinking=None):
    events = [_thinking_event(thinking)] if thinking else []
    return SimpleNamespace(
        response=_response([
            ToolUseBlock(
                type="tool_use",
                id=call_id,
                name=name,
                input=tool_input,
            )
        ], "tool_use"),
        events=events,
    )


class FakeStream:
    def __init__(self, script):
        self.script = script

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        yield from self.script.events

    def get_final_message(self):
        return self.script.response


class RecordingMessages:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.requests = []

    def stream(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        index = len(self.requests) - 1
        if index >= len(self.scripts):
            raise AssertionError(f"unexpected model request #{index + 1}")
        return FakeStream(self.scripts[index])


def _tool(name):
    return {
        "name": name,
        "description": f"Test {name}",
        "input_schema": {"type": "object"},
    }


def _agent(scripts, *, handlers=None, tools=None, events=None, approval_callback=None):
    recording = RecordingMessages(scripts)
    event_log = events if events is not None else []
    agent = AgentLoop(
        client=SimpleNamespace(messages=recording),
        model="test-model",
        system="Test system",
        tools=tools if tools is not None else [_tool("read_file")],
        tool_handlers=handlers or {},
        bg=SimpleNamespace(drain=lambda: []),
        bus=SimpleNamespace(read_inbox=lambda _: []),
        event_callback=event_log.append,
        approval_callback=approval_callback,
        token_threshold=100000,
    )
    return agent, recording, event_log


def _block_text(block):
    return block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")


def test_chinese_action_announcement_continues_to_real_read_and_final_answer():
    announcement = "接下来我将读取 README.md，请稍等。"
    final_answer = "README.md 显示项目名称是 code-agent。"
    calls = []
    scripts = [
        _text_script(announcement, thinking="先判断需要读取哪个文件。"),
        _tool_script(
            "read_file", {"path": "README.md"}, call_id="read-1",
            thinking="现在实际读取文件。",
        ),
        _text_script(final_answer, thinking="根据读取结果组织答案。"),
    ]
    agent, recording, events = _agent(
        scripts,
        handlers={"read_file": lambda path: calls.append(path) or "# code-agent"},
    )
    history = [{"role": "user", "content": "请读取 README.md 并告诉我项目名称。"}]

    result = agent.run(history, run_id="run-zh")

    assert result == final_answer
    assert calls == ["README.md"]
    assert len(recording.requests) == 3
    assert recording.requests[1]["messages"][-1]["role"] == "user"
    request_local_turn = recording.requests[1]["messages"][-1]
    assert request_local_turn not in history
    assert [message["role"] for message in history] == [
        "user", "assistant", "assistant", "user", "assistant",
    ]
    assert history[1]["role"] == "assistant"
    assert _block_text(history[1]["content"][0]) == announcement
    assert sum(event.get("type") == "agent_state" for event in events) == 3
    assert [event["text"] for event in events if event.get("type") == "thinking"] == [
        "先判断需要读取哪个文件。",
        "现在实际读取文件。",
        "根据读取结果组织答案。",
    ]
    assert len([event for event in events if event.get("type") == "run_end"]) == 1


def test_english_action_announcement_continues_to_completed_answer():
    announcement = "Let me inspect the project files before I answer."
    final_answer = "The project entry point is app.py."
    agent, recording, _ = _agent([
        _text_script(announcement),
        _text_script(final_answer),
    ])
    history = [{"role": "user", "content": "Where is the project entry point?"}]

    result = agent.run(history)

    assert result == final_answer
    assert len(recording.requests) == 2
    assert recording.requests[1]["messages"][-1]["role"] == "user"
    assert _block_text(history[1]["content"][0]) == announcement
    assert recording.requests[1]["messages"][-1] not in history
    assert [message["role"] for message in history] == ["user", "assistant", "assistant"]


def test_repeated_announcement_has_one_retry_and_explicit_unfinished_notice():
    first = "I'll inspect the files now."
    repeated = "Let me read the files before I answer."
    agent, recording, events = _agent([
        _text_script(first),
        _text_script(repeated),
    ])
    history = [{"role": "user", "content": "Inspect the files and report the result."}]

    result = agent.run(history, run_id="run-repeated")

    assert len(recording.requests) == 2
    assert "has not been executed" in result
    assert "remains incomplete" in result
    assert _block_text(history[-1]["content"][-1]).strip().endswith(
        "this task remains incomplete."
    )
    assert recording.requests[1]["messages"][-1] not in history
    assert [message["role"] for message in history] == ["user", "assistant", "assistant"]
    assert len([event for event in events if event.get("type") == "run_end"]) == 1
    assert events[-1]["type"] == "run_end"


def test_completed_answer_with_next_step_recommendation_does_not_retry():
    answer = "实现已经完成。下一步建议你运行 pytest 验证。"
    agent, recording, _ = _agent([_text_script(answer)])
    history = [{"role": "user", "content": "请完成修改。"}]

    assert agent.run(history) == answer
    assert len(recording.requests) == 1


@pytest.mark.parametrize("answer", [
    "I will not run this command.",
    "我将不会运行这个危险命令。",
    "如果你同意，我将读取这些文件。",
    "I will inspect the logs once you approve.",
    "请确认。\n\n我将修改配置文件。",
    "For this small local app, I will use SQLite because it keeps deployment simple.",
    "我们将使用 SQLite 作为数据库。",
    "I will call validate() from submit(), which keeps validation centralized.",
    "To pass control, I will call `next()` after authentication succeeds.",
    "I will check authorization in Next.js middleware before serving protected pages.",
    "I will check for null before parsing and return 400 for malformed JSON.",
    "我会调用 validate 集中校验，避免两处分叉。",
    "I will not run this command now.",
    "接下来我将不会运行这个危险命令。",
    "如果你同意，接下来我将读取这些文件。",
    "请确认。\n\n接下来我将修改配置文件。",
])
def test_negated_conditional_or_descriptive_promise_does_not_retry(answer):
    agent, recording, _ = _agent([_text_script(answer)])
    history = [{"role": "user", "content": "Explain what you will do."}]

    assert agent.run(history) == answer
    assert len(recording.requests) == 1


def test_exact_wait_for_searches_ending_triggers_one_continuation():
    announcement = "请等待我执行这些搜索。"
    final_answer = "搜索已完成，结果中没有匹配项。"
    agent, recording, _ = _agent([
        _text_script(announcement),
        _text_script(final_answer),
    ])
    history = [{"role": "user", "content": "请搜索所有相关文件。"}]

    assert agent.run(history) == final_answer
    assert len(recording.requests) == 2
    assert recording.requests[1]["messages"][-1]["role"] == "user"
    assert [message["role"] for message in history] == ["user", "assistant", "assistant"]


def test_ambiguous_user_language_uses_english_announcement_for_retry_and_notice():
    first = "Now I'll inspect the login flow."
    repeated = "Next, let me check the relevant files."
    agent, recording, _ = _agent([
        _text_script(first),
        _text_script(repeated),
    ])
    history = [{"role": "user", "content": "Review 登录 flow"}]

    result = agent.run(history)

    assert len(recording.requests) == 2
    continuation_turn = recording.requests[1]["messages"][-1]
    assert continuation_turn["role"] == "user"
    controller_reminder = continuation_turn["content"].split(
        "\n\n<response-language>", 1
    )[0]
    assert controller_reminder.startswith("Your last response ended")
    assert "上一条回复" not in controller_reminder
    assert "has not been executed" in result
    assert "remains incomplete" in result
    assert "本次任务未完成" not in result
    assert continuation_turn not in history


def test_plan_mode_does_not_retry_action_announcement():
    announcement = "我将读取相关文件，然后制定实施计划。"
    agent, recording, _ = _agent([_text_script(announcement)])
    history = [{"role": "user", "content": "请先规划如何修改。"}]

    assert agent.run(history, plan_mode=True) == announcement
    assert len(recording.requests) == 1


def test_choices_tool_keeps_waiting_for_user_without_continuation():
    agent, recording, events = _agent(
        [_tool_script("choices", {"options": "safe: Safe option\nfast: Fast option"})],
        tools=[_tool("choices"), _tool("read_file")],
    )
    history = [{"role": "user", "content": "Help me choose an approach."}]

    agent.run(history, plan_mode=True)

    assert len(recording.requests) == 1
    presented = [event for event in events if event.get("type") == "options_presented"]
    assert len(presented) == 1
    assert [option["id"] for option in presented[0]["options"]] == ["safe", "fast"]


def test_recovered_write_call_still_requires_approval_and_rejection_skips_handler():
    writes = []
    approvals = []

    def approve(call_id, name, tool_input):
        approvals.append((call_id, name, tool_input))
        return False

    scripts = [
        _text_script("我将写入 result.txt，请稍等。"),
        _tool_script(
            "write_file", {"path": "result.txt", "content": "done"},
            call_id="write-1",
        ),
        _text_script("写入审批被拒绝，因此文件没有修改。"),
    ]
    agent, recording, events = _agent(
        scripts,
        tools=[_tool("write_file")],
        handlers={"write_file": lambda **kwargs: writes.append(kwargs) or "written"},
        approval_callback=approve,
    )
    history = [{"role": "user", "content": "请把结果写入 result.txt。"}]

    result = agent.run(history)

    assert result == "写入审批被拒绝，因此文件没有修改。"
    assert len(recording.requests) == 3
    assert approvals == [("write-1", "write_file", {"path": "result.txt", "content": "done"})]
    assert writes == []
    assert not [event for event in events if event.get("type") == "tool_start"]
    rejected = [event for event in events if event.get("type") == "tool_result"]
    assert len(rejected) == 1
    assert "跳过" in rejected[0]["output"]


def test_interrupt_before_continuation_prevents_second_request_and_does_not_duplicate_round():
    announcement = "接下来我将读取 README.md 并检查项目结构，请稍等。"
    events = []
    agent, recording, _ = _agent([_text_script(announcement)], events=events)

    def capture(event):
        events.append(event)
        if event.get("type") == "chunk" and event.get("text") == announcement:
            agent.interrupt()

    agent.event_callback = capture
    history = [{"role": "user", "content": "请检查项目结构。"}]

    result = agent.run(history, run_id="run-interrupt-before")

    assert result == announcement
    assert len(recording.requests) == 1
    assert _block_text(history[-1]["content"][0]) == announcement
    assert [event["type"] for event in events if event.get("type") in {"interrupted", "run_end"}] == [
        "interrupted", "run_end",
    ]

    context = SimpleNamespace(history=history, events=events, baseline_length=1)
    before = deepcopy(history)
    _persist_unfinished_stream(context)
    assert history == before


def test_interrupt_during_continuation_preserves_partial_once_without_third_request():
    announcement = "I'll inspect the repository files now."
    partial = "Partial continuation output before interruption."
    scripts = [
        _text_script(announcement, thinking="Preparing the inspection."),
        _text_script(
            partial,
            thinking="Reading the available context.",
            trailing_events=(_thinking_event("This event must not be processed."),),
        ),
    ]
    events = []
    agent, recording, _ = _agent(scripts, events=events)

    def capture(event):
        events.append(event)
        if event.get("type") == "chunk" and event.get("text") == partial:
            agent.interrupt()

    agent.event_callback = capture
    history = [{"role": "user", "content": "Inspect the repository."}]

    result = agent.run(history, run_id="run-interrupt-during")

    assert result == announcement
    assert len(recording.requests) == 2
    assert sum(message["role"] == "assistant" for message in history) == 1
    assert "".join(event.get("text", "") for event in events if event.get("type") == "chunk").endswith(partial)
    assert len([event for event in events if event.get("type") == "interrupted"]) == 1
    assert len([event for event in events if event.get("type") == "run_end"]) == 1

    context = SimpleNamespace(history=history, events=events, baseline_length=1)
    _persist_unfinished_stream(context)
    assert sum(message["role"] == "assistant" for message in history) == 2
    persisted = history[-1]["content"]
    assert persisted == [
        {
            "type": "text",
            "text": "[Partial thinking before interruption]\nReading the available context.",
        },
        {"type": "text", "text": partial},
    ]

    snapshot = deepcopy(history)
    _persist_unfinished_stream(context)
    assert history == snapshot
