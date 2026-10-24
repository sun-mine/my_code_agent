"""Recover a text-only action announcement without inventing a tool call."""

import re


_AWAITING_USER = re.compile(
    r"请(?:你|您)?(?:确认|批准|选择|提供|告诉)|是否(?:同意|允许|需要)"
    r"|(?:如果|若|待|等(?:到)?|在)[^。！？\n]{0,24}(?:你|您|用户)[^。！？\n]{0,16}(?:确认|批准|同意|授权)"
    r"|\b(?:please (?:confirm|approve|provide|choose)|(?:would|do) you (?:like|want)|may I|can I)\b"
    r"|\b(?:once|if|after|when|until)\s+(?:you|the user)\s+(?:approve|confirm|authorize|provide)\b",
    re.I,
)
_NEGATED_PROMISE = re.compile(
    r"我(?:们)?(?:将|会|要|需要)\s*(?:暂时|暂|先|继续)?(?:不|无需|无须|避免)"
    r"|\b(?:I['’]ll|I will|I am going to|I['’]m going to|we will|we['’]ll|let me|let us|let['’]s)"
    r"\s+(?:not|never|no longer|avoid)\b",
    re.I,
)
_EXECUTION_CUE = re.compile(
    r"接下来|下一步|我(?:们)?(?:现在|马上|立即|开始)|请(?:稍等|稍候|等待)|让(?:我|我们)"
    r"|\b(?:now|let me|let us|let['’]s|please wait)\b"
    r"|(?:^|[.!?\n]\s*)next[:,]?\s",
    re.I,
)
_ACTION_PROMISE = re.compile(
    r"(?:我(?:们)?(?:将|会|要|需要)|让(?:我|我们))[^。！？\n]{0,120}"
    r"(?:读取|查看|检查|搜索|查找|列出|执行|运行|修改|编辑|写入|调用|获取)"
    r"|(?:稍等|稍候|等待)[^。！？\n]{0,60}我[^。！？\n]{0,60}"
    r"(?:执行|读取|检查|搜索|查找|运行|调用)"
    r"|\b(?:I['’]ll|I will|I am going to|I['’]m going to|we will|we['’]ll|let me|let us|let['’]s)\b"
    r"[^.!?\n]{0,120}\b(?:read|inspect|check|search|list|run|execute|edit|write|modify|look|call|open|fetch)\b",
    re.I,
)


def needs_action_continuation(text: str) -> bool:
    """Only recover a closing promise, not recommendations or approval requests."""
    prose = re.sub(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", "", text, flags=re.S)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    prose = re.sub(r"(?m)^\s*>.*$", "", prose)
    prose = re.sub(r'''"[^"\n]*"|“[^”\n]*”''', "", prose).strip()
    if not prose or _AWAITING_USER.search(prose):
        return False
    closing = re.split(r"\n\s*\n", prose)[-1][-800:]
    return (
        not _NEGATED_PROMISE.search(closing)
        and bool(_EXECUTION_CUE.search(closing))
        and bool(_ACTION_PROMISE.search(closing))
    )


def continuation_prompt(language: str | None) -> str:
    if language == "English":
        return (
            "Your last response ended with an announcement of work, but made no tool call. "
            "Continue the original request: if an authorized action needs a tool, issue a real call to an available tool now; "
            "otherwise provide the completed answer supported by the evidence already obtained. "
            "Do not merely ask the user to wait, invent file contents, or claim an action was executed without a tool result. "
            "Respect the user's scope and permissions. If approval or missing information is required, ask for it and stop."
        )
    return (
        "上一条回复以行动预告结束，但没有真正调用工具。请继续完成原任务："
        "若用户已授权的操作需要工具，请实际调用提供的工具；若已有足够资料，请给出有依据的完整答案。"
        "不要只说请稍等，不要编造文件内容，也不要在没有工具结果时声称操作已经完成。"
        "继续遵守用户的任务范围和权限；若需要用户批准或缺少必要信息，请明确提出并停止。"
    )


def unfinished_notice(language: str | None) -> str:
    if language == "English":
        return (
            "\n\nThe model still returned only an action announcement after one continuation attempt. "
            "The announced action has not been executed, and this task remains incomplete."
        )
    return "\n\n已自动继续尝试一次，但模型仍只返回行动预告；预告中的操作尚未执行，本次任务未完成。"
