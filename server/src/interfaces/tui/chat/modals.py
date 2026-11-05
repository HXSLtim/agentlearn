"""各弹层：输入历史、会话恢复、审批、计划确认、会话内查找与运行设置。"""

from __future__ import annotations

import json

from dataclasses import dataclass

from datetime import datetime

from typing import Callable

from rich.text import Text

from textual import on

from textual.app import ComposeResult

from textual.binding import Binding

from textual.containers import Horizontal, Vertical, VerticalScroll

from textual.screen import ModalScreen

from textual.worker import WorkerFailed

from textual.widgets import (
    Button,
    Input,
    OptionList,
    Select,
    Static,
)

from textual.widgets.option_list import Option

from infrastructure.config import SettingsSnapshot, SettingsValues

from ..theme import (
    DIM,
    ERROR,
    INK,
    LINE,
    MUTED,
    PANEL,
    PANEL_RAISED,
    PAPER,
    SIGNAL,
    WARN,
)

from .cards import (
    UserMessage,
    AssistantMessage,
)


@dataclass(frozen=True)
class SessionSummary:
    """会话选择器需要的一行；由组合根从 Store + Transcript 映射出来。"""

    session_id: str
    title: str
    model: str
    created_at: str
    turns: int


class HistorySearchScreen(ModalScreen[str | None]):
    """按内容过滤本会话输入历史，选中后只回填输入台。"""

    CSS = f"""
    HistorySearchScreen {{
        align: center middle;
        background: rgba(0,0,0,0.84);
    }}

    #history-search-dialog {{
        width: 82;
        max-width: 94%;
        height: 68%;
        background: {PANEL};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #history-search-title {{
        height: 2;
        color: {SIGNAL};
        text-style: bold;
    }}

    #history-search-input {{
        height: 3;
        margin-bottom: 1;
        background: {INK};
        border: solid {LINE};
        color: {PAPER};
    }}

    #history-search-input:focus {{
        border: solid {SIGNAL};
    }}

    #history-search-list {{
        height: 1fr;
        background: {INK};
        border: solid {LINE};
    }}

    #history-search-list > .option-list--option {{
        padding: 1;
        border-bottom: solid {LINE};
        color: {MUTED};
    }}

    #history-search-list > .option-list--option-highlighted {{
        color: {INK};
        background: {SIGNAL};
        text-style: bold;
    }}

    #history-search-footer {{
        height: 2;
        padding-top: 1;
        color: {DIM};
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", show=False),
        Binding("up", "previous", "previous", show=False),
        Binding("down", "next", "next", show=False),
    ]

    def __init__(self, history: list[str]) -> None:
        super().__init__()
        self.history = list(reversed(history))
        self.matches = list(self.history)

    def compose(self) -> ComposeResult:
        with Vertical(id="history-search-dialog"):
            yield Static("HISTORY  /  REVERSE SEARCH", id="history-search-title")
            yield Input(
                placeholder="输入内容过滤本会话历史…",
                id="history-search-input",
            )
            yield OptionList(id="history-search-list")
            yield Static(
                "↑↓ SELECT · ENTER RESTORE · ESC CLOSE",
                id="history-search-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#history-search-input", Input).focus()
        self._refresh("")

    def _refresh(self, query: str) -> None:
        normalized = query.strip().casefold()
        self.matches = [
            item for item in self.history if not normalized or normalized in item.casefold()
        ]
        option_list = self.query_one("#history-search-list", OptionList)
        option_list.clear_options()
        if not self.matches:
            option_list.add_option(Option(Text("没有匹配的历史输入", style=DIM)))
            return
        option_list.add_options(
            Option(
                Text.assemble(
                    (f"{index + 1:02d}  ", f"bold {SIGNAL}"),
                    (" ".join(item.split())[:160], PAPER),
                ),
                id=str(index),
            )
            for index, item in enumerate(self.matches)
        )
        option_list.highlighted = 0

    @on(Input.Changed, "#history-search-input")
    def _changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    @on(Input.Submitted, "#history-search-input")
    def _submitted(self) -> None:
        option_list = self.query_one("#history-search-list", OptionList)
        if not self.matches:
            return
        index = option_list.highlighted
        if index is None:
            index = 0
        self.dismiss(self.matches[index])

    @on(OptionList.OptionSelected, "#history-search-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        index = int(event.option_id)
        if 0 <= index < len(self.matches):
            self.dismiss(self.matches[index])

    def action_previous(self) -> None:
        self._move_highlight(-1)

    def action_next(self) -> None:
        self._move_highlight(1)

    def _move_highlight(self, delta: int) -> None:
        if not self.matches:
            return
        option_list = self.query_one("#history-search-list", OptionList)
        current = option_list.highlighted or 0
        option_list.highlighted = (
            current + delta + len(self.matches)
        ) % len(self.matches)
        option_list.scroll_to_highlight()

    def action_cancel(self) -> None:
        self.dismiss(None)


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

    #resume-search {{
        height: 3;
        margin-bottom: 1;
        background: {INK};
        border: solid {LINE};
        color: {PAPER};
    }}

    #resume-search:focus {{
        border: solid {SIGNAL};
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
        self.matches = list(sessions)

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
            yield Input(
                placeholder="按 id / 标题 / 模型过滤…",
                id="resume-search",
            )
            yield OptionList(id="resume-list")
            yield Static(
                "TYPE FILTER  ·  ↑↓ SELECT  ·  ENTER RESUME  ·  ESC CLOSE",
                id="resume-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#resume-search", Input).focus()
        self._refresh("")

    def _refresh(self, query: str) -> None:
        normalized = query.strip().casefold()
        self.matches = [
            session
            for session in self.sessions
            if not normalized
            or normalized in session.session_id.casefold()
            or normalized in session.title.casefold()
            or normalized in session.model.casefold()
        ]
        option_list = self.query_one("#resume-list", OptionList)
        option_list.clear_options()
        if not self.matches:
            option_list.add_option(Option(Text("没有匹配的会话", style=DIM)))
            return
        option_list.add_options(
            Option(self._option_text(session), id=session.session_id)
            for session in self.matches
        )
        for index, session in enumerate(self.matches):
            if session.session_id == self.current_session_id:
                option_list.highlighted = index
                break
        else:
            option_list.highlighted = 0
        option_list.scroll_to_highlight()

    @on(Input.Changed, "#resume-search")
    def _search_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    @on(Input.Submitted, "#resume-search")
    def _search_submitted(self) -> None:
        option_list = self.query_one("#resume-list", OptionList)
        if self.matches and option_list.highlighted is not None:
            self.dismiss(self.matches[option_list.highlighted].session_id)

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


@dataclass(frozen=True)
class ApprovalChoice:
    approved: bool
    scope: str = "once"
    reason: str = ""


class ApprovalScreen(ModalScreen[ApprovalChoice]):
    """执行前审批。Conversation 在工作线程里等这个决定。"""

    CSS = f"""
    ApprovalScreen {{
        align: center middle;
        background: rgba(0,0,0,0.86);
    }}

    #approval-dialog {{
        width: 78;
        max-width: 94%;
        height: auto;
        max-height: 94%;
        background: {PANEL};
        border: thick {WARN};
        padding: 1 2;
    }}

    #approval-header {{
        height: 3;
        align: center middle;
    }}

    #approval-title {{
        width: 1fr;
        color: {WARN};
        text-style: bold;
    }}

    #approval-mode {{
        width: auto;
        color: {MUTED};
    }}

    #approval-copy {{
        height: auto;
        margin-bottom: 1;
        color: {MUTED};
    }}

    #approval-arguments {{
        height: auto;
        max-height: 6;
        margin-bottom: 1;
        padding: 1 1;
        color: {PAPER};
        background: {INK};
        border: solid {LINE};
    }}

    #approval-reason {{
        margin-bottom: 1;
    }}

    #approval-actions {{
        height: 3;
        align: right middle;
    }}

    #approval-deny, #approval-accept, #approval-accept-session {{
        width: 20;
        min-width: 20;
        height: 3;
        margin-left: 1;
    }}

    #approval-deny {{
        border: solid {LINE};
        background: {PANEL_RAISED};
        color: {PAPER};
    }}

    #approval-accept {{
        border: solid {SIGNAL};
        background: {SIGNAL};
        color: {INK};
        text-style: bold;
    }}

    #approval-accept-session {{
        border: solid {SIGNAL};
        background: {PANEL_RAISED};
        color: {SIGNAL};
        text-style: bold;
    }}
    """

    BINDINGS = [Binding("escape", "deny", "deny", show=False)]

    def __init__(self, tool_name: str, arguments: dict, mode: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.mode = mode

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            with Horizontal(id="approval-header"):
                yield Static("TOOL APPROVAL  /  EXECUTION GATE", id="approval-title")
                yield Static(f"MODE {self.mode.upper()}", id="approval-mode")
            yield Static(
                f"{self.tool_name} 请求在宿主机执行。ACCEPT ONCE 只放行这一次；"
                "ACCEPT SESSION 本会话后续同名工具免问。",
                id="approval-copy",
            )
            yield Static(
                json.dumps(self.arguments, ensure_ascii=False, indent=2),
                id="approval-arguments",
            )
            yield Input(
                placeholder="DENIAL REASON (OPTIONAL)",
                id="approval-reason",
            )
            with Horizontal(id="approval-actions"):
                yield Button("DENY", id="approval-deny")
                yield Button("ACCEPT ONCE", id="approval-accept")
                yield Button("ACCEPT SESSION", id="approval-accept-session")

    def on_mount(self) -> None:
        self.query_one("#approval-accept", Button).focus()

    def action_deny(self) -> None:
        self.dismiss(self._deny_choice())

    @on(Button.Pressed, "#approval-deny")
    def _deny(self) -> None:
        self.dismiss(self._deny_choice())

    @on(Button.Pressed, "#approval-accept")
    def _accept(self) -> None:
        self.dismiss(ApprovalChoice(True))

    @on(Button.Pressed, "#approval-accept-session")
    def _accept_session(self) -> None:
        self.dismiss(ApprovalChoice(True, "session"))

    def _deny_choice(self) -> ApprovalChoice:
        reason = self.query_one("#approval-reason", Input).value.strip()
        return ApprovalChoice(False, reason=reason)


class PlanScreen(ModalScreen[bool]):
    """展示 plan mode 的产物；确认是一次显式人机动作。"""

    CSS = f"""
    PlanScreen {{
        align: center middle;
    }}

    #plan-dialog {{
        width: 82;
        max-width: 96%;
        height: auto;
        max-height: 94%;
        background: {PANEL};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #plan-title {{
        margin-bottom: 1;
        color: {SIGNAL};
        text-style: bold;
    }}

    #plan-text {{
        height: auto;
        max-height: 16;
        margin-bottom: 1;
        padding: 1 1;
        color: {PAPER};
        background: {INK};
        border: solid {LINE};
    }}

    #plan-actions {{
        height: 3;
        align: right middle;
    }}

    #plan-discard, #plan-confirm {{
        width: 24;
        min-width: 24;
        height: 3;
        margin-left: 1;
    }}

    #plan-discard {{
        border: solid {LINE};
        background: transparent;
        color: {PAPER};
    }}

    #plan-confirm {{
        border: solid {SIGNAL};
        background: {PANEL_RAISED};
        color: {SIGNAL};
        text-style: bold;
    }}
    """

    BINDINGS = [Binding("escape", "discard", "discard", show=False)]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="plan-dialog"):
            yield Static("PLAN MODE  /  HUMAN CONFIRMATION", id="plan-title")
            yield Static(self.text, id="plan-text")
            with Horizontal(id="plan-actions"):
                yield Button("DISCARD PLAN", id="plan-discard")
                yield Button("CONFIRM & EXECUTE", id="plan-confirm")

    def on_mount(self) -> None:
        self.query_one("#plan-confirm", Button).focus()

    def action_discard(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#plan-discard")
    def _discard(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#plan-confirm")
    def _confirm(self) -> None:
        self.dismiss(True)


def _format_session_time(value: str) -> str:
    """把 UTC ISO 时间压成选择器里够用的本地时间。"""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "unknown time"


class ConversationSearchScreen(ModalScreen[UserMessage | AssistantMessage | None]):
    """在当前时间线里定位已经渲染出来的用户/回答卡片。"""

    CSS = f"""
    ConversationSearchScreen {{
        align: center middle;
        background: rgba(0,0,0,0.86);
    }}

    #conversation-search-dialog {{
        width: 84;
        max-width: 94%;
        height: 68%;
        background: {PANEL};
        border: thick {WARN};
        padding: 1 2;
    }}

    #conversation-search-title {{
        height: 2;
        color: {WARN};
        text-style: bold;
    }}

    #conversation-search-input {{
        height: 3;
        margin-bottom: 1;
        background: {INK};
        border: solid {LINE};
        color: {PAPER};
    }}

    #conversation-search-input:focus {{
        border: solid {WARN};
    }}

    #conversation-search-list {{
        height: 1fr;
        background: {INK};
        border: solid {LINE};
    }}

    #conversation-search-list > .option-list--option {{
        padding: 1;
        border-bottom: solid {LINE};
        color: {MUTED};
    }}

    #conversation-search-list > .option-list--option-highlighted {{
        color: {INK};
        background: {WARN};
        text-style: bold;
    }}

    #conversation-search-footer {{
        height: 2;
        padding-top: 1;
        color: {DIM};
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", show=False),
        Binding("up", "previous", "previous", show=False),
        Binding("down", "next", "next", show=False),
    ]

    def __init__(
        self,
        candidates: list[tuple[UserMessage | AssistantMessage, str]],
        initial_query: str = "",
    ) -> None:
        super().__init__()
        self.candidates = list(candidates)
        self.matches = list(self.candidates)
        self.initial_query = initial_query

    def compose(self) -> ComposeResult:
        with Vertical(id="conversation-search-dialog"):
            yield Static("SESSION  /  RENDERED MESSAGE SEARCH", id="conversation-search-title")
            yield Input(
                placeholder="输入内容过滤当前会话…",
                id="conversation-search-input",
            )
            yield OptionList(id="conversation-search-list")
            yield Static(
                "↑↓ SELECT · ENTER LOCATE · ESC CLOSE",
                id="conversation-search-footer",
            )

    def on_mount(self) -> None:
        search_input = self.query_one("#conversation-search-input", Input)
        search_input.value = self.initial_query
        search_input.focus()
        self._refresh(self.initial_query)

    def _snippet(self, text: str, query: str) -> str:
        compact = " ".join(text.split())
        normalized = query.strip().casefold()
        position = compact.casefold().find(normalized) if normalized else 0
        start = max(0, position - 30)
        return compact[start : start + 160] or "(empty message)"

    def _refresh(self, query: str) -> None:
        normalized = query.strip().casefold()
        self.matches = [
            candidate
            for candidate in self.candidates
            if not normalized or normalized in candidate[1].casefold()
        ]
        option_list = self.query_one("#conversation-search-list", OptionList)
        option_list.clear_options()
        if not self.matches:
            option_list.add_option(Option(Text("没有匹配的已渲染消息", style=DIM)))
            return
        option_list.add_options(
            Option(
                Text.assemble(
                    (f"{index + 1:02d}  ", f"bold {WARN}"),
                    (self._snippet(text, query), PAPER),
                ),
                id=str(index),
            )
            for index, (_, text) in enumerate(self.matches)
        )
        option_list.highlighted = 0

    @on(Input.Changed, "#conversation-search-input")
    def _changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    @on(Input.Submitted, "#conversation-search-input")
    def _submitted(self) -> None:
        option_list = self.query_one("#conversation-search-list", OptionList)
        if not self.matches:
            return
        index = option_list.highlighted or 0
        self.dismiss(self.matches[index][0])

    @on(OptionList.OptionSelected, "#conversation-search-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        index = int(event.option_id)
        if 0 <= index < len(self.matches):
            self.dismiss(self.matches[index][0])

    def action_previous(self) -> None:
        self._move_highlight(-1)

    def action_next(self) -> None:
        self._move_highlight(1)

    def _move_highlight(self, delta: int) -> None:
        if not self.matches:
            return
        option_list = self.query_one("#conversation-search-list", OptionList)
        current = option_list.highlighted or 0
        option_list.highlighted = (
            current + delta + len(self.matches)
        ) % len(self.matches)
        option_list.scroll_to_highlight()

    def action_cancel(self) -> None:
        self.dismiss(None)


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
        model_loader: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.current = current
        self.saver = saver
        self.model_loader = model_loader

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
                yield Select(
                    [(self.current.model_id, self.current.model_id)],
                    allow_blank=False,
                    id="settings-model",
                )
                yield Static(
                    "READING MODELS…",
                    id="settings-model-status",
                    classes="settings-hint",
                )

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

    async def on_mount(self) -> None:
        # Select 在构造时设值会早于 SelectCurrent 挂载，显示会是空白。
        self.query_one("#settings-provider", Select).value = self.current.provider
        self.query_one("#settings-effort", Select).value = self.current.effort or "omit"
        model_select = self.query_one("#settings-model", Select)
        model_select.value = self.current.model_id
        model_select.focus()
        if self.model_loader is None:
            self._model_status("模型列表回调没有装配，仅保留当前模型。", ERROR)
            return
        model_select.disabled = True
        try:
            worker = self.run_worker(
                self.model_loader,
                thread=True,
                group="settings-models",
                exit_on_error=False,
            )
            models = await worker.wait()
        except WorkerFailed as exc:
            reason = str(worker.error or exc)
            self._model_status(f"模型列表不可用：{reason}", ERROR)
            return
        except Exception as exc:
            self._model_status(f"模型列表不可用：{exc}", ERROR)
            return
        self._render_models(models)

    def _render_models(self, models: list[str]) -> None:
        model_select = self.query_one("#settings-model", Select)
        selected = str(model_select.value or self.current.model_id)
        options = list(models)
        if selected not in options:
            options.insert(0, selected)
        model_select.set_options([(model_id, model_id) for model_id in options])
        model_select.value = selected
        model_select.disabled = False
        self._model_status(
            f"模型列表 {len(models)} 个可选项。"
            if models
            else "接口没有返回模型，仅保留当前选择。",
            MUTED,
        )

    def _model_status(self, message: str, color: str) -> None:
        status = self.query_one("#settings-model-status", Static)
        status.update(message)
        status.styles.color = color

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
            model_id=str(self.query_one("#settings-model", Select).value),
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
