import json
import time
from pathlib import Path

def estimate_tokens(messages: list) -> int:
    return len(json.dumps(messages, default=str)) // 4

def microcompact(messages: list):
    """Lightweight compaction: trim old tool results to keep context lean.

    Keeps the last 5 tool results intact (not 3) to preserve decision context.
    Truncates older results to 200 chars instead of clearing them entirely,
    so the model still knows what happened.
    """
    indices = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    indices.append(part)
    if len(indices) <= 5:
        return
    for part in indices[:-5]:
        if isinstance(part.get("content"), str) and len(part["content"]) > 200:
            part["content"] = part["content"][:200] + f"\n...[truncated {len(part['content']) - 200} chars]"

def auto_compact(client, model, messages: list) -> list:
    """Structured compaction: summarize older turns, keep recent ones intact.

    Strategy:
    1. Save full transcript to disk
    2. Build a structured summary prompt that extracts: key decisions,
       files modified, pending tasks, and errors encountered
    3. Keep the last 3 message turns (user + assistant pairs) intact
    4. Return: 1 summary user message + last 3 turns
    """
    transcript_dir = getattr(client, "_transcript_dir", Path(".") / ".transcripts")
    transcript_dir.mkdir(exist_ok=True)
    path = transcript_dir / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    # Split: last 3 turns (6 messages = 3 user+assistant pairs + any tool results)
    keep = min(6, len(messages))
    recent = messages[-keep:]
    older = messages[:-keep] if len(messages) > keep else []

    if not older:
        return messages

    # Build structured summary of older turns
    older_json = json.dumps(older, default=str)[-60000:]
    summary_prompt = (
        "Summarize the conversation below as a structured record for continuity. "
        "Output STRICTLY in this format:\n\n"
        "**Goal:** <one sentence — what the user wants>\n"
        "**Decisions:** <key decisions made, comma-separated>\n"
        "**Files:** <files that were read or modified>\n"
        "**Pending:** <any unfinished tasks>\n"
        "**Errors:** <any errors encountered>\n\n"
        f"Conversation:\n{older_json}"
    )
    try:
        resp = client.messages.create(
            model=model,
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=600,
        )
        summary = resp.content[0].text.strip()
    except Exception:
        summary = "[compacted]"

    # Return: summary + recent turns
    return [
        {"role": "user", "content": f"[Compacted. Transcript: {path}]\n{summary}"},
        *recent,
    ]