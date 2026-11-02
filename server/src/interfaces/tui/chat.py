"""Textual 终端工作台。

这一层仍然只做输入输出。会话编排、落盘和 trace 都在
application/conversation.py；这里消费 turn() 已经定义好的事件，
把它们排成三个稳定区域：

    顶栏：品牌、当前会话、运行状态
    主体：对话时间线 + 运行账本（窄终端自动收起）
    底栏：斜杠命令面板、多行输入台和键盘提示

`main.py tui` 负责装配 Conversation，然后把控制权交给这里；
终端的具体呈现和键盘交互都收在 Textual 应用里。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from application.conversation import Conversation
from domain.message import Message as DomainMessage
from infrastructure.config import SettingsSnapshot, SettingsValues
from .theme import (
    DIM,
    ERROR,
    INK,
    LINE,
    MUTED,
    PANEL,
    PANEL_RAISED,
    PAPER,
    SIGNAL,
    TOOL,
    WARN,
    apply_agentlearn_theme,
)


@dataclass(frozen=True)
class SlashCommand:
    name: str
    usage: str
    description: str
    group: str
    aliases: tuple[str, ...] = ()
    takes_argument: bool = False


@dataclass(frozen=True)
class SessionSummary:
    """会话选择器需要的一行；由组合根从 Store + Transcript 映射出来。"""

    session_id: str
    title: str
    model: str
    created_at: str
    turns: int


COMMANDS = (
    SlashCommand(
        "resume",
        "/resume [id]",
        "选择并续接历史会话",
        "SESSION",
        takes_argument=True,
    ),
    SlashCommand("clear", "/clear", "清空当前上下文并开新会话", "SESSION"),
    SlashCommand("new", "/new", "新开一个空会话", "SESSION"),
    SlashCommand("status", "/status", "查看当前会话与运行状态", "RUNTIME"),
    SlashCommand(
        "model",
        "/model <id>",
        "切换运行模型；不带参数打开设置",
        "RUNTIME",
        takes_argument=True,
    ),
    SlashCommand(
        "config",
        "/config",
        "打开 config.ini 模型设置",
        "RUNTIME",
        ("settings",),
    ),
    SlashCommand("now", "/now", "触发 now_time 示例", "TASK"),
    SlashCommand("files", "/files", "查看 sandbox 目录", "TASK"),
    SlashCommand(
        "boundary",
        "/boundary",
        "触发 reason / act / report 示例",
        "TASK",
    ),
    SlashCommand("help", "/help", "显示全部命令", "SYSTEM"),
    SlashCommand("quit", "/quit", "退出终端工作台", "SYSTEM"),
)
COMMANDS_BY_NAME = {
    alias: command
    for command in COMMANDS
    for alias in (command.name, *command.aliases)
}


class PromptComposer(TextArea):
    """多行输入台。Enter 发送，Shift+Enter / Ctrl+J 换行。"""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        command_key_handler = getattr(self, "command_key_handler", None)
        if command_key_handler is not None and command_key_handler(event):
            event.stop()
            event.prevent_default()
            return
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


class CommandPalette(Static):
    """输入 `/` 时出现的轻量命令索引，不抢输入焦点。"""

    def __init__(self) -> None:
        super().__init__(classes="command-palette")
        self._matches = list(COMMANDS)
        self._selected = 0
        self.display = False

    @property
    def selected(self) -> SlashCommand:
        return self._matches[self._selected]

    def filter(self, query: str) -> bool:
        text = query.strip()
        if not text.startswith("/") or any(char.isspace() for char in text):
            self.display = False
            return False
        prefix = text[1:].casefold()
        matches = [
            command
            for command in COMMANDS
            if any(
                candidate.startswith(prefix)
                for candidate in (command.name, *command.aliases)
            )
        ]
        if not matches:
            self.display = False
            return False
        self._matches = matches
        self._selected = min(self._selected, len(matches) - 1)
        self.update(self._paint())
        self.display = True
        return True

    def move(self, delta: int) -> None:
        if not self.display:
            return
        self._selected = (self._selected + delta) % len(self._matches)
        self.update(self._paint())

    def _paint(self) -> Text:
        text = Text.assemble(
            ("COMMAND INDEX", f"bold {SIGNAL}"),
            ("  /  SESSION COMMANDS FIRST\n", DIM),
        )
        group = ""
        for index, command in enumerate(self._matches):
            if command.group != group:
                group = command.group
                text.append(f"{group}\n", f"bold {DIM}")
            selected = index == self._selected
            row_style = f"bold {INK} on {SIGNAL}" if selected else PAPER
            detail_style = f"{INK} on {SIGNAL}" if selected else MUTED
            text.append("  ▶ " if selected else "    ", style=SIGNAL)
            text.append(f"{command.usage:<22}", style=row_style)
            text.append(command.description + "\n", style=detail_style)
        text.append("↑↓ SELECT · TAB COMPLETE · ENTER RUN · ESC CLOSE", style=DIM)
        return text


class CommandResult(Static):
    """命令的本地结果；不会写进 JSONL，也不会发给模型。"""

    def __init__(self, title: str, body: str, *, error: bool = False) -> None:
        tone = ERROR if error else TOOL
        text = Text.assemble(
            ("COMMAND  /  ", f"bold {tone}"),
            (title, f"bold {PAPER}"),
            ("\n", DIM),
            (body, ERROR if error else MUTED),
        )
        super().__init__(text, classes="command-result")


class ResumeScreen(ModalScreen[str | None]):
    """历史会话选择器。选中后退出当前 App，由 main.py 重新装配目标会话。"""

    CSS = f"""
    ResumeScreen {{
        align: center middle;
        background: rgba(0,0,0,0.84);
    }}

    #resume-dialog {{
        width: 88;
        max-width: 94%;
        height: 78%;
        background: {PANEL};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #resume-header {{
        height: 3;
        align: center middle;
    }}

    #resume-title {{
        width: 1fr;
        color: {SIGNAL};
        text-style: bold;
    }}

    #resume-close {{
        width: 5;
        min-width: 5;
        height: 3;
        border: none;
        background: transparent;
        color: {MUTED};
    }}

    #resume-note {{
        height: auto;
        margin-bottom: 1;
        color: {DIM};
    }}

    #resume-list {{
        height: 1fr;
        background: {INK};
        border: solid {LINE};
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {INK};
    }}

    #resume-list:focus {{
        border: solid {SIGNAL};
    }}

    #resume-list > .option-list--option {{
        padding: 1 1;
        border-bottom: solid {LINE};
        color: {PAPER};
    }}

    #resume-list > .option-list--option-highlighted {{
        color: {INK};
        background: {SIGNAL};
        text-style: bold;
    }}

    #resume-footer {{
        height: 2;
        padding-top: 1;
        color: {DIM};
    }}
    """

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    def __init__(
        self,
        sessions: list[SessionSummary],
        current_session_id: str,
    ) -> None:
        super().__init__()
        self.sessions = sessions
        self.current_session_id = current_session_id

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            with Horizontal(id="resume-header"):
                yield Static("RESUME  /  STORED SESSIONS", id="resume-title")
                yield Button("×", id="resume-close")
            yield Static(
                f"当前 {self.current_session_id[:8]}  ·  "
                f"{len(self.sessions)} 个已落盘会话  ·  正文来自 JSONL",
                id="resume-note",
            )
            yield OptionList(
                *(
                    Option(self._option_text(session), id=session.session_id)
                    for session in self.sessions
                ),
                id="resume-list",
            )
            yield Static(
                "↑↓ SELECT  ·  ENTER RESUME  ·  ESC CLOSE",
                id="resume-footer",
            )

    def on_mount(self) -> None:
        option_list = self.query_one("#resume-list", OptionList)
        for index, session in enumerate(self.sessions):
            if session.session_id == self.current_session_id:
                option_list.highlighted = index
                break
        option_list.focus()
        option_list.scroll_to_highlight()

    def _option_text(self, session: SessionSummary) -> Text:
        current = session.session_id == self.current_session_id
        text = Text()
        text.append(_format_session_time(session.created_at), style=DIM)
        text.append(f"  {session.session_id[:8]}", style=f"bold {SIGNAL}")
        text.append(f"  {session.turns} TURNS", style=MUTED)
        if current:
            text.append("  CURRENT", style=f"bold {WARN}")
        text.append(f"\n  {session.title}", style=PAPER)
        text.append(f"  · {session.model or 'model unknown'}", style=DIM)
        return text

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#resume-close")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#resume-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)


def _format_session_time(value: str) -> str:
    """把 UTC ISO 时间压成选择器里够用的本地时间。"""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "unknown time"


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
        self._kicker = Static(self._kicker_text(), classes="message-kicker")
        self._text = ""

    def compose(self) -> ComposeResult:
        yield self._kicker
        yield self.body

    def _kicker_text(self) -> str:
        return "AGENT  /  " + ("RESTORED RESPONSE" if self._restored else "LIVE RESPONSE")

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


class SettingsScreen(ModalScreen[bool]):
    """运行中的模型设置。保存成功后由 main.py 的回调重建客户端。"""

    CSS = f"""
    SettingsScreen {{
        align: center middle;
        background: rgba(0,0,0,0.82);
    }}

    #settings-dialog {{
        width: 72;
        max-width: 94%;
        height: 90%;
        background: {PANEL};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #settings-header {{
        height: 3;
        align: center middle;
    }}

    #settings-title {{
        width: 1fr;
        color: {SIGNAL};
        text-style: bold;
    }}

    #settings-close {{
        width: 5;
        min-width: 5;
        height: 3;
        border: none;
        background: transparent;
        color: {MUTED};
    }}

    #settings-body {{
        height: 1fr;
        padding-right: 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {PANEL};
        scrollbar-corner-color: {PANEL};
    }}

    .settings-label {{
        color: {SIGNAL};
        text-style: bold;
        margin-top: 1;
    }}

    .settings-hint {{
        color: {DIM};
        margin-bottom: 1;
    }}

    #settings-dialog Input, #settings-dialog Select {{
        height: 3;
        background: {PANEL_RAISED};
        border: solid {LINE};
        color: {PAPER};
    }}

    #settings-dialog SelectCurrent {{
        height: 3;
        background: {PANEL_RAISED};
        border: none;
        color: {PAPER};
    }}

    #settings-dialog SelectCurrent Static#label {{
        color: {PAPER};
    }}

    #settings-dialog SelectCurrent .arrow {{
        color: {SIGNAL};
    }}

    #settings-dialog Input:focus, #settings-dialog Select:focus {{
        border: solid {SIGNAL};
    }}

    #settings-status {{
        min-height: 1;
        color: {MUTED};
        margin-top: 1;
    }}

    #settings-actions {{
        height: 4;
        align: right middle;
        margin-top: 1;
    }}

    #settings-cancel, #settings-save {{
        width: 12;
        min-width: 12;
        height: 3;
        margin-left: 1;
    }}

    #settings-cancel {{
        border: solid {LINE};
        background: {PANEL_RAISED};
        color: {PAPER};
    }}

    #settings-save {{
        border: solid {SIGNAL};
        background: {SIGNAL};
        color: {INK};
        text-style: bold;
    }}
    """

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    def __init__(
        self,
        current: SettingsSnapshot,
        saver: Callable[[SettingsValues], SettingsSnapshot],
    ) -> None:
        super().__init__()
        self.current = current
        self.saver = saver

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            with Horizontal(id="settings-header"):
                yield Static("SETTINGS  /  MODEL RUNTIME", id="settings-title")
                yield Button("×", id="settings-close")
            with VerticalScroll(id="settings-body"):
                yield Static("PROVIDER", classes="settings-label")
                yield Select(
                    [("Anthropic", "anthropic"), ("OpenAI", "openai")],
                    allow_blank=False,
                    id="settings-provider",
                )

                yield Static("MODEL ID", classes="settings-label")
                yield Input(value=self.current.model_id, id="settings-model")

                yield Static("BASE URL", classes="settings-label")
                yield Input(value=self.current.base_url, id="settings-base-url")

                yield Static("MAX TOKENS", classes="settings-label")
                yield Input(
                    value=str(self.current.max_tokens),
                    type="integer",
                    id="settings-max-tokens",
                )

                yield Static("THINKING EFFORT", classes="settings-label")
                yield Select(
                    [
                        ("默认（不发送）", "omit"),
                        ("off", "off"),
                        ("low", "low"),
                        ("medium", "medium"),
                        ("high", "high"),
                        ("max", "max"),
                    ],
                    allow_blank=False,
                    id="settings-effort",
                )

                yield Static("API KEY", classes="settings-label")
                yield Static(
                    f"当前来源：{self.current.api_key_source}。留空保持原值。",
                    classes="settings-hint",
                )
                yield Input(
                    placeholder="输入新密钥，或写 ${ENV_VAR}",
                    password=True,
                    id="settings-api-key",
                )
            yield Static("", id="settings-status")
            with Horizontal(id="settings-actions"):
                yield Button("CANCEL", id="settings-cancel")
                yield Button("SAVE", id="settings-save")

    def on_mount(self) -> None:
        # Select 在构造时设值会早于 SelectCurrent 挂载，显示会是空白。
        self.query_one("#settings-provider", Select).value = self.current.provider
        self.query_one("#settings-effort", Select).value = self.current.effort or "omit"
        self.query_one("#settings-model", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#settings-close")
    @on(Button.Pressed, "#settings-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#settings-save")
    def _save(self) -> None:
        max_tokens_raw = self.query_one("#settings-max-tokens", Input).value.strip()
        if not max_tokens_raw.isdigit():
            self._show_error("max_tokens 必须是正整数。")
            return
        effort_value = str(self.query_one("#settings-effort", Select).value)
        effort = "" if effort_value == "omit" else effort_value
        api_key = self.query_one("#settings-api-key", Input).value.strip() or None
        values = SettingsValues(
            provider=str(self.query_one("#settings-provider", Select).value),
            base_url=self.query_one("#settings-base-url", Input).value.strip(),
            model_id=self.query_one("#settings-model", Input).value.strip(),
            max_tokens=int(max_tokens_raw),
            effort=effort,
            api_key=api_key,
        )
        save_button = self.query_one("#settings-save", Button)
        save_button.disabled = True
        try:
            self.saver(values)
        except Exception as exc:
            self._show_error(str(exc))
            save_button.disabled = False
            return
        self.dismiss(True)

    def _show_error(self, message: str) -> None:
        status = self.query_one("#settings-status", Static)
        status.update(message)
        status.styles.color = ERROR


class AgentLearnTUI(App[str | None]):
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
    }}

    #brand-mark {{
        color: {SIGNAL};
    }}

    #top-session {{
        width: 1fr;
        padding-left: 2;
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
        padding: 1 2;
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {INK};
        scrollbar-corner-color: {INK};
    }}

    #ledger {{
        width: 31;
        background: {PANEL};
        border-left: solid {LINE};
        padding: 1 1;
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
        padding: 2 0 1 0;
    }}

    .empty-boot {{
        color: {SIGNAL};
        text-style: bold;
        margin-bottom: 1;
    }}

    .empty-console {{
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding: 1 2;
        background: {PANEL};
        border-left: thick {SIGNAL};
        color: {PAPER};
    }}

    .empty-copy {{
        color: {MUTED};
        max-width: 78;
        margin-bottom: 1;
    }}

    .prompt-list {{
        height: auto;
        margin-bottom: 1;
    }}

    .prompt-chip {{
        width: 100%;
        min-width: 0;
        height: 3;
        margin: 0 0 1 0;
        padding: 0 1;
        border: solid {LINE};
        background: transparent;
        color: {PAPER};
        content-align: left middle;
        text-align: left;
    }}

    .prompt-chip:hover, .prompt-chip:focus {{
        border: solid {SIGNAL};
        background: {SIGNAL};
        color: {INK};
    }}

    .empty-meta {{
        color: {DIM};
        margin-top: 1;
    }}

    .user-message {{
        width: 100%;
        height: auto;
        margin: 0 0 2 0;
        padding: 1 2;
        background: {PANEL};
        border: solid {LINE};
        border-left: thick {SIGNAL};
        color: {PAPER};
    }}

    .assistant-message {{
        width: 100%;
        height: auto;
        margin: 0 0 2 0;
        padding-left: 1;
        border-left: solid {LINE};
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
        background: {INK};
        border: solid {LINE};
        border-left: thick {SIGNAL};
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
        border: solid {LINE};
        background: transparent;
    }}

    .reasoning.-live {{
        border: solid {WARN};
    }}

    .reasoning > CollapsibleTitle {{
        color: {DIM};
        background: transparent;
    }}

    .reasoning.-live > CollapsibleTitle {{
        color: {WARN};
    }}

    .reasoning.-done > CollapsibleTitle {{
        color: {MUTED};
    }}

    .reasoning Copy {{
        color: {MUTED};
        background: {INK};
        padding: 1 2;
        border-top: solid {LINE};
    }}

    .tool-card {{
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border-left: thick {TOOL};
        background: {PANEL};
        color: {MUTED};
    }}

    .command-result {{
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: solid {LINE};
        border-left: thick {SIGNAL};
        background: {PANEL};
    }}

    .command-palette {{
        width: 100%;
        height: auto;
        max-height: 18;
        margin: 0 0 1 0;
        padding: 0 1;
        border: solid {LINE};
        border-left: thick {SIGNAL};
        background: {PANEL};
        color: {MUTED};
    }}

    #composer-shell {{
        height: auto;
        min-height: 6;
        max-height: 30;
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
        background: {INK};
        border: solid {LINE};
        color: {PAPER};
        padding: 0 1;
    }}

    #composer:focus {{
        border: solid {SIGNAL};
    }}

    #send {{
        width: 13;
        min-width: 13;
        height: 3;
        margin-left: 1;
        border: solid {SIGNAL};
        background: {SIGNAL};
        color: {INK};
        text-style: bold;
    }}

    #send:hover, #send:focus {{
        background: {INK};
        border: solid {SIGNAL};
        color: {SIGNAL};
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
        Binding("ctrl+comma", "settings", "settings", show=False),
    ]

    def __init__(
        self,
        conversation: Conversation,
        history: list[DomainMessage],
        session_id: str,
        settings_loader: Callable[[], SettingsSnapshot] | None = None,
        settings_saver: Callable[[SettingsValues], SettingsSnapshot] | None = None,
        session_loader: Callable[[], list[SessionSummary]] | None = None,
    ) -> None:
        super().__init__()
        apply_agentlearn_theme(self)
        self.conversation = conversation
        self.history = history
        self.session_id = session_id
        self.settings_loader = settings_loader
        self.settings_saver = settings_saver
        self.session_loader = session_loader
        self.streaming = False
        self.turn_started = 0.0
        self.turn_count = sum(1 for message in history if message.role == "user" and any(
            block.type == "text" for block in message.content
        ))
        self.tool_count = sum(
            1
            for message in history
            for block in message.content
            if block.type == "tool_use"
        )
        self.stop_reason = "—"
        self._assistant: AssistantMessage | None = None
        self._reasoning: ReasoningPanel | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._suppress_palette: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                Text.assemble(
                    (" AGENTLEARN ", f"bold {INK} on {SIGNAL}"),
                    ("  LOCAL RUNTIME", DIM),
                ),
                id="brand",
            )
            yield Static(self._session_line(), id="top-session")
            yield Static("● READY", id="top-state")

        with Horizontal(id="workspace"):
            with VerticalScroll(id="timeline"):
                yield EmptyState(id="empty") if not self.history else Static(
                    "RESTORED THREAD", classes="message-kicker"
                )
            with Vertical(id="ledger"):
                yield Static("RUN LOG", classes="ledger-kicker")
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
                    "CTRL+,      /config\n"
                    "/           commands\n"
                    "CTRL+Q      quit",
                    id="key-help",
                )

        with Vertical(id="composer-shell"):
            yield CommandPalette()
            yield Static("COMPOSE  /  ENTER RUN", id="composer-label")
            with Horizontal(id="composer-row"):
                yield PromptComposer(
                    placeholder="输入任务或命令…",
                    id="composer",
                    soft_wrap=True,
                    compact=True,
                )
                yield Button("RUN  ↵", id="send")
            yield Static(
                "/resume  HISTORICAL SESSIONS  ·  /clear  NEW CONTEXT  ·  /  ALL COMMANDS",
                id="composer-help",
            )

    async def on_mount(self) -> None:
        if self.history:
            await self._mount_history()
        composer = self.query_one("#composer", PromptComposer)
        composer.command_key_handler = self._command_key
        composer.focus()
        self.set_interval(0.25, self._tick_elapsed)
        self._update_compact_layout(self.size.width)
        if self.history:
            # Markdown 在首帧之后才确定最终高度，等排版稳定再滚到底。
            self.set_timer(0.05, self._scroll_timeline_end)

    def on_resize(self, event: events.Resize) -> None:
        self._update_compact_layout(event.size.width)

    def _update_compact_layout(self, width: int) -> None:
        self.screen.set_class(width < 106, "-compact")

    def _session_line(self) -> str:
        count = len(self.history)
        return f"SESSION {self._short_session()}  ·  {count} MESSAGES"

    def _short_session(self) -> str:
        return self.session_id[:8]

    async def _mount_history(self) -> None:
        timeline = self.query_one("#timeline", VerticalScroll)
        by_tool_id: dict[str, ToolCard] = {}
        for message in self.history:
            if message.role == "user":
                for block in message.content:
                    if block.type == "text" and block.text:
                        await timeline.mount(UserMessage(block.text))
                    elif block.type == "tool_result":
                        card = by_tool_id.get(block.tool_use_id)
                        if card is None:
                            card = ToolCard(block.tool_use_id, "tool", {})
                            by_tool_id[block.tool_use_id] = card
                            await timeline.mount(card)
                        card.set_result(block.content, block.is_error, None)
                continue

            # JSONL 的 content 顺序就是模型实际生成顺序。恢复时逐块挂载，
            # 不再先攒 thinking、再攒正文、最后把工具卡统一追到末尾。
            for block in message.content:
                if block.type == "thinking" and block.thinking:
                    await timeline.mount(ReasoningPanel(block.thinking, restored=True))
                elif block.type == "text" and block.text:
                    assistant = AssistantMessage(restored=True)
                    await timeline.mount(assistant)
                    assistant.append_text(block.text)
                elif block.type == "tool_use":
                    card = ToolCard(block.id, block.name, block.input)
                    by_tool_id[block.id] = card
                    await timeline.mount(card)
        timeline.scroll_end(animate=False)

    def _scroll_timeline_end(self) -> None:
        """把续接会话定位到最后一轮，而不是停在历史开头。"""
        self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)

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
        self._assistant = None
        self._reasoning = None
        self._tool_cards = {}

        self.streaming = True
        self.turn_started = time.monotonic()
        self.turn_count += 1
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
            fragment = event["text"]
            if not fragment:
                return
            # thinking 如果出现在上一段正文之后，先收住正文，保证屏幕顺序
            # 和模型块顺序一致；连续 thinking 片段仍复用同一个 ticker。
            if self._assistant is not None:
                self._assistant.finish()
                self._assistant = None
            if self._reasoning is None:
                self._reasoning = ReasoningPanel()
                await self.query_one("#timeline", VerticalScroll).mount(self._reasoning)
            self._reasoning.append_text(fragment)
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "text":
            fragment = event["text"]
            if not fragment:
                return
            if self._reasoning is not None:
                self._reasoning.finish()
                self._reasoning = None
            if self._assistant is None:
                self._assistant = AssistantMessage()
                await self.query_one("#timeline", VerticalScroll).mount(self._assistant)
            self._assistant.append_text(fragment)
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "tool_use":
            self._finish_live_blocks()
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

    def _finish_live_blocks(self) -> None:
        if self._reasoning is not None:
            self._reasoning.finish()
            self._reasoning = None
        if self._assistant is not None:
            self._assistant.finish()
            self._assistant = None

    async def _finish_turn(self, stop_reason: str) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.stop_reason = stop_reason
        self._set_running_state("COMPLETE")
        self._update_ledger()
        self._send_button(disabled=False, label="RUN  ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _finish_error(self, message: str) -> None:
        self._finish_live_blocks()
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
        color = TOOL if state in ("READY", "COMPLETE") else (
            ERROR if state == "ERROR" else WARN
        )
        self.query_one("#top-state", Static).update(
            Text.assemble(("● ", color), (state, f"bold {color}"))
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

    def _command_key(self, event: events.Key) -> bool:
        palette = self.query_one(CommandPalette)
        if not palette.display:
            return False
        if event.key in ("up", "down"):
            palette.move(-1 if event.key == "up" else 1)
            return True
        if event.key == "tab":
            self._complete_command(palette.selected)
            return True
        if event.key == "escape":
            palette.display = False
            composer = self.query_one("#composer", PromptComposer)
            if composer.text.strip() == "/":
                composer.load_text("")
            composer.focus()
            return True
        return False

    def _complete_command(self, command: SlashCommand) -> None:
        value = f"/{command.name}"
        if command.takes_argument:
            value += " "
        composer = self.query_one("#composer", PromptComposer)
        self._suppress_palette = value
        composer.load_text(value)
        composer.move_cursor((0, len(value)))
        self.query_one(CommandPalette).display = False
        composer.focus()

    @on(TextArea.Changed, "#composer")
    def _composer_changed(self, event: TextArea.Changed) -> None:
        palette = self.query_one(CommandPalette)
        if self._suppress_palette == event.text_area.text:
            self._suppress_palette = None
            palette.display = False
            return
        palette.filter(event.text_area.text)

    @on(PromptComposer.Submitted)
    async def _composer_submitted(self, event: PromptComposer.Submitted) -> None:
        text = event.value
        if not text.startswith("/"):
            await self._submit(text)
            return

        palette = self.query_one(CommandPalette)
        parts = text[1:].split(maxsplit=1)
        command_name = parts[0].casefold() if parts else ""
        if palette.display and command_name not in COMMANDS_BY_NAME:
            self._complete_command(palette.selected)
            return

        composer = self.query_one("#composer", PromptComposer)
        composer.load_text("")
        palette.display = False
        await self._run_command(text)

    @on(EmptyState.Prompt)
    async def _empty_prompt(self, event: EmptyState.Prompt) -> None:
        await self._submit(event.value)

    @on(Button.Pressed, "#send")
    async def _send_pressed(self) -> None:
        await self._submit(self.query_one("#composer", PromptComposer).text)

    async def _run_command(self, text: str) -> None:
        head, _, argument = text[1:].partition(" ")
        name = head.casefold()
        argument = argument.strip()
        command = COMMANDS_BY_NAME.get(name)
        if command is None:
            await self._show_command_result(
                "UNKNOWN",
                f"没有 /{name or '?'} 这条命令。输入 / 查看全部命令。",
                error=True,
            )
            return
        if command.name == "config":
            self.action_settings()
            return
        if command.name == "model":
            await self._command_model(argument)
            return
        if command.name == "status":
            await self._show_command_result("STATUS", self._status_text())
            return
        if command.name == "help":
            composer = self.query_one("#composer", PromptComposer)
            composer.load_text("/")
            composer.move_cursor((0, 1))
            self.query_one(CommandPalette).filter("/")
            composer.focus()
            return
        if command.name == "quit":
            self.exit(None)
            return
        if self.streaming:
            self.notify("当前轮结束后再触发命令", severity="warning")
            return
        if command.name == "resume":
            await self._command_resume(argument)
            return
        if command.name in ("clear", "new"):
            self.exit(str(uuid.uuid4()))
            return
        prompts = {
            "now": "现在几点了？",
            "files": "看一下 sandbox 目录里有哪些文件。",
            "boundary": "先说明你准备怎么做，再执行，最后把结论和依据分开列出来。",
        }
        await self._submit(prompts[name])

    async def _command_resume(self, argument: str) -> None:
        if self.session_loader is None:
            await self._show_command_result(
                "RESUME",
                "会话索引没有装配，不能读取历史会话。",
                error=True,
            )
            return
        try:
            sessions = self.session_loader()
        except Exception as exc:
            await self._show_command_result("RESUME", str(exc), error=True)
            return
        if not sessions:
            await self._show_command_result(
                "RESUME",
                "还没有已落盘会话。先完成一轮对话，再回来续接。",
            )
            return

        if argument:
            query = argument.casefold()
            matches = [
                session
                for session in sessions
                if session.session_id.casefold().startswith(query)
            ]
            if len(matches) == 1:
                self._resume_session(matches[0].session_id)
                return
            if not matches:
                await self._show_command_result(
                    "RESUME",
                    f"没有会话 id 以 {argument} 开头。输入 /resume 打开选择器。",
                    error=True,
                )
                return
            await self._show_command_result(
                "RESUME",
                "匹配到多个会话，请多输入几位 id，或直接输入 /resume 选择。",
                error=True,
            )
            return

        self.push_screen(
            ResumeScreen(sessions, self.session_id),
            self._resume_closed,
        )

    def _resume_closed(self, session_id: str | None) -> None:
        if session_id is None:
            self.query_one("#composer", PromptComposer).focus()
            return
        self._resume_session(session_id)

    def _resume_session(self, session_id: str) -> None:
        if session_id == self.session_id:
            self.notify("已经是当前会话")
            self.query_one("#composer", PromptComposer).focus()
            return
        self.exit(session_id)

    def _status_text(self) -> str:
        state = (
            "STREAMING"
            if self.streaming
            else ("ERROR" if self.stop_reason == "error" else "READY")
        )
        return (
            f"session   {self.session_id}\n"
            f"model     {self.conversation.chat.model}\n"
            f"state     {state}\n"
            f"turns     {self.turn_count}\n"
            f"tools     {self.tool_count}\n"
            f"stop      {self.stop_reason}\n"
            "storage   JSONL transcript + SQLite trace"
        )

    async def _command_model(self, model_id: str) -> None:
        if not model_id:
            self.action_settings()
            return
        if self.streaming:
            self.notify("当前轮结束后再改设置", severity="warning")
            return
        if self.settings_loader is None or self.settings_saver is None:
            await self._show_command_result(
                "MODEL", "设置回调没有装配，不能直接切换模型。", error=True
            )
            return
        try:
            current = self.settings_loader()
            updated = self.settings_saver(
                SettingsValues(
                    provider=current.provider,
                    base_url=current.base_url,
                    model_id=model_id,
                    max_tokens=current.max_tokens,
                    effort=current.effort,
                    api_key=None,
                )
            )
        except Exception as exc:
            await self._show_command_result("MODEL", str(exc), error=True)
            return
        self.query_one("#ledger-model", Static).update(updated.model_id)
        await self._show_command_result(
            "MODEL",
            f"{updated.model_id}\n下一轮模型请求生效。",
        )

    async def _show_command_result(
        self, title: str, body: str, *, error: bool = False
    ) -> None:
        empty = self.query("#empty")
        if empty:
            await empty.remove()
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(CommandResult(title, body, error=error))
        timeline.scroll_end(animate=False)

    def action_settings(self) -> None:
        if self.streaming:
            self.notify("当前轮结束后再改设置", severity="warning")
            return
        if self.settings_loader is None or self.settings_saver is None:
            self.notify("设置回调没有装配", severity="error")
            return
        try:
            current = self.settings_loader()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.push_screen(
            SettingsScreen(current, self.settings_saver),
            self._settings_closed,
        )

    def _settings_closed(self, saved: bool | None) -> None:
        if saved:
            self.notify("设置已保存，下一轮生效")
        self.query_one("#composer", PromptComposer).focus()

    def action_focus_composer(self) -> None:
        self.query_one("#composer", PromptComposer).focus()


def run_chat(
    conversation: Conversation,
    history: list[DomainMessage],
    session_id: str | None = None,
    settings_loader: Callable[[], SettingsSnapshot] | None = None,
    settings_saver: Callable[[SettingsValues], SettingsSnapshot] | None = None,
    session_loader: Callable[[], list[SessionSummary]] | None = None,
) -> str | None:
    """启动对话 TUI；返回要切换到的下一个 session_id，None 表示退出。"""
    session_id = session_id or str(uuid.uuid4())
    return AgentLearnTUI(
        conversation,
        history,
        session_id,
        settings_loader,
        settings_saver,
        session_loader,
    ).run()
