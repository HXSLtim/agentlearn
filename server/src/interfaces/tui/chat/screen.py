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

import base64
import os
import shlex
import subprocess
import tempfile
import time
import uuid

from collections import deque

from decimal import Decimal

from pathlib import Path

from typing import Callable

from rich.text import Text

from textual import events, on, work

from textual.app import App, ComposeResult

from textual.binding import Binding

from textual.containers import Horizontal, Vertical, VerticalScroll

from textual.widgets import (
    Button,
    Markdown,
    Static,
    TextArea,
)

from application.approval import APPROVAL_MODES, ApprovalBroker

from application.conversation import Conversation, TurnCancelled

from application.plan import PlanBroker

from application.tools import append_memory, read_memory

from application.transcriptExport import transcript_to_markdown

from application.workspace import changed_files

from infrastructure.config import SettingsSnapshot, SettingsValues

from application.sessionManager import MODES as STEERING_MODES, RunningTurn

from domain.message import ImageBlock

from domain.message import Message as DomainMessage

from ..trace import answer_texts
from application.traceView import span_payloads
from application.sandbox import SANDBOX, normalize_workdir

from ..theme import (
    DIM,
    ERROR,
    INK,
    LINE,
    MUTED,
    PANEL,
    PAPER,
    SIGNAL,
    TOOL,
    WARN,
    apply_agentlearn_theme,
)

from .labels import (
    approval_label,
    receipt_label,
    run_state_label,
    steering_label,
    stop_reason_label,
)
from ..formatting import cache_hit_percent, format_duration
from .cards import (
    UserMessage,
    AssistantMessage,
    ReasoningPanel,
    ToolCard,
    RetryErrorCard,
    EmptyState,
    _user_text,
)

from .composer import (
    ATTACH_MEDIA_TYPES,
    PromptComposer,
    QueuedPrompt,
)

from .palette import (
    SlashCommand,
    COMMANDS_BY_NAME,
    EFFORT_CYCLE,
    CommandPalette,
    CommandResult,
)

from .modals import (
    SessionSummary,
    HistorySearchScreen,
    ResumeScreen,
    ApprovalChoice,
    ApprovalScreen,
    PlanScreen,
    ConversationSearchScreen,
    SettingsScreen,
)

from .panels import (
    TraceScreen,
    WorkspaceDiffScreen,
)


