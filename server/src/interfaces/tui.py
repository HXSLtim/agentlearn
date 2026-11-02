"""Textual 终端工作台。

这一层仍然只做输入输出。会话编排、落盘和 trace 都在
application/conversation.py；这里消费 turn() 已经定义好的事件，
把它们排成三个稳定区域：

    顶栏：品牌、当前会话、运行状态
    主体：对话时间线 + 运行账本（窄终端自动收起）
    底栏：多行输入台和键盘提示

交互终端才进这里。stdout / stdin 被重定向时由 cli.py 回退到原来的
逐行模式，避免管道里混进 ANSI 控制序列。
"""
from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Collapsible, Markdown, Static, TextArea

from application.conversation import Conversation
from domain.message import Message as DomainMessage

# 颜色只在样式和状态文字里出现。保持一处定义，Tauri 端也使用同一组
# 语义色（纸张 / 墨色 / 信号橙 / 工具青绿），但两边各自适配终端与像素。
INK = "#12130f"
PANEL = "#1a1c17"
PANEL_RAISED = "#22251e"
PAPER = "#e9e6da"
MUTED = "#92988a"
DIM = "#62685d"
SIGNAL = "#ff8a4c"
TOOL = "#7bd8c2"
ERROR = "#ff6b6b"
LINE = "#35392f"


class PromptComposer(TextArea):
    """多行输入台。Enter 发送，Shift+Enter / Ctrl+J 换行。"""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            value = self.text.strip()
            if value:
                self.post_message(self.Submitted(value))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        body = Text(text)
        body.stylize(PAPER)
        super().__init__(body, classes="user-message")


class AssistantMessage(Vertical):
    def __init__(self, *, restored: bool = False) -> None:
        super().__init__(classes="assistant-message")
        self.body = Markdown("", classes="assistant-body")
        self._restored = restored
        self._text = ""

    def compose(self) -> ComposeResult:
        yield Static(
            "AGENT  /  " + ("RESTORED RESPONSE" if self._restored else "LIVE RESPONSE"),
            classes="message-kicker",
        )
        yield self.body

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self.body.update(self._text)

    def finish(self) -> None:
        if not self._text:
            self.add_class("-empty")


class ReasoningPanel(Collapsible):
    def __init__(self, text: str = "", *, restored: bool = False) -> None:
        self._text = text
        self._restored = restored
        self._label = Static("", classes="reasoning-copy")
        super().__init__(
            self._label,
            title=self._title(),
            collapsed=True,
            classes="reasoning",
        )
        self._label.update(text)

    def _title(self) -> str:
        status = "RESTORED" if self._restored else "LIVE"
        return f"REASONING  /  {status}  ·  {len(self._text)} chars"

    def append_text(self, fragment: str) -> None:
        self._text += fragment
        self._label.update(self._text)
        self.title = self._title()


