"""AgentLearn 的统一 TUI 门面。

`main.py` 只依赖本模块导出的两个入口：

- run_chat：交互式会话工作台
- run_trace：只读 span 检查器

内部实现分开，避免对话 worker、模型依赖和 trace 只读逻辑互相污染。
SessionSummary 是 run_chat 的会话选择器数据形状，也从门面导出。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["SessionSummary", "run_chat", "run_trace"]

if TYPE_CHECKING:
    from .chat import SessionSummary, run_chat
    from .trace import run_trace


def __getattr__(name: str):
    """按需加载入口，让 `trace` 模式不 import 对话工作台。"""
    if name == "run_chat":
        from .chat import run_chat

        return run_chat
    if name == "run_trace":
        from .trace import run_trace

        return run_trace
    if name == "SessionSummary":
        from .chat import SessionSummary

        return SessionSummary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
