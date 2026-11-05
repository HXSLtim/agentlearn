"""时间线卡片：用户/回答消息、思考面板、工具卡、错误卡与空态。"""

from __future__ import annotations

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
    def __init__(self, *, restored: bool = False) -> None:
        super().__init__(classes="assistant-message")
        self.body = Markdown("", classes="assistant-body")
        self._restored = restored
        self._kicker = Static(self._kicker_text(), classes="message-kicker")
        self._text = ""

    def compose(self) -> ComposeResult:
        yield self._kicker
        yield self.body

    def _kicker_text(self) -> str:
        return "AGENT  /  " + ("RESTORED RESPONSE" if self._restored else "LIVE RESPONSE")

    @property
    def search_text(self) -> str:
        return self._text

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self.body.update(self._text)

    def finish(self) -> None:
        if not self._text:
            self.add_class("-empty")
        elif not self._restored:
            self._kicker.update("AGENT  /  RESPONSE")


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
        status = "STORED" if self._restored else ("LIVE" if self._live else "DONE")
        return f"THINKING  /  {status}  ·  {len(self._text)} chars  ·  {self._snippet()}"

    def _snippet(self) -> str:
        compact = " ".join(self._text.split())
        if not compact:
            return "waiting for first token"
        tail = compact[-88:]
        return ("…" if len(compact) > len(tail) else "") + tail

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self._label.update(self._text)
        self.title = self._display_title()

    def finish(self) -> None:
        if not self._live:
            return
        self._live = False
        self.remove_class("-live")
        self.add_class("-done")
        self.title = self._display_title()


class ToolCard(Static):
    """一次工具调用的紧凑卡片。id 用来把 result 填回正确的 use。"""

    def __init__(self, tool_id: str, name: str, arguments: dict) -> None:
        self.tool_id = tool_id
        self.tool_name = name
        self.arguments = arguments
        self._output: str | None = None
        self._is_error = False
        self._elapsed_ms: int | None = None
        super().__init__(classes="tool-card")
        self._paint()

    def _paint(self) -> None:
        args = ", ".join(f"{key}={value!r}" for key, value in self.arguments.items())
        call = f"{self.tool_name}({args})"
        if self._output is None:
            text = Text.assemble(
                ("RUNNING  ", f"bold {WARN}"),
                (call, f"bold {PAPER}"),
                ("\nwaiting for tool result", DIM),
            )
        else:
            mark = "ERROR" if self._is_error else "RESULT"
            elapsed = f"  {self._elapsed_ms}ms" if self._elapsed_ms is not None else ""
            text = Text.assemble(
                (f"{mark}  ", f"bold {ERROR if self._is_error else TOOL}"),
                (call, f"bold {PAPER}"),
                (f"{elapsed}\n", DIM),
                (self._output, ERROR if self._is_error else MUTED),
            )
        self.update(text)

    def set_result(self, output: str, is_error: bool, elapsed_ms: int | None) -> None:
        self._output = output.strip() or "(no output)"
        self._is_error = is_error
        self._elapsed_ms = elapsed_ms
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
                ("ERROR  /  ", f"bold {ERROR}"),
                (self.error_message, PAPER),
            ),
            classes="retry-message",
        )
        yield Static(
            "已完成的结果仍保留；重试会作为新请求执行。",
            classes="retry-note",
        )
        yield Button("RETRY  ↻", classes="retry-button")

    @on(Button.Pressed, ".retry-button")
    def _retry(self) -> None:
        self.post_message(self.Retry(self.question))


class EmptyState(Vertical):
    class Prompt(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("SESSION 00  /  LOCAL RUNTIME", classes="empty-boot")
        yield Static(
            Text.assemble(
                ("$ ", f"bold {SIGNAL}"),
                ("awaiting input", f"bold {PAPER}"),
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
            "ENTER / RUN   ·   SHIFT+ENTER / NEWLINE   ·   JSONL + SQLITE / LOCAL",
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
