"""把 JSONL 会话正文导出成给人读的 Markdown。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from domain.message import Message


def _fenced(text: str, language: str = "") -> str:
    marker_length = 3
    while "`" * marker_length in text:
        marker_length += 1
    marker = "`" * marker_length
    return f"{marker}{language}\n{text.rstrip()}\n{marker}"


def transcript_to_markdown(
    messages: list[Message],
    *,
    session_id: str,
    model: str = "",
) -> str:
    exported_at = datetime.now(UTC).isoformat(timespec="seconds")
    tool_names = {
        block.id: block.name
        for message in messages
        if message.role == "assistant"
        for block in message.content
        if block.type == "tool_use"
    }
    lines = [
        "# AgentLearn Transcript",
        "",
        f"- Session: `{session_id}`",
        f"- Model: `{model or 'unknown'}`",
        f"- Exported at: `{exported_at}`",
        f"- Messages: `{len(messages)}`",
        "",
        "---",
        "",
    ]
    for message in messages:
        if message.role == "user":
            lines.extend(["## User", ""])
        else:
            lines.extend(["## Agent", ""])
        for block in message.content:
            if block.type == "text" and block.text:
                lines.extend([block.text, ""])
            elif block.type == "thinking" and block.thinking:
                lines.extend(["### Thinking", "", _fenced(block.thinking, "text"), ""])
            elif block.type == "tool_use":
                lines.extend(
                    [
                        f"### Tool Call: `{block.name}`",
                        "",
                        f"- Tool ID: `{block.id}`",
                        "- Input:",
                        "",
                        _fenced(json.dumps(block.input, ensure_ascii=False, indent=2), "json"),
                        "",
                    ]
                )
            elif block.type == "tool_result":
                name = tool_names.get(block.tool_use_id, "unknown")
                status = "error" if block.is_error else "ok"
                lines.extend(
                    [
                        f"### Tool Result: `{name}`",
                        "",
                        f"- Tool ID: `{block.tool_use_id}`",
                        f"- Status: `{status}`",
                        "- Output:",
                        "",
                        _fenced(block.content, "text"),
                        "",
                    ]
                )
                if block.image is not None:
                    lines.extend(
                        [
                            f"![tool image](data:{block.image.media_type};base64,{block.image.data})",
                            "",
                        ]
                    )
            elif block.type == "image":
                lines.extend(
                    [
                        f"![user image](data:{block.media_type};base64,{block.data})",
                        "",
                    ]
                )
        lines.extend(["---", ""])
    return "\n".join(lines).rstrip() + "\n"
