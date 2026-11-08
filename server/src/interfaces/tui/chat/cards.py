"""时间线卡片：用户/回答消息、思考面板、工具卡、错误卡与空态。"""

from __future__ import annotations

import json

from rich.text import Text

from textual import on

from textual.app import ComposeResult

from textual.containers import Vertical

from textual.message import Message

from textual.widgets import (
    Button,
    Collapsible,
    Markdown,
    Static,
)

from domain.message import Message as DomainMessage

from ..formatting import format_duration
from ..theme import (
    DIM,
    ERROR,
    MUTED,
    PAPER,
    SIGNAL,
    TOOL,
    WARN,
)


def _user_text(message: DomainMessage) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


class UserMessage(Static):
    def __init__(self, text: str, image_count: int = 0) -> None:
        self.search_text = text
        body = Text(text or "(image only)")
        body.stylize(PAPER)
        if image_count:
            body.append(f"\n[ATTACHED IMAGES {image_count}]", style=f"bold {SIGNAL}")
        super().__init__(body, classes="user-message")


class AssistantMessage(Vertical):
    def __init__(self, *, restored: bool = False, show_kicker: bool = True) -> None:
        super().__init__(classes="assistant-message")
        self.body = Markdown("", classes="assistant-body")
        self._restored = restored
        # 连续的助手消息不重复抬头（与 WebUI 的 is-continuation 同规则）：
        # 一轮里第二段正文、工具轮之后的正文，都跟着前一条走。
        self._show_kicker = show_kicker
        self._kicker = Static(self._kicker_text(), classes="message-kicker")
        self._tick = Static("", classes="span-tick")
        self._text = ""
        # 本条消息对应的 turn 耗时（毫秒）。None = 还没有真实数据，
        # 没数据就不画 tick——账本不造假。
        self.duration_ms: float | None = None

    def compose(self) -> ComposeResult:
        if self._show_kicker:
            yield self._kicker
        yield self.body
        yield self._tick

    def _kicker_text(self) -> str:
        return "智能体 · 历史回复" if self._restored else "智能体 · 实时回复"

    @property
    def search_text(self) -> str:
        return self._text

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self.body.update(self._text)

    def set_tick(self, duration_ms: float, max_ms: float) -> None:
        """画本轮耗时的小刻度条（WebUI 的 span-tick 签名元素，终端版）。

        12 格条按「本轮 / 全会话最长轮」比例填充，后面跟人话时长。
        max_ms <= 0 时只画一格——有数但没基准，一格至少说明「这一轮存在」。
        """
        self.duration_ms = duration_ms
        cells = 12
        filled = 1 if duration_ms is not None and duration_ms >= 0 else 0
        if max_ms and max_ms > 0 and duration_ms is not None and duration_ms >= 0:
            filled = max(1, min(cells, round(duration_ms / max_ms * cells)))
        bar = "█" * filled + "·" * (cells - filled)
        self._tick.update(
            Text.assemble(
                (bar[:filled], SIGNAL),
                (bar[filled:], DIM),
                (f"  {format_duration(duration_ms)}", MUTED),
            )
        )
        self._tick.add_class("-on")

    def finish(self) -> None:
        if not self._text:
            self.add_class("-empty")
        elif not self._restored and self._show_kicker:
            self._kicker.update("智能体 · 回复")


class ReasoningPanel(Collapsible):
    def __init__(self, text: str = "", *, restored: bool = False) -> None:
        self._text = text
        self._restored = restored
        self._live = not restored
        self._label = Static("", classes="reasoning-copy")
        super().__init__(
            self._label,
            title=self._display_title(),
            collapsed=True,
            classes=f"reasoning{' -live' if self._live else ''}",
        )
        self._label.update(text)

    def _display_title(self) -> str:
        status = "已存" if self._restored else ("实时" if self._live else "完成")
        return f"思考  ·  {status}  ·  {len(self._text)} 字  ·  {self._snippet()}"

    def _snippet(self) -> str:
        compact = " ".join(self._text.split())
        if not compact:
            return "等待首字"
        tail = compact[-88:]
        return ("…" if len(compact) > len(tail) else "") + tail

    def _visible_text(self) -> str:
        # 流式阶段跟 WebUI 一样只露尾巴，避免长思考把时间线撑满。
        if self._live and len(self._text) > 280:
            return "…" + self._text[-280:]
        return self._text

    @property
    def char_count(self) -> int:
        """字数（活动条目用）；标题里的同一个数，别两处各算各的。"""
        return len(self._text)

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self._label.update(self._visible_text())
        self.title = self._display_title()

    def finish(self) -> None:
        if not self._live:
            return
        self._live = False
        self.remove_class("-live")
        self.add_class("-done")
        self._label.update(self._text)
        self.title = self._display_title()


