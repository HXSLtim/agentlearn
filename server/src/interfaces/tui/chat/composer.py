"""输入台：多行编辑、队列项与附图类型白名单。"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Callable

from textual import events

from textual.message import Message

from textual.widgets import (
    TextArea,
)

from domain.message import ImageBlock


@dataclass(frozen=True)
class QueuedPrompt:
    question: str
    images: tuple[ImageBlock, ...] = ()


ATTACH_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class PromptComposer(TextArea):
    """多行输入台。Enter 发送，Shift+Enter / Ctrl+J 换行。"""

    command_key_handler: Callable[[events.Key], bool] | None = None
    history_key_handler: Callable[[events.Key], bool] | None = None
    escape_handler: Callable[[events.Key], bool] | None = None
    allow_empty_submit = False

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    @staticmethod
    def _consume_key(event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def _run_key_handler(
        self,
        handler: Callable[[events.Key], bool] | None,
        event: events.Key,
    ) -> bool:
        if handler is None or not handler(event):
            return False
        self._consume_key(event)
        return True

    def _submit_from_enter(self, event: events.Key) -> None:
        self._consume_key(event)
        value = self.text.strip()
        if value or self.allow_empty_submit:
            self.post_message(self.Submitted(value))

    def _insert_newline(self, event: events.Key) -> None:
        self._consume_key(event)
        self.insert("\n")

    async def _on_key(self, event: events.Key) -> None:
        if self._run_key_handler(self.command_key_handler, event):
            return
        if self._run_key_handler(self.history_key_handler, event):
            return
        if (
            event.key == "escape"
            and self._run_key_handler(self.escape_handler, event)
        ):
            return
        if event.key == "enter":
            self._submit_from_enter(event)
            return
        if event.key in ("shift+enter", "ctrl+j"):
            self._insert_newline(event)
            return
        await super()._on_key(event)
