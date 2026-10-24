from copy import deepcopy
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

import core.loop as loop_module
from core.loop import AgentLoop
from core.response_language import response_language_instructions


def user(content):
    return {"role": "user", "content": content}


@pytest.mark.parametrize(("content", "language"), [
    ("请分析当前项目的目录结构、技术栈和主要入口。", "中文 (Chinese)"),
    ("Analyze the project structure, stack and entry points.", "English"),
    ("请分析 React + TypeScript 项目。", "中文 (Chinese)"),
    ('Explain the label "保存".', "English"),
    ("Explain the label 保存", "English"),
    ("How should I handle 中文路径 in this parser?", "English"),
    ("React + TypeScript 项目应该如何启动？", "中文 (Chinese)"),
    ("帮我 review this PR", "中文 (Chinese)"),
    ("请用英文回答：分析当前项目", "English"),
    ("把这段翻译成英文：你好。", "English"),
    ("Translate this into Chinese: hello.", "中文 (Chinese)"),
    ("Please answer in Chinese.", "中文 (Chinese)"),
    ("不要用英文回答，请分析项目。", "中文 (Chinese)"),
    ("请使用英文变量名并解释修改。", "中文 (Chinese)"),
    ('Explain the string "reply in Chinese".', "English"),
    ("Explain the string 'reply in Chinese'.", "English"),
    ("[文件: 示例.ts]\n```\n// 中文注释\n```\n\nExplain this code.", "English"),
    ([{"type": "image", "source": {}}, {"type": "text", "text": "请分析这张图"}], "中文 (Chinese)"),
])
def test_default_uses_human_prose(content, language):
    instructions = response_language_instructions([user(content)])
    assert f"Default reply language for this turn: {language}." in instructions


def test_ambiguous_attachment_fences_do_not_force_the_attachments_language():
    instructions = response_language_instructions([
        user("之前用中文讨论过。"),
        user("[文件: 示例.md]\n```\n# 示例\n```python\nprint('你好')\n```\n说明\n```\n\nExplain the bug."),
    ])
    assert "Default reply language for this turn:" not in instructions
    assert "English questions must receive English answers" in instructions


@pytest.mark.parametrize("internal", [
    [{"type": "tool_result", "tool_use_id": "call-1", "content": "English package metadata"},
     {"type": "text", "text": "Update your todos."}],
    "<inbox>English teammate report</inbox>",
    "<background-results>English command output</background-results>",
    "[Compacted. Transcript: transcript.jsonl]\nEnglish summary",
    "__compress__",
])
def test_internal_messages_do_not_change_language(internal):
    instructions = response_language_instructions([user("请分析这个项目"), user(internal)])
    assert "Default reply language for this turn: 中文 (Chinese)." in instructions


def test_chinese_plan_execution_wrapper_does_not_override_english_request():
    messages = [
        user("Implement this plan."),
        {"role": "assistant", "content": "Plan ready."},
        user("<plan-execute>用户已批准计划，现在执行。</plan-execute>"),
    ]
    assert "Default reply language for this turn: English." in response_language_instructions(messages)


@pytest.mark.parametrize("button_message", [
    "选择: option_1",
    "请重新规划，考虑不同的方案",
    "请根据以下反馈修改计划：\nUse a transaction.",
])
def test_english_plan_buttons_do_not_switch_to_chinese(button_message):
    instructions = response_language_instructions([user("Plan the changes."), user(button_message)])
    assert "Default reply language for this turn: English." in instructions


def test_actual_chinese_plan_feedback_can_change_the_reply_language():
    instructions = response_language_instructions([
        user("Plan the changes."), user("请根据以下反馈修改计划：\n请增加回滚步骤"),
    ])
    assert "Default reply language for this turn: 中文 (Chinese)." in instructions


class RecordingMessages:
    def __init__(self):
        self.requests = []

    def stream(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        if len(self.requests) == 1:
            response = SimpleNamespace(content=[ToolUseBlock(
                type="tool_use", id="call-1", name="list_files", input={},
            )], stop_reason="tool_use")
        else:
            response = SimpleNamespace(content=[TextBlock(type="text", text="Answer")], stop_reason="end_turn")
        return FakeStream(response)


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        for block in self.response.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self):
        return self.response


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize(("user_text", "language"), [
    ("请分析当前项目", "中文 (Chinese)"),
    ("请用英文回答：分析当前项目", "English"),
    ("Translate this into Chinese: hello.", "中文 (Chinese)"),
])
def test_language_survives_tools_compaction_and_changes_on_next_user_turn(monkeypatch, compact, user_text, language):
    recording = RecordingMessages()
    agent = AgentLoop(
        client=SimpleNamespace(messages=recording), model="test", system="Base instructions",
        tools=[{"name": "list_files", "description": "List files", "input_schema": {"type": "object"}}],
        tool_handlers={"list_files": lambda: "English package metadata"},
        bg=SimpleNamespace(drain=lambda: []), bus=SimpleNamespace(read_inbox=lambda _: []),
        token_threshold=1 if compact else 100000,
    )
    # Exercise a compaction that removes the original human request entirely.
    monkeypatch.setattr(loop_module, "auto_compact", lambda *_: [user("[Compacted. Transcript: test]\nEnglish summary")])
    history = [user(user_text)]
    agent.run(history)

    assert len(recording.requests) == 2
    for request in recording.requests:
        assert f"Default reply language for this turn: {language}." in request["system"]
    assert recording.requests[0]["system"] == recording.requests[1]["system"]
    assert agent.system == "Base instructions"
    if not compact:
        assert recording.requests[1]["messages"][-1]["content"][0]["content"] == "English package metadata"

    history.append(user("Now explain the entry points in English."))
    agent.run(history)
    assert "Default reply language for this turn: English." in recording.requests[-1]["system"]