class ToolCard(Static):
    """一次工具调用的紧凑卡片。id 用来把 result 填回正确的 use。"""

    def __init__(
        self, tool_id: str, name: str, arguments: dict, *, detailed: bool = False
    ) -> None:
        self.tool_id = tool_id
        self.tool_name = name
        self.arguments = arguments
        self.detailed = detailed
        self._output: str | None = None
        self._is_error = False
        self._elapsed_ms: int | None = None
        self._waiting = False
        super().__init__(classes="tool-card")
        self._paint()

    def set_detailed(self, detailed: bool) -> None:
        if self.detailed == detailed:
            return
        self.detailed = detailed
        self._paint()

    def set_waiting(self, waiting: bool) -> None:
        if self._waiting == waiting:
            return
        self._waiting = waiting
        self._paint()

    def _summary(self) -> str:
        preferred = next(
            (
                value
                for key in ("command", "path", "name", "text")
                if isinstance(value := self.arguments.get(key), str) and value.strip()
            ),
            None,
        )
        raw = (
            preferred
            if preferred is not None
            else json.dumps(self.arguments, ensure_ascii=False)
            if self.arguments
            else "无参数"
        )
        return " ".join(str(raw).split()).strip()[:120] or "无参数"

    def _paint(self) -> None:
        if not self.detailed:
            if self._output is None:
                state = "等待审批" if self._waiting else "执行中"
                tone = WARN
            else:
                state = "出错" if self._is_error else "完成"
                if self._elapsed_ms is not None:
                    state += f" · {format_duration(self._elapsed_ms)}"
                tone = ERROR if self._is_error else TOOL
            self.update(
                Text.assemble(
                    ("工具  ", f"bold {tone}"),
                    (self.tool_name, f"bold {PAPER}"),
                    (f"  {state}\n", DIM),
                    (self._summary(), MUTED),
                )
            )
            return
        args = ", ".join(f"{key}={value!r}" for key, value in self.arguments.items())
        call = f"{self.tool_name}({args})"
        if self._output is None:
            state = "等待审批" if self._waiting else "运行中"
            text = Text.assemble(
                (f"{state}  ", f"bold {WARN}"),
                (call, f"bold {PAPER}"),
            )
        else:
            mark = "出错" if self._is_error else "结果"
            elapsed = (
                f"  {format_duration(self._elapsed_ms)}"
                if self._elapsed_ms is not None
                else ""
            )
            text = Text.assemble(
                (f"{mark}  ", f"bold {ERROR if self._is_error else TOOL}"),
                (call, f"bold {PAPER}"),
                (f"{elapsed}\n", DIM),
                (self._output, ERROR if self._is_error else MUTED),
            )
        self.update(text)

    def set_result(self, output: str, is_error: bool, elapsed_ms: int | None) -> None:
        self._output = output.strip() or "（无输出）"
        self._is_error = is_error
        self._elapsed_ms = elapsed_ms
        self._waiting = False
        self._paint()


class RetryErrorCard(Vertical):
    """错误不吞掉；保留现场，并给一个明确的重试入口。"""

    class Retry(Message):
        def __init__(self, question: str) -> None:
            self.question = question
            super().__init__()

    def __init__(self, message: str, question: str) -> None:
        super().__init__(classes="retry-card")
        self.error_message = message
        self.question = question

    def compose(self) -> ComposeResult:
        yield Static(
            Text.assemble(
                ("出错  ·  ", f"bold {ERROR}"),
                (self.error_message, PAPER),
            ),
            classes="retry-message",
        )
        yield Static(
            "已完成的结果仍保留；重试会作为新请求执行。",
            classes="retry-note",
        )
        yield Button("重试  ↻", classes="retry-button")

    @on(Button.Pressed, ".retry-button")
    def _retry(self) -> None:
        self.post_message(self.Retry(self.question))


class EmptyState(Vertical):
    class Prompt(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("新会话  ·  本地运行时", classes="empty-boot")
        yield Static(
            Text.assemble(
                ("$ ", f"bold {SIGNAL}"),
                ("等待输入", f"bold {PAPER}"),
                ("_", SIGNAL),
            ),
            classes="empty-console",
        )
        yield Static(
            "输入任务，模型请求、工具执行和 trace 会沿同一条执行线展开。",
            classes="empty-copy",
        )
        with Vertical(classes="prompt-list"):
            yield Button(
                "[01]  现在几点了？        now_time",
                id="prompt-now",
                classes="prompt-chip",
            )
            yield Button(
                "[02]  查看 sandbox         bash / ls",
                id="prompt-files",
                classes="prompt-chip",
            )
            yield Button(
                "[03]  测试执行边界        reason / act / report",
                id="prompt-boundary",
                classes="prompt-chip",
            )
        yield Static(
            "ENTER 发送   ·   SHIFT+ENTER 换行   ·   JSONL + SQLITE 本地留存",
            classes="empty-meta",
        )

    @on(Button.Pressed, "#prompt-now")
    def _prompt_now(self) -> None:
        self.post_message(self.Prompt("现在几点了？"))

    @on(Button.Pressed, "#prompt-files")
    def _prompt_files(self) -> None:
        self.post_message(self.Prompt("看一下 sandbox 目录里有哪些文件。"))

    @on(Button.Pressed, "#prompt-boundary")
    def _prompt_boundary(self) -> None:
        self.post_message(
            self.Prompt("先说明你准备怎么做，再执行，最后把结论和依据分开列出来。")
        )