class ToolCard(Static):
    """一次工具调用的紧凑卡片。id 用来把 result 填回正确的 use。"""

    def __init__(self, tool_id: str, name: str, arguments: dict) -> None:
        self.tool_id = tool_id
        self.name = name
        self.arguments = arguments
        self._output: str | None = None
        self._is_error = False
        self._elapsed_ms: int | None = None
        super().__init__(classes="tool-card")
        self._render()

    def _render(self) -> None:
        args = ", ".join(f"{key}={value!r}" for key, value in self.arguments.items())
        call = f"{self.name}({args})"
        if self._output is None:
            text = Text.assemble(
                ("RUNNING  ", f"bold {TOOL}"),
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
        self._render()


class EmptyState(Vertical):
    class Prompt(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("A SMALL AGENT,\nA CLEAR WORKBENCH.", classes="empty-title")
        yield Static(
            "发一句话开始。工具调用、思考片段和结束原因会留在同一条执行线上。",
            classes="empty-copy",
        )
        with Horizontal(classes="prompt-row"):
            yield Button("现在几点？", id="prompt-now", classes="prompt-chip")
            yield Button("列出 sandbox 文件", id="prompt-files", classes="prompt-chip")
        yield Static(
            "LOCAL SESSION  /  JSONL + SQLITE  /  STREAMING EVENTS",
            classes="empty-meta",
        )

    @on(Button.Pressed, "#prompt-now")
    def _prompt_now(self) -> None:
        self.post_message(self.Prompt("现在几点了？"))

    @on(Button.Pressed, "#prompt-files")
    def _prompt_files(self) -> None:
        self.post_message(self.Prompt("看一下 sandbox 目录里有哪些文件。"))


class AgentLearnTUI(App):
    """AgentLearn 的交互式终端入口。"""

    TITLE = "AgentLearn"
    SUB_TITLE = "local agent workbench"
    CSS = f"""
    Screen {{
        background: {INK};
        color: {PAPER};
    }}

    #topbar {{
        height: 3;
        background: {PANEL};
        border-bottom: solid {LINE};
        padding: 0 2;
        align: center middle;
    }}

    #brand {{
        width: auto;
        color: {PAPER};
        text-style: bold;
        letter-spacing: 1;
    }}

    #brand-mark {{
        color: {SIGNAL};
    }}

    #top-session {{
        width: 1fr;
        padding-left: 3;
        color: {MUTED};
    }}

    #top-state {{
        width: auto;
        color: {TOOL};
        text-align: right;
    }}

    #workspace {{
        height: 1fr;
    }}

    #timeline {{
        width: 1fr;
        padding: 2 3;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {INK};
    }}

    #ledger {{
        width: 34;
        background: {PANEL};
        border-left: solid {LINE};
        padding: 2 2;
    }}

    .ledger-kicker {{
        color: {SIGNAL};
        text-style: bold;
        margin-bottom: 1;
    }}

    .ledger-rule {{
        height: 1;
        border-top: solid {LINE};
        margin: 1 0;
    }}

    .ledger-label {{
        color: {DIM};
        margin-top: 1;
    }}

    .ledger-value {{
        color: {PAPER};
    }}

    #ledger-state {{
        color: {TOOL};
        text-style: bold;
    }}

    #ledger-session {{
        color: {MUTED};
    }}

    #key-help {{
        color: {DIM};
        margin-top: 2;
    }}

    #empty {{
        width: 100%;
        height: auto;
        padding: 4 0 2 0;
    }}

    .empty-title {{
        color: {PAPER};
        text-style: bold;
        margin-bottom: 1;
    }}

    .empty-copy {{
        color: {MUTED};
        max-width: 72;
        margin-bottom: 2;
    }}

    .prompt-row {{
        height: auto;
        margin-bottom: 2;
    }}

    .prompt-chip {{
        width: auto;
        min-width: 14;
        height: 3;
        margin-right: 1;
        padding: 0 1;
        border: solid {LINE};
        background: {PANEL_RAISED};
        color: {PAPER};
    }}

    .prompt-chip:hover, .prompt-chip:focus {{
        border: solid {SIGNAL};
        color: {SIGNAL};
    }}

    .empty-meta {{
        color: {DIM};
        margin-top: 1;
    }}

    .user-message {{
        width: 90%;
        height: auto;
        margin: 1 0 2 0;
        padding: 1 2;
        background: {PANEL_RAISED};
        border-left: thick {SIGNAL};
        color: {PAPER};
    }}

    .assistant-message {{
        width: 100%;
        height: auto;
        margin: 0 0 2 0;
    }}

    .assistant-message.-empty {{
        display: none;
    }}

    .message-kicker {{
        color: {MUTED};
        margin-bottom: 1;
    }}

    .assistant-body {{
        width: 100%;
        height: auto;
        background: transparent;
        color: {PAPER};
        margin: 0;
        padding: 0;
    }}

    MarkdownFence {{
        background: #0c0d0b;
        border-left: solid {TOOL};
        margin: 1 0;
        padding: 1 2;
    }}

    MarkdownH1, MarkdownH2, MarkdownH3 {{
        color: {PAPER};
        text-style: bold;
    }}

    MarkdownBlockQuote {{
        border-left: thick {SIGNAL};
        color: {MUTED};
        padding-left: 2;
    }}

    .reasoning {{
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        border: none;
        background: transparent;
    }}

    .reasoning > CollapsibleTitle {{
        color: {DIM};
        background: transparent;
    }}

    .reasoning Copy {{
        color: {MUTED};
        background: {PANEL};
        padding: 1 2;
        border-left: solid {LINE};
    }}

    .tool-card {{
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border-left: thick {TOOL};
        background: #171a16;
        color: {MUTED};
    }}

    #composer-shell {{
        height: auto;
        min-height: 6;
        max-height: 12;
        background: {PANEL};
        border-top: solid {LINE};
        padding: 1 2 1 2;
    }}

    #composer-label {{
        height: 1;
        color: {SIGNAL};
        text-style: bold;
    }}

    #composer-row {{
        height: auto;
        min-height: 3;
    }}

    #composer {{
        width: 1fr;
        height: auto;
        min-height: 3;
        max-height: 6;
        background: {PANEL_RAISED};
        border: solid {LINE};
        color: {PAPER};
        padding: 0 1;
    }}

    #composer:focus {{
        border: solid {SIGNAL};
    }}

    #send {{
        width: 13;
        height: 3;
        margin-left: 1;
        border: solid {SIGNAL};
        background: {SIGNAL};
        color: {INK};
        text-style: bold;
    }}

    #send:hover, #send:focus {{
        background: {PAPER};
        border: solid {PAPER};
    }}

    #composer-help {{
        height: 1;
        color: {DIM};
        margin-top: 1;
    }}

    Screen.-compact #ledger {{
        display: none;
    }}

    Screen.-compact #timeline {{
        padding: 1 1;
    }}

    Screen.-compact .user-message {{
        width: 100%;
    }}

    Screen.-compact #composer-shell {{
        padding: 1 1;
    }}
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "quit", show=False),
        Binding("ctrl+n", "focus_composer", "composer", show=False),
    ]

    def __init__(
        self,
        conversation: Conversation,
        history: list[DomainMessage],
        session_id: str,
    ) -> None:
        super().__init__()
        self.conversation = conversation
        self.history = history
        self.session_id = session_id
        self.streaming = False
        self.turn_started = 0.0
        self.turn_count = sum(1 for message in history if message.role == "user" and any(
            block.type == "text" for block in message.content
        ))
        self.tool_count = 0
        self.stop_reason = "—"
        self._assistant: AssistantMessage | None = None
        self._reasoning: ReasoningPanel | None = None
        self._tool_cards: dict[str, ToolCard] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(Text.assemble(("◢ ", SIGNAL), ("AGENTLEARN", f"bold {PAPER}")), id="brand")
            yield Static(self._session_line(), id="top-session")
            yield Static("● LOCAL  /  READY", id="top-state")

        with Horizontal(id="workspace"):
            with VerticalScroll(id="timeline"):
                yield EmptyState(id="empty") if not self.history else Static(
                    "RESTORED THREAD", classes="message-kicker"
                )
            with Vertical(id="ledger"):
                yield Static("RUN LEDGER", classes="ledger-kicker")
                yield Static("STATE", classes="ledger-label")
                yield Static("READY", id="ledger-state")
                yield Static("SESSION", classes="ledger-label")
                yield Static(self._short_session(), id="ledger-session")
                yield Static("MODEL", classes="ledger-label")
                yield Static(self.conversation.chat.model, id="ledger-model", classes="ledger-value")
                yield Static("", classes="ledger-rule")
                yield Static("TURNS", classes="ledger-label")
                yield Static(str(self.turn_count), id="ledger-turns", classes="ledger-value")
                yield Static("TOOL CALLS", classes="ledger-label")
                yield Static(str(self.tool_count), id="ledger-tools", classes="ledger-value")
                yield Static("ELAPSED", classes="ledger-label")
                yield Static("—", id="ledger-elapsed", classes="ledger-value")
                yield Static("STOP REASON", classes="ledger-label")
                yield Static("—", id="ledger-stop", classes="ledger-value")
                yield Static("", classes="ledger-rule")
                yield Static(
                    "ENTER       send\n"
                    "SHIFT+ENTER newline\n"
                    "CTRL+J      newline\n"
                    "CTRL+N      focus input\n"
                    "CTRL+Q      quit",
                    id="key-help",
                )

        with Vertical(id="composer-shell"):
            yield Static("COMPOSE  /  ONE TURN AT A TIME", id="composer-label")
            with Horizontal(id="composer-row"):
                yield PromptComposer(
                    placeholder="向 agent 发消息…",
                    id="composer",
                    soft_wrap=True,
                    compact=True,
                )
                yield Button("RUN  ↵", id="send")
            yield Static(
                "流式回答会留在时间线里  ·  工具和思考不打断对话",
                id="composer-help",
            )

    def on_mount(self) -> None:
        if self.history:
            self._mount_history()
        self.query_one("#composer", PromptComposer).focus()
        self.set_interval(0.25, self._tick_elapsed)
        self._update_compact_layout(self.size.width)

    def on_resize(self, event: events.Resize) -> None:
        self._update_compact_layout(event.size.width)

    def _update_compact_layout(self, width: int) -> None:
        self.screen.set_class(width < 106, "-compact")

    def _session_line(self) -> str:
        count = len(self.history)
        return f"SESSION {self._short_session()}  ·  {count} MESSAGES"

    def _short_session(self) -> str:
        return self.session_id[:8]

    def _mount_history(self) -> None:
        timeline = self.query_one("#timeline", VerticalScroll)
        by_tool_id: dict[str, ToolCard] = {}
        for message in self.history:
            if message.role == "user":
                text = "".join(block.text for block in message.content if block.type == "text")
                if text:
                    timeline.mount(UserMessage(text))
                for block in message.content:
                    if block.type == "tool_result":
                        card = by_tool_id.get(block.tool_use_id)
                        if card is None:
                            card = ToolCard(block.tool_use_id, "tool", {})
                        card.set_result(block.content, block.is_error, None)
                continue

            text = "".join(
                block.text for block in message.content if block.type == "text"
            )
            thinking = "".join(
                block.thinking for block in message.content if block.type == "thinking"
            )
            if thinking:
                timeline.mount(ReasoningPanel(thinking, restored=True))
            if text:
                assistant = AssistantMessage(restored=True)
                timeline.mount(assistant)
                assistant.append_text(text)
            for block in message.content:
                if block.type == "tool_use":
                    card = ToolCard(block.id, block.name, block.input)
                    by_tool_id[block.id] = card
                    timeline.mount(card)
        self.call_after_refresh(timeline.scroll_end, animate=False)

    async def _submit(self, question: str) -> None:
        question = question.strip()
        if not question or self.streaming:
            return

        empty = self.query("#empty")
        if empty:
            await empty.remove()

        composer = self.query_one("#composer", PromptComposer)
        composer.load_text("")
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(UserMessage(question))
        self._assistant = AssistantMessage()
        self._reasoning = None
        self._tool_cards = {}
        await timeline.mount(self._assistant)

        self.streaming = True
        self.turn_started = time.monotonic()
        self.turn_count += 1
        self.tool_count = 0
        self.stop_reason = "—"
        self._set_running_state("STREAMING")
        self._update_ledger()
        self._send_button(disabled=True, label="RUNNING")
        self.call_after_refresh(timeline.scroll_end, animate=False)
        self._run_turn(question)

    @work(thread=True, exclusive=True, group="agent-turn")
    def _run_turn(self, question: str) -> None:
        """网络和工具执行在 worker 线程；所有 widget 修改都回主线程。"""
        try:
            for event in self.conversation.turn(question):
                try:
                    self.call_from_thread(self._apply_event, event)
                except RuntimeError:
                    return
        except Exception as exc:
            try:
                self.call_from_thread(self._finish_error, str(exc))
            except RuntimeError:
                return
        else:
            try:
                self.call_from_thread(self._finish_turn, self.conversation.stop_reason or "—")
            except RuntimeError:
                return

    async def _apply_event(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "thinking":
            if self._reasoning is None:
                self._reasoning = ReasoningPanel()
                await self.query_one("#timeline", VerticalScroll).mount(
                    self._reasoning, before=self._assistant
                )
            self._reasoning.append_text(event["text"])
            return
        if kind == "text":
            if self._assistant is not None:
                self._assistant.append_text(event["text"])
                self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "tool_use":
            self.tool_count += 1
            card = ToolCard(event["id"], event["name"], event["input"])
            self._tool_cards[event["id"]] = card
            await self.query_one("#timeline", VerticalScroll).mount(card)
            self._update_ledger()
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "tool_result":
            card = self._tool_cards.get(event["id"])
            if card is not None:
                card.set_result(
                    event["output"],
                    event["is_error"],
                    event["elapsed_ms"],
                )
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)

    async def _finish_turn(self, stop_reason: str) -> None:
        self.streaming = False
        self.stop_reason = stop_reason
        if self._assistant is not None:
            self._assistant.finish()
        self._set_running_state("COMPLETE")
        self._update_ledger()
        self._send_button(disabled=False, label="RUN  ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _finish_error(self, message: str) -> None:
        self.streaming = False
        self.stop_reason = "error"
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(
            Static(Text.assemble(("ERROR  /  ", f"bold {ERROR}"), (message, PAPER)), classes="tool-card")
        )
        self._set_running_state("ERROR")
        self._update_ledger()
        self._send_button(disabled=False, label="RETRY  ↵")
        self.query_one("#composer", PromptComposer).focus()

    def _set_running_state(self, state: str) -> None:
        color = TOOL if state in ("READY", "COMPLETE") else (ERROR if state == "ERROR" else SIGNAL)
        self.query_one("#top-state", Static).update(
            Text.assemble(("● LOCAL  /  ", TOOL), (state, f"bold {color}"))
        )
        ledger = self.query_one("#ledger-state", Static)
        ledger.update(state)
        ledger.styles.color = color

    def _update_ledger(self) -> None:
        self.query_one("#ledger-turns", Static).update(str(self.turn_count))
        self.query_one("#ledger-tools", Static).update(str(self.tool_count))
        self.query_one("#ledger-stop", Static).update(self.stop_reason)
        if not self.streaming:
            self.query_one("#ledger-elapsed", Static).update(
                f"{round((time.monotonic() - self.turn_started) * 1000)}ms"
                if self.turn_started else "—"
            )

    def _tick_elapsed(self) -> None:
        if self.streaming:
            elapsed = round((time.monotonic() - self.turn_started) * 1000)
            self.query_one("#ledger-elapsed", Static).update(f"{elapsed}ms")

    def _send_button(self, *, disabled: bool, label: str) -> None:
        button = self.query_one("#send", Button)
        button.disabled = disabled
        button.label = label

    @on(PromptComposer.Submitted)
    async def _composer_submitted(self, event: PromptComposer.Submitted) -> None:
        await self._submit(event.value)

    @on(EmptyState.Prompt)
    async def _empty_prompt(self, event: EmptyState.Prompt) -> None:
        await self._submit(event.value)

    @on(Button.Pressed, "#send")
    async def _send_pressed(self) -> None:
        await self._submit(self.query_one("#composer", PromptComposer).text)

    def action_focus_composer(self) -> None:
        self.query_one("#composer", PromptComposer).focus()


def run(
    conversation: Conversation,
    history: list[DomainMessage],
    session_id: str | None = None,
) -> None:
    """启动 TUI。main.py 已经把 cwd 钉在 server/。"""
    session_id = session_id or str(uuid.uuid4())
    AgentLearnTUI(conversation, history, session_id).run()
