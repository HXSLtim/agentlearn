"""会话分支：从源会话的指定消息点复制出新会话（P1 会话库）。

原来长在 web 端点里；TUI /resume 的分支动作要同一套语义，抽到
application 两端共用。输入只有 store / transcript 两个端口形状，
不认识 FastAPI 也不认识 Textual。
"""
from __future__ import annotations

import uuid


def fork_session(store, transcript, source_id: str, through: int) -> tuple[str, int]:
    """保留源会话前 through 条消息，复制出一个新会话，返回 (新 id, 条数)。

    spans 不复制——trace 是「这次运行怎么跑出来的」，分支的树从
    第一轮 turn 重新长。新会话标题加「（分支）」后缀，好认也随时可改。
    through 越界、源会话不在索引里都抛 ValueError，调用方翻译成
    自己界面的错误（HTTP 422 / TUI 错误收条）。
    """
    row = None
    for candidate in store.list_sessions(limit=1000, include_archived=True):
        if candidate["id"] == source_id:
            row = candidate
            break
    if row is None:
        raise ValueError("源会话不存在。")
    messages = transcript.read(source_id)
    if through < 1 or through > len(messages):
        raise ValueError(f"through 要在 1..{len(messages)} 之间（消息总数 {len(messages)}）。")
    new_id = str(uuid.uuid4())
    store.create_session(
        new_id,
        row["model"] or "",
        str(transcript.path_for(new_id)),
        title=(row["title"] or "") + "（分支）",
    )
    for message in messages[:through]:
        transcript.append(new_id, message)
    return new_id, through
