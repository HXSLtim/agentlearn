"""trace 的视图模型：spans 和落盘回答的配对（2026-11-03 抽出）。

原来长在 interfaces/tui/trace.py 里；desktop 的只读 trace 面板
（GET /sessions/<id>/trace）要同一套配对，而 web 进程不该为此
import TUI 包（会把 Textual 拖进 HTTP 服务）。两个界面共用的
派生逻辑放 application 层，输入只有 spans 行和 JSONL 消息。
"""
from __future__ import annotations

import json


def span_payloads(store, session_id: str) -> list[dict]:
    """把 spans_for 的 sqlite3.Row 翻成契约 dict（data 解析回对象，含耗时）。

    Web 端点和 TUI 检查器都要这一份形状；以前 web 内联翻一遍、TUI 直接
    塞 Row，结果 answers_for_requests 只认 dict 的 .get()，`main.py trace`
    一启动就 AttributeError。转换收敛到这里，两个界面吃同一种输入。
    """
    payloads = []
    for row in store.spans_for(session_id):
        payloads.append(
            {
                "span_id": row["span_id"],
                "parent_id": row["parent_id"],
                "name": row["name"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "status": row["status"],
                # data 在库里是 JSON 字符串，出站统一解析成对象。
                "data": json.loads(row["data"]) if row["data"] else {},
                "duration_ms": store.span_duration_ms(row["span_id"]),
            }
        )
    return payloads


def answer_texts(messages) -> list[str]:
    """取出已落盘的 assistant 正文，按时间顺序排列。"""
    return [
        "".join(block.text for block in message.content if block.type == "text").strip()
        for message in messages
        if message.role == "assistant"
    ]


def answers_for_requests(spans, answers: list[str]) -> dict[str, str]:
    """把成功的 LLM 请求和落盘的回答按发生顺序配对。

    error / running 请求不消费回答，否则一个半截请求会让后续回答整体错位。
    compaction 请求也不消费回答——它生成的是派生摘要，不落 assistant 正文。
    """
    mapping: dict[str, str] = {}
    cursor = 0
    for span in spans:
        if span["name"] != "llm_request":
            continue
        data = span.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        if isinstance(data, dict) and data.get("purpose") == "compaction":
            mapping[span["span_id"]] = ""
            continue
        if span["status"] == "ok":
            mapping[span["span_id"]] = (
                answers[cursor] if cursor < len(answers) else ""
            )
            cursor += 1
        else:
            mapping[span["span_id"]] = ""
    return mapping
