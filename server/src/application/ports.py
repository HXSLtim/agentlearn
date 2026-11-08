"""application 层依赖的外部能力契约。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from domain.message import ImageBlock, Message
from domain.tool import Tool


class ChatClient(Protocol):
    """会话循环需要的模型客户端能力。"""

    model: str
    effort: str | None
    effort_note: str
    usage: dict
    blocks: list
    stop_reason: str | None

    def stream(
        self,
        history: list[Message],
        tools: list[Tool],
        *,
        system: str = "",
    ) -> Iterator[tuple[str, str]]:
        ...


class TranscriptStore(Protocol):
    """会话正文存储能力。"""

    def path_for(self, session_id: str) -> Path:
        ...

    def summary_path_for(self, session_id: str) -> Path:
        ...

    def append(self, session_id: str, message: Message) -> None:
        ...

    def read(self, session_id: str) -> list[Message]:
        ...


class SpanStore(Protocol):
    """会话索引和执行轨迹存储能力。"""

    def create_session(
        self, session_id: str, model: str, jsonl_path: str, title: str = ""
    ) -> None:
        ...

    def has_session(self, session_id: str) -> bool:
        ...

    def get_workdir(self, session_id: str) -> str:
        ...

    def set_workdir(self, session_id: str, workdir: str) -> bool:
        ...

    def set_title_if_empty(self, session_id: str, title: str) -> None:
        ...

    def start_span(
        self,
        session_id: str,
        name: str,
        parent_id: str | None = None,
        data: dict | None = None,
    ) -> str:
        ...

    def end_span(
        self, span_id: str, status: str = "ok", data: dict | None = None
    ) -> None:
        ...

    def span_duration_ms(self, span_id: str) -> int | None:
        ...

    def spans_for(self, session_id: str) -> list:
        ...


class ToolGateway(Protocol):
    """外部工具网关能力；MCP 只是这个端口的一种实现。"""

    def list_tools(self) -> list[tuple[str, dict]]:
        ...

    def call(
        self, alias: str, name: str, arguments: dict
    ) -> tuple[str, bool, ImageBlock | None]:
        ...

    def health(self) -> list[dict]:
        ...
