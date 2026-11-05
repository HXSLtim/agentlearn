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

    async def _on_key(self, event: events.Key) -> None:
        command_key_handler = self.command_key_handler
        if command_key_handler is not None and command_key_handler(event):
            event.stop()
            event.prevent_default()
            return
        history_key_handler = self.history_key_handler
        if history_key_handler is not None and history_key_handler(event):
            event.stop()
            event.prevent_default()
            return
        escape_handler = self.escape_handler
        if (
            event.key == "escape"
            and escape_handler is not None
            and escape_handler(event)
        ):
            event.stop()
            event.prevent_default()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            value = self.text.strip()
            if value or self.allow_empty_submit:
                self.post_message(self.Submitted(value))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)