TRANSCRIPT_MODES = ("normal", "thinking", "verbose")


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
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {PANEL};
    }}

    .ledger-kicker {{
        color: {SIGNAL};
        text-style: bold;
        margin-bottom: 1;
    }}

    .ledger-rule {{
        height: 1;
        border-top: solid {LINE};
        /* 上下 margin 各 1 会把每条分隔线撑到 3 行；36 行屏的侧栏放不下，只留下边距。 */
        margin: 0 0 1 0;
    }}

    .ledger-row {{
        height: 1;
    }}

    .ledger-label {{
        width: 11;
        color: {DIM};
    }}

    .ledger-value {{
        width: 1fr;
        height: 1;
        color: {PAPER};
        text-align: right;
        overflow: hidden;
    }}

    #ledger-footer {{
        color: {DIM};
    }}

    #ledger-state {{
        color: {TOOL};
        text-style: bold;
    }}

    #ledger-session {{
        color: {MUTED};
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

    /* 签名元素 span-tick 的终端版：消息底部一条 12 格耗时刻度。
       默认不占行（没数据的轮不画），set_tick 挂上 -on 才显示。 */
    .span-tick {{
        display: none;
        height: 1;
        margin-top: 1;
    }}

    .span-tick.-on {{
        display: block;
    }}

    .user-message.search-highlight,
    .assistant-message.search-highlight {{
        border: round {WARN};
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

    .retry-card {{
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: solid {ERROR};
        border-left: thick {ERROR};
        background: {PANEL};
        color: {PAPER};
    }}

    .retry-button {{
        width: 15;
        height: 3;
        margin-top: 1;
        border: solid {ERROR};
        background: transparent;
        color: {ERROR};
    }}

    .retry-button:hover, .retry-button:focus {{
        border: solid {ERROR};
        background: {ERROR};
        color: {INK};
    }}

    .retry-message {{
        height: auto;
        min-height: 1;
        color: {PAPER};
    }}

    .retry-note {{
        height: 1;
        margin-top: 1;
        color: {DIM};
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

    #composer-heading {{
        height: 1;
    }}

    #composer-status {{
        width: 1fr;
        color: {SIGNAL};
        text-style: bold;
    }}

    #composer-workdir {{
        width: auto;
        color: {TOOL};
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

    Screen.transcript-normal .reasoning,
    Screen.transcript-normal .tool-card {{
        display: none;
    }}

    Screen.transcript-thinking .tool-card {{
        display: none;
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
        Binding(
            "ctrl+r",
            "history_search",
            "input history",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+g",
            "edit_composer",
            "external editor",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+o",
            "copy_answer",
            "copy answer",
            show=False,
            priority=True,
        ),
        Binding("ctrl+comma", "settings", "settings", show=False),
        Binding("alt+t", "cycle_effort", "thinking effort", show=False, priority=True),
        Binding(
            "shift+tab",
            "cycle_approval",
            "approval mode",
            show=False,
            priority=True,
        ),
        Binding(
            "alt+s",
            "cycle_steering",
            "steering mode",
            show=False,
            priority=True,
        ),
    ]

    def __init__(
        self,
        conversation: Conversation,
        history: list[DomainMessage],
        session_id: str,
        settings_loader: Callable[[], SettingsSnapshot] | None = None,
        settings_saver: Callable[[SettingsValues], SettingsSnapshot] | None = None,
        session_loader: Callable[[], list[SessionSummary]] | None = None,
        approval: ApprovalBroker | None = None,
        plan_broker: PlanBroker | None = None,
        capability_loader: Callable[[], dict] | None = None,
        model_loader: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__()
        apply_agentlearn_theme(self)
        self.conversation = conversation
        self.history = history
        self.session_id = session_id
        self.settings_loader = settings_loader
        self.settings_saver = settings_saver
        self.session_loader = session_loader
        self.approval = approval
        self.plan_broker = plan_broker
        self.capability_loader = capability_loader
        self.model_loader = model_loader
        self.approval_mode = getattr(conversation, "approval_mode", "approve")
        self.steering_mode = "queue"
        self.steering_turn: RunningTurn | None = None
        self.queued_prompts: deque[QueuedPrompt] = deque()
        self.pending_images: list[ImageBlock] = []
        self.input_history: list[str] = [
            text
            for message in history
            if message.role == "user"
            if (text := _user_text(message))
        ]
        self._history_index: int | None = None
        self._history_draft = ""
        self._pending_approval_id: str | None = None
        self._confirmed_plan_id: str | None = None
        self._last_failed_question: str | None = None
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
        self.turn_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cache_hit_ratio": 0,
        }
        self.effort = "—"
        self.pricing: dict[str, str] | None = None
        if self.settings_loader is not None:
            try:
                snapshot = self.settings_loader()
                self.effort = self._effort_label(snapshot.effort)
                self.pricing = snapshot.pricing
            except Exception:
                pass
        self._assistant: AssistantMessage | None = None
        self._reasoning: ReasoningPanel | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._suppress_palette: str | None = None
        self.transcript_mode = "normal"
        # 抬头规则（WebUI showAuthorFor 同款）：一条用户消息之后的第
        # 一条助手消息带「智能体」抬头，轮内后续的连续助手消息不带。
        self._expect_kicker = True
        # 本轮挂出来的助手消息（stamp tick 用最后一条）；tick 登记簿存
        # 全会话 (消息, 时长)，最长轮变化时整批按新基准重画。
        self._turn_assistants: list[AssistantMessage] = []
        self._tick_registry: list[tuple[AssistantMessage, float]] = []
        self._max_turn_ms = 0.0

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                Text.assemble(
                    (" AGENTLEARN ", f"bold {INK} on {SIGNAL}"),
                    ("  本地运行时", DIM),
                ),
                id="brand",
            )
            yield Static(self._session_line(), id="top-session")
            yield Static("● 就绪", id="top-state")

        with Horizontal(id="workspace"):
            with VerticalScroll(id="timeline"):
                yield EmptyState(id="empty") if not self.history else Static(
                    "历史会话", classes="message-kicker"
                )
            # VerticalScroll：内容是 22 行起步的固定清单，比这矮的终端
            # （比如 30 行的分屏）靠滚轮也能看全，不硬裁。
            with VerticalScroll(id="ledger"):
                yield Static("运行日志", classes="ledger-kicker")
                # 台账与 WebUI 右栏（index.html 的 run-metrics）同一套指标和行序。
                # 「标签上、值下」的两行制在 36 行的终端里放不下 13 行指标，
                # 这里压成一行两列：标签左、值右，一格一行，信息密度对齐。
                with Horizontal(classes="ledger-row"):
                    yield Static("状态", classes="ledger-label")
                    yield Static("就绪", id="ledger-state", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("会话", classes="ledger-label")
                    yield Static(self._short_session(), id="ledger-session", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("轮次", classes="ledger-label")
                    yield Static(str(self.turn_count), id="ledger-turns", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("工具调用", classes="ledger-label")
                    yield Static(str(self.tool_count), id="ledger-tools", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("用时", classes="ledger-label")
                    yield Static("—", id="ledger-elapsed", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("输入 token", classes="ledger-label")
                    yield Static("0", id="ledger-input", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("输出 token", classes="ledger-label")
                    yield Static("0", id="ledger-output", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("缓存读", classes="ledger-label")
                    yield Static("0", id="ledger-cache-read", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("缓存写", classes="ledger-label")
                    yield Static("0", id="ledger-cache-write", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("缓存命中", classes="ledger-label")
                    yield Static("—", id="ledger-cache-hit", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("成本", classes="ledger-label")
                    yield Static(
                        self._format_cost(),
                        id="ledger-cost",
                        classes="ledger-value",
                    )
                with Horizontal(classes="ledger-row"):
                    yield Static("思考强度", classes="ledger-label")
                    yield Static(self.effort, id="ledger-effort", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("执行审批", classes="ledger-label")
                    yield Static(
                        approval_label(self.approval_mode),
                        id="ledger-approval",
                        classes="ledger-value",
                    )
                with Horizontal(classes="ledger-row"):
                    yield Static("消息插话", classes="ledger-label")
                    yield Static(
                        steering_label(self.steering_mode),
                        id="ledger-steering",
                        classes="ledger-value",
                    )
                # 下面两行是 TUI 补充的实时位：模型名和排队中的插话数。
                # WebUI 不在右栏放这两项（模型在设置里，插话有自己的浮层）。
                with Horizontal(classes="ledger-row"):
                    yield Static("模型", classes="ledger-label")
                    yield Static(self.conversation.chat.model, id="ledger-model", classes="ledger-value")
                with Horizontal(classes="ledger-row"):
                    yield Static("排队中", classes="ledger-label")
                    yield Static("0", id="ledger-queued", classes="ledger-value")
                yield Static("", classes="ledger-rule")
                with Horizontal(classes="ledger-row"):
                    yield Static("停止原因", classes="ledger-label")
                    yield Static("—", id="ledger-stop", classes="ledger-value")
                yield Static("JSONL · SQLite · 仅本地", id="ledger-footer")

        with Vertical(id="composer-shell"):
            yield CommandPalette()
            with Horizontal(id="composer-heading"):
                yield Static(
                    "Enter 发送 · Shift+Enter 换行 · Esc 停止本轮",
                    id="composer-status",
                )
                yield Static("", id="composer-workdir")
            with Horizontal(id="composer-row"):
                yield PromptComposer(
                    placeholder="输入任务或命令…",
                    id="composer",
                    soft_wrap=True,
                    compact=True,
                )
                yield Button("发送 ↵", id="send")
            yield Static(
                "CTRL+R 历史 · CTRL+G 编辑器 · CTRL+O 复制回答 · CTRL+, 设置"
                " · ALT+T 思考 · SHIFT+TAB 审批 · ALT+S 插话 · / 命令面板",
                id="composer-help",
            )

    async def on_mount(self) -> None:
        if self.history:
            await self._mount_history()
        self._refresh_workdir_ui()
        composer = self.query_one("#composer", PromptComposer)
        composer.command_key_handler = self._command_key
        composer.history_key_handler = self._history_key
        composer.escape_handler = self._escape_key
        composer.focus()
        self.set_interval(0.25, self._tick_elapsed)
        self._update_compact_layout(self.size.width)
        self._set_transcript_mode("normal", show_receipt=False)
        if self.history:
            # Markdown 在首帧之后才确定最终高度，等排版稳定再滚到底。
            self.set_timer(0.05, self._scroll_timeline_end)

    def on_resize(self, event: events.Resize) -> None:
        self._update_compact_layout(event.size.width)

    def _update_compact_layout(self, width: int) -> None:
        self.screen.set_class(width < 106, "-compact")

    def _set_transcript_mode(self, mode: str, *, show_receipt: bool = True) -> None:
        if mode not in TRANSCRIPT_MODES:
            return
        self.transcript_mode = mode
        for candidate in TRANSCRIPT_MODES:
            self.screen.remove_class(f"transcript-{candidate}")
        self.screen.add_class(f"transcript-{mode}")
        for reasoning in self.query(ReasoningPanel):
            reasoning.collapsed = mode == "normal"
        if show_receipt:
            descriptions = {
                "normal": "只看用户消息、回答和审批；隐藏思考块与工具明细。",
                "thinking": "在 normal 基础上显示思考块；工具仍只保留审批。",
                "verbose": "显示思考块、工具入参和工具出参。",
            }
            self.notify(f"Transcript：{mode.upper()} · {descriptions[mode]}")

    def _session_line(self) -> str:
        # 顶栏只挂会话标题（与 WebUI 头部一致）；没有标题的新会话退回短 id。
        # 消息数不放在这：history 是启动快照，轮次进行中会滞后说谎。
        return self._session_title()

    def _session_title(self) -> str:
        store = getattr(self.conversation, "store", None)
        if store is not None:
            try:
                for row in store.list_sessions(limit=200, include_archived=True):
                    if row["id"] == self.session_id and row["title"]:
                        return row["title"]
            except Exception:
                # 标题只是展示位，读不到就退回短 id，不该把顶栏搞挂。
                pass
        return f"会话 {self._short_session()}"

    def _short_session(self) -> str:
        return self.session_id[:8]

    async def _mount_history(self) -> None:
        timeline = self.query_one("#timeline", VerticalScroll)
        by_tool_id: dict[str, ToolCard] = {}
        # 按用户消息切轮，收 tick 时长用（与 WebUI hydrateTurnDurations
        # 同一套对应：第 k 组 ↔ 第 k 个 turn span）。
        turn_groups: list[list[AssistantMessage]] = []
        for message in self.history:
            if message.role == "user":
                # 切组只认真正带文本的用户消息：tool_result 也住在 user
                # 消息里，但它是上一轮的回执，不开新轮（WebUI 同规则，
                # 否则组和 turn span 错位，tick 配错轮）。
                has_prompt = any(
                    block.type == "text" and block.text
                    for block in message.content
                )
                if has_prompt:
                    self._expect_kicker = True
                    turn_groups.append([])
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
                    assistant = AssistantMessage(
                        restored=True, show_kicker=self._expect_kicker
                    )
                    self._expect_kicker = False
                    await timeline.mount(assistant)
                    assistant.append_text(block.text)
                    if turn_groups:
                        turn_groups[-1].append(assistant)
                elif block.type == "tool_use":
                    card = ToolCard(block.id, block.name, block.input)
                    by_tool_id[block.id] = card
                    await timeline.mount(card)
        self._hydrate_turn_ticks(turn_groups)
        timeline.scroll_end(animate=False)

    def _hydrate_turn_ticks(self, turn_groups: list[list[AssistantMessage]]) -> None:
        """恢复会话时给每轮收尾的助手消息补 turn 时长（span-tick 数据源）。

        拉不到 trace 就静默：tick 只在真有数据时出现，账本不造假。
        超过 24h 的时长按脏数据丢——历史清扫曾把孤儿 span 的 end_time
        写成重启时刻，几天长的「轮次」都是假的。
        """
        store = getattr(self.conversation, "store", None)
        if store is None or not turn_groups:
            return
        try:
            spans = span_payloads(store, self.session_id)
        except Exception:
            return
        turn_spans = [span for span in spans if span["name"] == "turn"]
        for group, span in zip(turn_groups, turn_spans):
            if not group:
                continue
            duration = span.get("duration_ms")
            if (
                not isinstance(duration, (int, float))
                or duration < 0
                or duration >= 24 * 3_600_000
            ):
                continue
            self._tick_registry.append((group[-1], float(duration)))
        self._refresh_span_ticks()

    def _stamp_turn_tick(self) -> None:
        """本轮收线：把实际耗时记到最后一条助手消息上，并整批重画。"""
        if not self._turn_assistants or not self.turn_started:
            return
        elapsed = (time.monotonic() - self.turn_started) * 1000
        self._tick_registry.append((self._turn_assistants[-1], elapsed))
        self._refresh_span_ticks()

    def _refresh_span_ticks(self) -> None:
        """最长轮是所有 tick 的比例基准，基准变了旧的也要跟着重画。"""
        self._max_turn_ms = max(
            (duration for _, duration in self._tick_registry), default=0.0
        )
        for widget, duration in self._tick_registry:
            widget.set_tick(duration, self._max_turn_ms)

    def _scroll_timeline_end(self) -> None:
        """把续接会话定位到最后一轮，而不是停在历史开头。"""
        self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)

    async def _submit(self, question: str, *, plan_mode: bool = False) -> None:
        question = question.strip()
        images = list(self.pending_images)
        if not question and not images:
            return
        if images and self.steering_mode != "queue":
            self.notify("带图输入只支持 queue；按 Alt+S 切回 queue", severity="warning")
            return
        if images:
            self.pending_images = []
            self._update_attachment_status()
        composer = self.query_one("#composer", PromptComposer)
        composer.load_text("")
        if self.streaming:
            await self._steer(question, images)
            return
        await self._start_turn(question, images=images, plan_mode=plan_mode)

    async def _start_turn(
        self,
        question: str,
        *,
        show_user: bool = True,
        append_user: bool = True,
        images: list[ImageBlock] | None = None,
        plan_mode: bool = False,
    ) -> None:
        self._last_failed_question = None
        empty = self.query("#empty")
        if empty:
            await empty.remove()

        timeline = self.query_one("#timeline", VerticalScroll)
        if show_user:
            turn_images = list(images or []) if append_user else []
            await timeline.mount(UserMessage(question, len(turn_images)))
            self._expect_kicker = True
        self._assistant = None
        self._reasoning = None
        self._tool_cards = {}
        self._turn_assistants = []

        self.streaming = True
        self.turn_started = time.monotonic()
        self.turn_count += 1
        self.stop_reason = "—"
        self.turn_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cache_hit_ratio": 0,
        }
        running = RunningTurn(approval_mode=self.approval_mode)
        self.steering_turn = running
        self.conversation.steering = running
        self._set_running_state("STREAMING")
        self._update_ledger()
        self._send_button(disabled=False, label="STEER  ↵")
        self.call_after_refresh(timeline.scroll_end, animate=False)
        self._run_turn(
            question,
            running,
            append_user=append_user,
            images=list(images or []) if append_user else None,
            plan_mode=plan_mode,
        )

    async def _steer(
        self, question: str, images: list[ImageBlock] | None = None
    ) -> None:
        """流式期间发来的消息按当前档位进入当前轮或下一轮。"""
        if self.steering_mode == "cooperative":
            running = self.steering_turn
            if running is not None and running.inject(question):
                await self._show_command_result(
                    "COOPERATIVE",
                    f"已送入当前轮，下一次模型请求前生效：\n{question}",
                )
                return
            self.queued_prompts.append(QueuedPrompt(question, tuple(images or ())))
            self._update_ledger()
            await self._show_command_result(
                "QUEUE",
                f"当前轮刚好收尾，消息顺延为下一轮：\n{question}",
            )
            return

        self.queued_prompts.append(QueuedPrompt(question, tuple(images or ())))
        self._update_ledger()
        if self.steering_mode == "preemptive":
            if self.steering_turn is not None:
                self.steering_turn.cancel.set()
            self._set_running_state("PREEMPTING")
            await self._show_command_result(
                "PREEMPTIVE",
                f"已请求在下一个检查点打断当前轮：\n{question}",
            )
            return
        await self._show_command_result(
            "QUEUE",
            f"当前轮结束后执行：\n{question}",
        )

    @work(thread=True, group="agent-turn")
    def _run_turn(
        self,
        question: str,
        running: RunningTurn,
        append_user: bool = True,
        images: list[ImageBlock] | None = None,
        plan_mode: bool = False,
    ) -> None:
        """网络和工具执行在 worker 线程；所有 widget 修改都回主线程。

        append_user=False 是重试（P0 重试去重）：失败轮的 user 已在
        JSONL 里，不再追加同文一条；turn 内部校验历史尾部必须是 user。
        """
        outcome: tuple[str, str]
        try:
            for event in self.conversation.turn(
                question,
                origin=(
                    "retry"
                    if not append_user
                    else "plan"
                    if plan_mode
                    else ""
                ),
                append_user=append_user,
                images=images,
                plan_mode=plan_mode,
            ):
                try:
                    self.call_from_thread(self._apply_event, event)
                except RuntimeError:
                    return
            outcome = ("turn", self.conversation.stop_reason or "—")
        except TurnCancelled:
            outcome = ("cancelled", "")
        except Exception as exc:
            outcome = ("error", str(exc))
        finally:
            running.close()
            leftovers = running.drain()
            if self.conversation.steering is running:
                self.conversation.steering = None
        try:
            if leftovers:
                self.call_from_thread(self._queue_leftovers, leftovers)
            if outcome[0] == "cancelled":
                self.call_from_thread(self._finish_cancelled)
            elif outcome[0] == "error":
                self.call_from_thread(self._finish_error, outcome[1], question)
            else:
                self.call_from_thread(self._finish_turn, outcome[1])
        except RuntimeError:
            return

    def _queue_leftovers(self, prompts: list[str]) -> None:
        """cooperative 消息赶在轮末到达时，补进下一轮队列而不是丢掉。"""
        self.queued_prompts.extend(QueuedPrompt(prompt) for prompt in prompts)
        self._update_ledger()

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
                self._assistant = AssistantMessage(show_kicker=self._expect_kicker)
                self._expect_kicker = False
                self._turn_assistants.append(self._assistant)
                await self.query_one("#timeline", VerticalScroll).mount(self._assistant)
            self._assistant.append_text(fragment)
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "usage":
            self.turn_usage = dict(event.get("usage", {}))
            self._update_ledger()
            return
        if kind == "context_warning":
            self._finish_live_blocks()
            estimate = event["estimate_tokens"]
            budget = event["budget_tokens"]
            ratio = event["ratio"]
            await self._show_command_result(
                "CONTEXT",
                f"工作集接近预算：{estimate} / {budget} token"
                f"（{round(ratio * 100)}%）。超过预算会自动压缩；可 /compact 手动压缩。",
                warning=True,
            )
            self.notify("工作集接近预算", severity="warning")
            return
        if kind == "steering_injected":
            self._finish_live_blocks()
            timeline = self.query_one("#timeline", VerticalScroll)
            await timeline.mount(UserMessage(event["text"]))
            self._expect_kicker = True
            timeline.scroll_end(animate=False)
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
        if kind == "tool_approval":
            approval_id = event.get("approval_id")
            if not approval_id or self.approval is None:
                return
            tool_name = event.get("name", "tool")
            self._pending_approval_id = approval_id
            # 用户在事件到达前刚切到 auto/yolo：审批已经不再需要，
            # 不再弹一个马上失效的旧模态框。
            if not self.approval.requires_approval(self.approval_mode, tool_name):
                self.approval.decide(approval_id, True)
                self._pending_approval_id = None
                if self.streaming:
                    self._set_running_state("STREAMING")
                return
            self._set_running_state("APPROVAL")

            def resolved(choice: ApprovalChoice | None) -> None:
                self._approval_closed(approval_id, choice)

            self.push_screen(
                ApprovalScreen(
                    tool_name,
                    event.get("input", {}),
                    event.get("mode", self.approval_mode),
                ),
                resolved,
            )
            return
        if kind == "plan":
            self._finish_live_blocks()
            plan_id = event.get("plan_id", "")
            plan_text = event.get("text", "")

            def plan_resolved(confirmed: bool | None) -> None:
                self._plan_closed(plan_id, confirmed)

            self.push_screen(PlanScreen(plan_text), plan_resolved)
            return
        if kind == "tool_result":
            card = self._tool_cards.get(event["id"])
            if card is None:
                card = ToolCard(event["id"], event.get("name", "tool"), {})
                self._tool_cards[event["id"]] = card
                await self.query_one("#timeline", VerticalScroll).mount(card)
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

    def _plan_closed(self, plan_id: str, confirmed: bool | None) -> None:
        if confirmed is True and plan_id:
            self._confirmed_plan_id = plan_id
            self.notify("计划已确认；当前计划轮结束后执行")
        else:
            self.notify("已丢弃计划；不会执行任何写操作")

    async def _finish_turn(self, stop_reason: str) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self.stop_reason = stop_reason
        self._stamp_turn_tick()
        if self._confirmed_plan_id is not None:
            plan_id = self._confirmed_plan_id
            self._confirmed_plan_id = None
            await self._execute_plan(plan_id)
            return
        if await self._start_queued_turn():
            return
        self._set_running_state("COMPLETE")
        self._update_ledger()
        self._send_button(disabled=False, label="发送 ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _execute_plan(self, plan_id: str) -> None:
        if self.plan_broker is None:
            await self._show_command_result(
                "PLAN", "计划状态没有装配，不能执行。", error=True
            )
            return
        plan = self.plan_broker.consume_confirmed(plan_id, self.session_id)
        if plan is None:
            await self._show_command_result(
                "PLAN", "计划不存在、未确认或已执行。", error=True
            )
            return
        await self._start_turn(
            "用户已确认以下计划，请按计划执行，不要重新生成计划：\n\n"
            + plan.text
        )

    async def _finish_cancelled(self) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self.stop_reason = "cancelled"
        self._stamp_turn_tick()
        await self._show_command_result(
            "STOPPED",
            "当前轮已在检查点停止；半截回答未落盘，已完成的结果保留。",
        )
        if await self._start_queued_turn():
            return
        self._set_running_state("READY")
        self._update_ledger()
        self._send_button(disabled=False, label="发送 ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _finish_error(self, message: str, question: str) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self._last_failed_question = question
        self.stop_reason = "error"
        self._stamp_turn_tick()
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(RetryErrorCard(message, question))
        timeline.scroll_end(animate=False)
        self._set_running_state("ERROR")
        self._update_ledger()
        if await self._start_queued_turn():
            return
        self._send_button(disabled=False, label="发送 ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _start_queued_turn(self) -> bool:
        if not self.queued_prompts:
            return False
        queued = self.queued_prompts.popleft()
        await self._start_turn(queued.question, images=list(queued.images))
        return True

    def _update_attachment_status(self) -> None:
        count = len(self.pending_images)
        self.query_one("#composer", PromptComposer).allow_empty_submit = count > 0
        # 与 WebUI 的 composer-status 同位：默认显示键位提示，挂了图片时
        # 前置图片计数（图片挂载状态是此刻更要紧的信息）。
        self.query_one("#composer-status", Static).update(
            f"图片 {count} 张 · Enter 发送"
            if count
            else "Enter 发送 · Shift+Enter 换行 · Esc 停止本轮"
        )

    def _set_running_state(self, state: str) -> None:
        color = TOOL if state in ("READY", "COMPLETE") else (
            ERROR if state == "ERROR" else WARN
        )
        label = run_state_label(state)
        self.query_one("#top-state", Static).update(
            Text.assemble(("● ", color), (label, f"bold {color}"))
        )
        ledger = self.query_one("#ledger-state", Static)
        ledger.update(label)
        ledger.styles.color = color

    def _update_ledger(self) -> None:
        # 顶栏标题跟着刷：第一条用户消息落库后 set_title_if_empty 会补标题。
        self.query_one("#top-session", Static).update(self._session_line())
        self.query_one("#ledger-turns", Static).update(str(self.turn_count))
        self.query_one("#ledger-tools", Static).update(str(self.tool_count))
        self.query_one("#ledger-queued", Static).update(str(len(self.queued_prompts)))
        self.query_one("#ledger-stop", Static).update(
            stop_reason_label(self.stop_reason) if self.stop_reason else "—"
        )
        self.query_one("#ledger-input", Static).update(
            str(self.turn_usage.get("input_tokens", 0))
        )
        self.query_one("#ledger-output", Static).update(
            str(self.turn_usage.get("output_tokens", 0))
        )
        self.query_one("#ledger-cache-read", Static).update(
            str(self.turn_usage.get("cache_read_tokens", 0))
        )
        self.query_one("#ledger-cache-write", Static).update(
            str(self.turn_usage.get("cache_write_tokens", 0))
        )
        self.query_one("#ledger-cache-hit", Static).update(
            cache_hit_percent(self.turn_usage.get("cache_hit_ratio", 0))
        )
        self.query_one("#ledger-cost", Static).update(self._format_cost())
        if not self.streaming:
            self.query_one("#ledger-elapsed", Static).update(
                format_duration((time.monotonic() - self.turn_started) * 1000)
                if self.turn_started else "—"
            )

    def _format_cost(self) -> str:
        if self.pricing is None:
            return "不算钱（无价目）"
        usage = self.turn_usage
        amount = (
            Decimal(str(usage.get("input_tokens", 0)))
            * Decimal(self.pricing["input_per_million"])
            + Decimal(str(usage.get("cache_read_tokens", 0)))
            * Decimal(self.pricing["cache_read_per_million"])
            + Decimal(str(usage.get("cache_write_tokens", 0)))
            * Decimal(self.pricing["cache_write_per_million"])
            + Decimal(str(usage.get("output_tokens", 0)))
            * Decimal(self.pricing["output_per_million"])
        ) / Decimal(1_000_000)
        rendered = format(amount, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return f"{rendered or '0'} {self.pricing['currency']}"

    def _tick_elapsed(self) -> None:
        if self.streaming:
            elapsed = (time.monotonic() - self.turn_started) * 1000
            self.query_one("#ledger-elapsed", Static).update(format_duration(elapsed))

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

    def _history_key(self, event: events.Key) -> bool:
        """只在输入框首行/末行拦截上下键，多行编辑不被历史抢走。"""
        if event.key not in ("up", "down"):
            return False
        composer = self.query_one("#composer", PromptComposer)
        row, _ = composer.cursor_location
        last_row = max(0, composer.document.line_count - 1)
        if event.key == "up":
            if row > 0 or not self.input_history:
                return False
            if self._history_index is None:
                self._history_draft = composer.text
                self._history_index = len(self.input_history) - 1
            else:
                self._history_index = max(0, self._history_index - 1)
            self._restore_history_text(self.input_history[self._history_index])
            return True
        if row < last_row or self._history_index is None:
            return False
        if self._history_index < len(self.input_history) - 1:
            self._history_index += 1
            self._restore_history_text(self.input_history[self._history_index])
        else:
            self._history_index = None
            self._restore_history_text(self._history_draft)
        return True

    def _escape_key(self, event: events.Key) -> bool:
        if not self.streaming:
            return False
        self._request_stop()
        return True

    def _restore_history_text(self, text: str) -> None:
        composer = self.query_one("#composer", PromptComposer)
        composer.load_text(text)
        lines = text.splitlines() or [""]
        composer.move_cursor((len(lines) - 1, len(lines[-1])))
        composer.focus()

    def _remember_input(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if not self.input_history or self.input_history[-1] != text:
            self.input_history.append(text)
            del self.input_history[:-200]
        self._history_index = None
        self._history_draft = ""

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
        self._remember_input(text)
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

    @on(RetryErrorCard.Retry)
    async def _retry_failed_turn(self, event: RetryErrorCard.Retry) -> None:
        if self.streaming:
            self.notify("当前轮还在运行；先停止或等待它收线", severity="warning")
            return
        # append_user=False：失败轮的 user 消息已在 JSONL，重跑不重复落盘。
        await self._start_turn(event.question, show_user=False, append_user=False)

    @on(events.Click, "#composer-workdir")
    def _workdir_clicked(self) -> None:
        # 终端 Static 没有 hover title（WebUI 靠它解释这个按钮），点一下
        # 弹用法，指到 /workdir 命令上。
        self.notify("/workdir <绝对路径> 设置 · /workdir reset 恢复 · 下一轮起生效")

    @on(Button.Pressed, "#send")
    async def _send_pressed(self) -> None:
        composer = self.query_one("#composer", PromptComposer)
        text = composer.text.strip()
        if text or composer.allow_empty_submit:
            self._remember_input(text)
            await self._submit(text)
            return
        if self.streaming:
            self.notify("输入 steering 消息后再发送")
        else:
            self.query_one("#composer", PromptComposer).focus()

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
        if command.name == "stop":
            self._request_stop()
            return
        if command.name == "retry":
            if self.streaming:
                self.notify("当前轮还在运行，不能重试", severity="warning")
            elif self._last_failed_question is not None:
                await self._start_turn(
                    self._last_failed_question, show_user=False, append_user=False
                )
            else:
                await self._show_command_result(
                    "RETRY",
                    "还没有失败请求可重试。",
                    error=True,
                )
            return
        if command.name == "plan":
            await self._command_plan(argument)
            return
        if command.name == "attach":
            await self._command_attach(argument)
            return

        if command.name == "steering":
            await self._command_steering(argument)
            return
        if command.name == "effort":
            await self._command_effort(argument)
            return
        if command.name == "approval":
            await self._command_approval(argument)
            return
        if command.name == "rounds":
            await self._command_rounds(argument)
            return
        if command.name == "trace":
            await self._command_trace()
            return
        if command.name == "diff":
            self._command_diff()
            return
        if command.name == "transcript":
            await self._command_transcript(argument)
            return
        if command.name == "context":
            await self._command_context()
            return
        if command.name == "compact":
            await self._command_compact()
            return
        if command.name == "memory":
            await self._command_memory(argument)
            return
        if command.name == "copy":
            self.action_copy_answer()
            return
        if command.name == "export":
            await self._command_export(argument)
            return
        if command.name == "find":
            self._command_find(argument)
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
        if command.name == "workdir":
            await self._command_workdir(argument)
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
        if command.name in ("resume", "clear", "new"):
            if self.streaming:
                self.notify("当前轮结束后再切换会话", severity="warning")
                return
            if command.name == "resume":
                await self._command_resume(argument)
            else:
                self.exit(str(uuid.uuid4()))
            return
        prompts = {
            "now": "现在几点了？",
            "files": "看一下 sandbox 目录里有哪些文件。",
            "boundary": "先说明你准备怎么做，再执行，最后把结论和依据分开列出来。",
        }
        await self._submit(prompts[name])

    async def _command_plan(self, argument: str) -> None:
        if not argument:
            await self._show_command_result(
                "PLAN", "用法：/plan <任务>。先只读探索，再输出计划。", error=True
            )
            return
        if self.streaming:
            self.notify("当前轮结束后再进入 plan mode", severity="warning")
            return
        await self._submit(argument, plan_mode=True)

    async def _command_transcript(self, argument: str) -> None:
        if not argument:
            index = TRANSCRIPT_MODES.index(self.transcript_mode)
            next_mode = TRANSCRIPT_MODES[(index + 1) % len(TRANSCRIPT_MODES)]
        else:
            next_mode = argument.casefold()
        if next_mode not in TRANSCRIPT_MODES:
            await self._show_command_result(
                "TRANSCRIPT",
                f"未知档位：{argument}。可用：normal / thinking / verbose。",
                error=True,
            )
            return
        self._set_transcript_mode(next_mode)
        await self._show_command_result(
            "TRANSCRIPT", f"当前档位：{next_mode}"
        )

    async def _command_attach(self, argument: str) -> None:
        if not argument:
            await self._show_command_result(
                "ATTACH",
                "用法：/attach <path>（png / jpg / webp / gif）",
                error=True,
            )
            return
        path = Path(argument).expanduser().resolve()
        media_type = ATTACH_MEDIA_TYPES.get(path.suffix.casefold())
        if media_type is None:
            await self._show_command_result(
                "ATTACH",
                f"不支持的图片类型：{path.suffix or path.name}",
                error=True,
            )
            return
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            await self._show_command_result("ATTACH", str(exc), error=True)
            return
        self.pending_images.append(ImageBlock(media_type=media_type, data=data))
        self._update_attachment_status()
        await self._show_command_result(
            "ATTACH",
            f"{path}\n已排队到下一条用户消息；发送时只支持 queue。",
        )

    async def _command_export(self, argument: str) -> None:
        transcript = getattr(self.conversation, "transcript", None)
        if transcript is None:
            await self._show_command_result(
                "EXPORT", "会话正文没有装配，不能导出。", error=True
            )
            return
        try:
            messages = transcript.read(self.session_id)
        except Exception as exc:
            await self._show_command_result("EXPORT", str(exc), error=True)
            return
        if not messages:
            await self._show_command_result(
                "EXPORT", "当前会话还没有可导出的正文。", error=True
            )
            return
        if argument:
            path = Path(argument).expanduser().resolve()
        else:
            path = (
                transcript.directory.parent
                / "exports"
                / f"agentlearn-{self.session_id}.md"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
        markdown = transcript_to_markdown(
            messages,
            session_id=self.session_id,
            model=self.conversation.chat.model,
        )
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(markdown)
        except FileExistsError:
            await self._show_command_result(
                "EXPORT", f"文件已存在，不覆盖：\n{path}", error=True
            )
            return
        except OSError as exc:
            await self._show_command_result("EXPORT", str(exc), error=True)
            return
        await self._show_command_result(
            "EXPORT",
            f"{path}\nMarkdown 已导出 · {len(markdown)} chars · {len(messages)} messages",
        )

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
                if (
                    query in session.session_id.casefold()
                    or query in session.title.casefold()
                    or query in session.model.casefold()
                )
            ]
            if len(matches) == 1:
                self._resume_session(matches[0].session_id)
                return
            if not matches:
                await self._show_command_result(
                    "RESUME",
                    f"没有 id / 标题 / 模型匹配 {argument} 的会话。"
                    "输入 /resume 打开选择器。",
                    error=True,
                )
                return
            await self._show_command_result(
                "RESUME",
                "匹配到多个会话，请补充关键词，或直接输入 /resume 选择。",
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
        status = (
            f"会话     {self.session_id}\n"
            f"模型     {self.conversation.chat.model}\n"
            f"思考     {self.effort}\n"
            f"审批     {approval_label(self.approval_mode)}\n"
            f"插话     {steering_label(self.steering_mode)}\n"
            f"排队     {len(self.queued_prompts)}\n"
            f"状态     {run_state_label(state)}\n"
            f"轮次     {self.turn_count}\n"
            f"工具     {self.tool_count}\n"
            f"停止     {stop_reason_label(self.stop_reason)}\n"
            "存储     JSONL 正文 + SQLite 轨迹"
        )
        capabilities = None
        capability_error = ""
        if self.capability_loader is not None:
            try:
                capabilities = self.capability_loader()
            except Exception as exc:
                capability_error = str(exc)
        if capabilities is None:
            return (
                status
                + "\ncaps      unavailable"
                + (f"\ncaps err  {capability_error}" if capability_error else "")
            )
        sources: dict[str, list[str]] = {}
        for tool in capabilities.get("tools", []):
            sources.setdefault(tool.get("source", "unknown"), []).append(
                tool.get("name", "?")
            )
        source_text = " · ".join(
            f"{source} {len(names)}({', '.join(names)})"
            for source, names in sorted(sources.items())
        )
        runtime = capabilities.get("runtime", {})
        environment = capabilities.get("environment", {})
        mcp = capabilities.get("mcp", [])
        mcp_text = (
            " · ".join(
                f"{entry.get('alias', '?')}={entry.get('state', '?')}"
                for entry in mcp
            )
            if mcp
            else "none configured/not started"
        )
        return (
            status
            + f"\npython    {runtime.get('python', '?')}"
            + f"\nprovider  {runtime.get('provider', '?')}"
            + f"\nmodel     {runtime.get('model', '?')}"
            + f"\neffort    {runtime.get('effort') or 'omit'}"
            + f"\ntriggers  {runtime.get('triggers', '?')}"
            + f"\ntools     {source_text or 'none'}"
            + f"\nmcp       {mcp_text}"
            + f"\nsandbox   {environment.get('sandbox', '?')}"
            + f"\nboundary  {environment.get('sandbox_boundary', '?')}"
            + (f"\ncaps err  {capability_error}" if capability_error else "")
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
        self.pricing = updated.pricing
        self.query_one("#ledger-model", Static).update(updated.model_id)
        self._update_ledger()
        await self._show_command_result(
            "MODEL",
            f"{updated.model_id}\n下一轮模型请求生效。",
        )

    async def _command_steering(self, mode: str) -> None:
        if not mode:
            self.action_cycle_steering()
            return
        normalized = mode.casefold()
        if normalized not in STEERING_MODES:
            await self._show_command_result(
                "STEERING",
                f"未知档位 {mode}。可选：{', '.join(STEERING_MODES)}。",
                error=True,
            )
            return
        self._set_steering_mode(normalized)

    async def _command_effort(self, level: str) -> None:
        if not level:
            self.action_cycle_effort()
            return
        normalized = level.casefold()
        aliases = {"default": "", "omit": "", "none": ""}
        normalized = aliases.get(normalized, normalized)
        if normalized not in ("", "off", "low", "medium", "high", "max"):
            await self._show_command_result(
                "THINKING",
                f"未知等级 {level}。可选：omit / off / low / medium / high / max。",
                error=True,
            )
            return
        self._set_effort(normalized)

    async def _command_approval(self, mode: str) -> None:
        if not mode:
            self.action_cycle_approval()
            return
        normalized = mode.casefold()
        if normalized not in APPROVAL_MODES:
            await self._show_command_result(
                "APPROVAL",
                f"未知档位 {mode}。可选：{', '.join(APPROVAL_MODES)}。",
                error=True,
            )
            return
        self._set_approval_mode(normalized)

    async def _command_rounds(self, argument: str) -> None:
        """查看/调整本会话的工具轮数上限（欠账清单里那条 per-session 可调）。

        是运行态属性不是配置：调它只影响这个 TUI 进程里的会话，
        调试长循环（轮询式后台任务）时临时调高，不用改代码重启。
        当前轮不追改：turn() 进循环时就取好了次数，改的下一次 turn 生效。
        """
        current = self.conversation.max_tool_rounds
        if not argument:
            await self._show_command_result(
                "ROUNDS", f"本会话工具轮数上限：{current}（/rounds N 调整）"
            )
            return
        try:
            value = int(argument)
        except ValueError:
            await self._show_command_result(
                "ROUNDS", f"{argument} 不是整数。用法：/rounds 16", error=True
            )
            return
        if value < 1:
            await self._show_command_result(
                "ROUNDS", "上限最少是 1（0 会让循环一次都不跑）。", error=True
            )
            return
        self.conversation.max_tool_rounds = value
        await self._show_command_result(
            "ROUNDS", f"工具轮数上限：{current} -> {value}（下一轮 turn 生效）"
        )

    async def _command_context(self) -> None:
        """上下文构成（P1）：估算管「下次请求多大」，实数管「上次花了多少」。

        只读，不跑模型。compact_now/context_report 是 Conversation 的方法，
        TUI 和 web 端点（GET /sessions/{id}/context）共用同一份账。
        """
        try:
            report = self.conversation.context_report()
        except Exception as exc:
            await self._show_command_result("CONTEXT", str(exc), error=True)
            return
        est = report["estimate"]
        usage = report["last_usage"]
        usage_lines = (
            [
                f"input         {usage.get('input_tokens', '—')}",
                f"cache_read    {usage.get('cache_read_tokens', '—')}"
                f"（命中 {cache_hit_percent(usage.get('cache_hit_ratio'))}）",
                f"cache_write   {usage.get('cache_write_tokens', '—')}",
                f"output        {usage.get('output_tokens', '—')}",
            ]
            if usage
            else ["（还没有请求记录）"]
        )
        await self._show_command_result(
            "CONTEXT",
            "\n".join(
                [
                    f"消息          {report['messages_total']} 条"
                    f"（已压缩进摘要 {report['covered']}）",
                    f"摘要          {report['summary_chars']} 字",
                    "—— 下次请求的估算 ——",
                    f"摘要 token    {est['summary_tokens']}",
                    f"尾部 token    {est['tail_tokens']}",
                    f"system 字符   {est['system_chars']}",
                    f"工作集预算    {est['budget_tokens']} token",
                    "—— 上次请求的实数 ——",
                    *usage_lines,
                ]
            ),
        )

    async def _command_compact(self) -> None:
        """手动压缩：不等预算触发，立刻把能压的压掉（花一次模型请求）。"""
        if self.streaming:
            self.notify("当前轮结束后再压缩", severity="warning")
            return
        result = await self.run_worker(
            self.conversation.compact_now, thread=True, group="compact"
        )
        if result["compressed"]:
            await self._show_command_result(
                "COMPACT",
                f"已压缩：covered {result['covered_before']} → {result['covered']}"
                f"（摘要 {result['summary_chars']} 字，"
                f"尾部保留 {result['tail_messages']} 条）",
            )
        else:
            await self._show_command_result(
                "COMPACT",
                f"没有可压的（covered 已是 {result['covered']}，"
                "或只剩最后一个轮次——当前轮的提问不能压）。",
            )

    async def _command_memory(self, argument: str) -> None:
        # 不等当前轮：记忆每圈圈界重读，追加只影响之后的请求。
        # TUI 和 server 同进程，直接调 application 的函数，不走 HTTP。
        if not argument:
            content = read_memory()
            await self._show_command_result(
                "MEMORY",
                content.strip() or "（记忆还是空的。/memory <内容> 记第一条。）",
            )
            return
        try:
            line = append_memory(argument)
        except Exception as exc:
            await self._show_command_result("MEMORY", str(exc), error=True)
            return
        await self._show_command_result(
            "MEMORY", f"已记下（下一圈请求生效）：\n{line}"
        )

    async def _command_workdir(self, argument: str) -> None:
        """会话工作目录（D35，与 WebUI 的 workdir 按钮同一套语义）。

        目录跟会话走：写进 sessions.workdir，正在跑的 Conversation 每轮
        开头 _load_workdir 重读，所以改完下一轮就生效，不用重启界面。
        """
        store = getattr(self.conversation, "store", None)
        if store is None:
            await self._show_command_result(
                "WORKDIR", "存储没有装配，读不了会话工作目录。", error=True
            )
            return
        if not argument:
            raw = store.get_workdir(self.session_id)
            await self._show_command_result(
                "WORKDIR",
                f"当前：{raw or f'{SANDBOX}（默认）'}\n"
                "用法：/workdir <绝对路径> 换目录；/workdir reset 恢复默认。\n"
                "整个会话生效，下一轮起跑。",
            )
            return
        if not store.has_session(self.session_id):
            await self._show_command_result(
                "WORKDIR",
                "会话还没登记（第一条消息没发过）。先发一条消息，再设目录。",
                error=True,
            )
            return
        if argument.casefold() in ("reset", "default"):
            store.set_workdir(self.session_id, "")
            self._refresh_workdir_ui()
            await self._show_command_result(
                "WORKDIR", f"已恢复默认 sandbox。下一轮起从 {SANDBOX} 起跑。"
            )
            return
        try:
            normalized = normalize_workdir(argument)
        except ValueError as exc:
            await self._show_command_result("WORKDIR", str(exc), error=True)
            return
        store.set_workdir(self.session_id, str(normalized))
        self._refresh_workdir_ui()
        await self._show_command_result(
            "WORKDIR", f"工作目录已设：{normalized}。整个会话生效，下一轮起跑。"
        )

    def _workdir_label(self) -> str:
        """输入区目录位的短名，与 WebUI workdirLabel 同规则：取最后两段。"""
        store = getattr(self.conversation, "store", None)
        raw = ""
        if store is not None:
            try:
                raw = store.get_workdir(self.session_id)
            except Exception:
                raw = ""
        if not raw:
            return "sandbox"
        parts = [part for part in raw.split("/") if part]
        return "/".join(parts[-2:]) if parts else raw

    def _refresh_workdir_ui(self) -> None:
        self.query_one("#composer-workdir", Static).update(
            f"目录 {self._workdir_label()}"
        )

    async def _command_trace(self) -> None:
        store = getattr(self.conversation, "store", None)
        transcript = getattr(self.conversation, "transcript", None)
        if store is None or transcript is None:
            await self._show_command_result(
                "TRACE",
                "trace 存储没有装配，不能读取当前会话轨迹。",
                error=True,
            )
            return
        try:
            spans = span_payloads(store, self.session_id)
            messages = transcript.read(self.session_id)
        except Exception as exc:
            await self._show_command_result("TRACE", str(exc), error=True)
            return
        if not spans:
            await self._show_command_result(
                "TRACE",
                "当前会话还没有 span。先完成一次模型请求。",
            )
            return

        def load() -> tuple[list, list[str]]:
            return (
                span_payloads(store, self.session_id),
                answer_texts(transcript.read(self.session_id)),
            )

        self.push_screen(
            TraceScreen(
                self.session_id,
                store,
                spans,
                load,
            ),
            lambda _result: self.query_one("#composer", PromptComposer).focus(),
        )

    def _command_diff(self) -> None:
        self.push_screen(
            WorkspaceDiffScreen(
                lambda: changed_files(include_untracked=True)
            ),
            lambda _result: self.query_one("#composer", PromptComposer).focus(),
        )

    async def _show_command_result(
        self,
        title: str,
        body: str,
        *,
        error: bool = False,
        warning: bool = False,
    ) -> None:
        empty = self.query("#empty")
        if empty:
            await empty.remove()
        timeline = self.query_one("#timeline", VerticalScroll)
        # 收条标题在渲染口统一转中文（labels.RECEIPT_LABELS），调用点仍传英文枚举。
        await timeline.mount(
            CommandResult(receipt_label(title), body, error=error, warning=warning)
        )
        timeline.scroll_end(animate=False)

    def action_settings(self) -> None:
        if self.settings_loader is None or self.settings_saver is None:
            self.notify("设置回调没有装配", severity="error")
            return
        try:
            current = self.settings_loader()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.push_screen(
            SettingsScreen(current, self.settings_saver, self.model_loader),
            self._settings_closed,
        )

    def _settings_closed(self, saved: bool | None) -> None:
        if saved:
            self._refresh_effort()
            if self.settings_loader is not None:
                try:
                    snapshot = self.settings_loader()
                    self.pricing = snapshot.pricing
                    self.query_one("#ledger-model", Static).update(
                        snapshot.model_id
                    )
                    self._update_ledger()
                except Exception:
                    pass
            self.notify("设置已保存，下一轮生效")
        self.query_one("#composer", PromptComposer).focus()

    def _effort_label(self, value: str) -> str:
        return value or "default (omit)"

    def _refresh_effort(self) -> None:
        if self.settings_loader is None:
            return
        try:
            current = self.settings_loader()
        except Exception:
            return
        self.effort = self._effort_label(current.effort)
        self.query_one("#ledger-effort", Static).update(self.effort)

    def action_cycle_effort(self) -> None:
        """Alt+T：切换思考强度，本轮保持旧档，下一轮请求生效。"""
        if len(self.screen_stack) > 1 and not isinstance(self.screen, ApprovalScreen):
            return
        if self.settings_loader is None:
            self.notify("设置回调没有装配", severity="error")
            return
        try:
            current = self.settings_loader()
            next_effort = EFFORT_CYCLE.get(current.effort, "low")
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self._set_effort(next_effort)

    def action_cycle_approval(self) -> None:
        """Shift+Tab：approve -> auto -> yolo，立即影响后续工具调用。"""
        if len(self.screen_stack) > 1 and not isinstance(self.screen, ApprovalScreen):
            return
        index = APPROVAL_MODES.index(self.approval_mode)
        self._set_approval_mode(
            APPROVAL_MODES[(index + 1) % len(APPROVAL_MODES)]
        )

    def _set_approval_mode(self, mode: str) -> None:
        self.approval_mode = mode
        self.conversation.approval_mode = self.approval_mode
        auto_approved: list[str] = []
        if self.steering_turn is not None:
            approval_ids = self.steering_turn.set_approval_mode(self.approval_mode)
            if self.approval is not None:
                auto_approved = self.approval.auto_approve_if_allowed(
                    approval_ids, self.approval_mode
                )
        if (
            self._pending_approval_id is not None
            and self._pending_approval_id in auto_approved
            and isinstance(self.screen, ApprovalScreen)
        ):
            self.screen.dismiss(True)
        elif isinstance(self.screen, ApprovalScreen):
            self.screen.query_one("#approval-mode", Static).update(
                f"档位 {approval_label(self.approval_mode)}"
            )
        self.query_one("#ledger-approval", Static).update(
            approval_label(self.approval_mode)
        )
        descriptions = {
            "approve": "每次工具调用都要确认",
            "auto": "只读工具自动放行，bash 需确认",
            "yolo": "全部工具直接执行",
        }
        suffix = " · 已自动放行当前审批" if auto_approved else ""
        self.notify(
            f"执行等级：{approval_label(self.approval_mode)} · "
            f"{descriptions[self.approval_mode]}{suffix}"
        )

    def action_cycle_steering(self) -> None:
        """Alt+S：切换流式期间新消息的处理档位。"""
        if len(self.screen_stack) > 1:
            return
        index = STEERING_MODES.index(self.steering_mode)
        self._set_steering_mode(
            STEERING_MODES[(index + 1) % len(STEERING_MODES)]
        )

    def _set_steering_mode(self, mode: str) -> None:
        self.steering_mode = mode
        self.query_one("#ledger-steering", Static).update(
            steering_label(self.steering_mode)
        )
        descriptions = {
            "queue": "当前轮结束后再跑",
            "cooperative": "下一次请求前并入当前轮",
            "preemptive": "下一个检查点打断当前轮",
        }
        self.notify(
            f"插话：{steering_label(self.steering_mode)} · {descriptions[self.steering_mode]}"
        )

    def _set_effort(self, effort: str) -> None:
        if self.settings_loader is None or self.settings_saver is None:
            self.notify("设置回调没有装配", severity="error")
            return
        try:
            current = self.settings_loader()
            updated = self.settings_saver(
                SettingsValues(
                    provider=current.provider,
                    base_url=current.base_url,
                    model_id=current.model_id,
                    max_tokens=current.max_tokens,
                    effort=effort,
                    api_key=None,
                )
            )
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.effort = self._effort_label(updated.effort)
        self.query_one("#ledger-effort", Static).update(self.effort)
        self.notify(f"思考等级：{self.effort} · 本轮不变，下一轮生效")

    def _request_stop(self) -> None:
        if not self.streaming or self.steering_turn is None:
            self.notify("当前没有运行中的轮次")
            return
        if self.steering_turn.cancel.is_set():
            self.notify("停止请求已发出，等待检查点")
            return
        self.steering_turn.cancel.set()
        self.stop_reason = "stopping"
        self._set_running_state("STOPPING")
        self._update_ledger()
        self.notify("已请求停止；当前检查点收尾后生效")

    def action_history_search(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not self.input_history:
            self.notify("当前会话还没有输入历史")
            return
        self.push_screen(
            HistorySearchScreen(self.input_history),
            self._history_search_closed,
        )

    def _conversation_search_candidates(
        self,
    ) -> list[tuple[UserMessage | AssistantMessage, str]]:
        timeline = self.query_one("#timeline", VerticalScroll)
        return [
            (widget, widget.search_text)
            for widget in timeline.children
            if isinstance(widget, (UserMessage, AssistantMessage))
            and widget.search_text
        ]

    def _highlight_rendered_message(
        self, widget: UserMessage | AssistantMessage
    ) -> None:
        timeline = self.query_one("#timeline", VerticalScroll)
        for child in timeline.children:
            if isinstance(child, (UserMessage, AssistantMessage)):
                child.remove_class("search-highlight")
        widget.add_class("search-highlight")
        timeline.scroll_to_widget(widget, animate=False)

    def _clear_rendered_message_highlight(self) -> None:
        timeline = self.query_one("#timeline", VerticalScroll)
        for child in timeline.children:
            if isinstance(child, (UserMessage, AssistantMessage)):
                child.remove_class("search-highlight")

    def _command_find(self, argument: str) -> None:
        if not argument:
            self.push_screen(
                ConversationSearchScreen(self._conversation_search_candidates()),
                self._conversation_search_closed,
            )
            return
        normalized = argument.casefold()
        for widget, text in self._conversation_search_candidates():
            if normalized in text.casefold():
                self._highlight_rendered_message(widget)
                self.notify("已定位当前会话的匹配消息")
                return
        self._clear_rendered_message_highlight()
        self.notify("当前会话没有匹配的已渲染消息", severity="warning")

    def _conversation_search_closed(
        self, widget: UserMessage | AssistantMessage | None
    ) -> None:
        if widget is None:
            self.query_one("#composer", PromptComposer).focus()
            return
        self._highlight_rendered_message(widget)

    def _history_search_closed(self, value: str | None) -> None:
        if value:
            self._restore_history_text(value)
            return
        self.query_one("#composer", PromptComposer).focus()

    def action_edit_composer(self) -> None:
        if len(self.screen_stack) > 1:
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not editor:
            self.notify("未设置 $VISUAL 或 $EDITOR", severity="error")
            return
        composer = self.query_one("#composer", PromptComposer)
        try:
            command = shlex.split(editor)
        except ValueError as exc:
            self.notify(f"编辑器命令无法解析：{exc}", severity="error")
            return
        if not command:
            self.notify("编辑器命令为空", severity="error")
            return

        fd, temp_path = tempfile.mkstemp(prefix="agentlearn-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(composer.text)
            try:
                with self.suspend():
                    completed = subprocess.run(
                        [*command, temp_path],
                        check=False,
                    )
            except Exception as exc:
                self.notify(f"打开编辑器失败：{exc}", severity="error")
                return
            if completed.returncode != 0:
                self.notify(
                    f"编辑器退出码 {completed.returncode}，草稿未更新",
                    severity="warning",
                )
                return
            text = Path(temp_path).read_text(encoding="utf-8").rstrip("\n")
            composer.load_text(text)
            lines = text.splitlines() or [""]
            composer.move_cursor((len(lines) - 1, len(lines[-1])))
            composer.focus()
            self.notify("外部编辑器内容已回填")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def action_copy_answer(self) -> None:
        transcript = getattr(self.conversation, "transcript", None)
        if transcript is None:
            self.notify("会话正文没有装配，不能复制", severity="error")
            return
        try:
            answers = answer_texts(transcript.read(self.session_id))
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        answer = next((text for text in reversed(answers) if text), "")
        if not answer:
            self.notify("还没有可复制的完整回答")
            return
        copied = False
        try:
            subprocess.run(
                ["pbcopy"],
                input=answer,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            copied = True
        except (OSError, subprocess.SubprocessError):
            pass
        if not copied:
            self.copy_to_clipboard(answer)
        self.notify(f"已复制最近回答 · {len(answer)} chars")

    def _approval_closed(
        self, approval_id: str, choice: ApprovalChoice | None
    ) -> None:
        if self._pending_approval_id == approval_id:
            self._pending_approval_id = None
        if self.approval is not None:
            approved = bool(choice and choice.approved)
            self.approval.decide(
                approval_id,
                approved,
                scope=choice.scope if choice and choice.approved else "once",
                reason=(
                    choice.reason.strip()
                    if choice is not None and not choice.approved
                    else ""
                ),
            )
        if self.streaming:
            self._set_running_state("STREAMING")
        self.query_one("#composer", PromptComposer).focus()

    def on_unmount(self) -> None:
        if self.steering_turn is not None:
            self.steering_turn.cancel.set()
        if self.approval is not None:
            self.approval.cancel_all()

    def action_focus_composer(self) -> None:
        self.query_one("#composer", PromptComposer).focus()


def run_chat(
    conversation: Conversation,
    history: list[DomainMessage],
    session_id: str | None = None,
    settings_loader: Callable[[], SettingsSnapshot] | None = None,
    settings_saver: Callable[[SettingsValues], SettingsSnapshot] | None = None,
    session_loader: Callable[[], list[SessionSummary]] | None = None,
    approval: ApprovalBroker | None = None,
    plan_broker: PlanBroker | None = None,
    capability_loader: Callable[[], dict] | None = None,
    model_loader: Callable[[], list[str]] | None = None,
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
        approval,
        plan_broker,
        capability_loader,
        model_loader,
    ).run()
