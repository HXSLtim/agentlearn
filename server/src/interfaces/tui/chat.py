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

import json
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
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
    Tree,
)
from textual.widgets.option_list import Option

from application.approval import APPROVAL_MODES, ApprovalBroker
from application.conversation import Conversation, TurnCancelled
from application.tools import append_memory, read_memory
from application.session_manager import MODES as STEERING_MODES
from application.session_manager import RunningTurn
from application.workspace import changed_files
from domain.message import Message as DomainMessage
from infrastructure.config import SettingsSnapshot, SettingsValues
from .trace import SpanDetail, answer_texts, answers_for_requests
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
    SlashCommand("stop", "/stop", "在下一个检查点停止当前轮", "RUNTIME"),
    SlashCommand("retry", "/retry", "重跑上一条失败请求，不重复用户卡片", "RUNTIME"),
    SlashCommand(
        "steering",
        "/steering <mode>",
        "切换 queue / cooperative / preemptive",
        "RUNTIME",
        takes_argument=True,
    ),
    SlashCommand(
        "effort",
        "/effort [level]",
        "切换思考等级；不带参数循环",
        "RUNTIME",
        takes_argument=True,
    ),
    SlashCommand(
        "approval",
        "/approval [mode]",
        "切换 approve / auto / yolo",
        "RUNTIME",
        takes_argument=True,
    ),
    SlashCommand(
        "rounds",
        "/rounds [N]",
        "查看/调整本会话工具轮数上限",
        "RUNTIME",
        takes_argument=True,
    ),
    SlashCommand("clear", "/clear", "清空当前上下文并开新会话", "SESSION"),
    SlashCommand("new", "/new", "新开一个空会话", "SESSION"),
    SlashCommand(
        "memory",
        "/memory [内容]",
        "往跨会话记忆记一条；不带参数查看",
        "SESSION",
        takes_argument=True,
    ),
    SlashCommand("status", "/status", "查看当前会话与运行状态", "RUNTIME"),
    SlashCommand(
        "context",
        "/context",
        "查看上下文构成：估算 / 实数 / 预算",
        "RUNTIME",
    ),
    SlashCommand(
        "compact",
        "/compact",
        "手动压缩上下文（花一次模型请求）",
        "RUNTIME",
    ),
    SlashCommand("trace", "/trace", "查看当前会话的 span 轨迹", "RUNTIME"),
    SlashCommand("diff", "/diff", "查看当前 Git 工作区差异", "RUNTIME"),
    SlashCommand("copy", "/copy", "复制最近一条完整回答", "SYSTEM"),
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

# Alt+T 只在真正会发往模型的四档之间循环；默认档是“不发送”，
# 和 off 在 adapter 里语义相同，所以不把两个看起来不同的空档串进来。
EFFORT_CYCLE = {
    "": "low",
    "off": "low",
    "low": "medium",
    "medium": "high",
    "high": "max",
    "max": "",
}


class PromptComposer(TextArea):
    """多行输入台。Enter 发送，Shift+Enter / Ctrl+J 换行。"""

    command_key_handler: Callable[[events.Key], bool] | None = None
    history_key_handler: Callable[[events.Key], bool] | None = None
    escape_handler: Callable[[events.Key], bool] | None = None

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


class TraceScreen(ModalScreen[None]):
    """当前会话的只读 span 检查器，不离开对话 App。"""

    CSS = f"""
    TraceScreen {{
        align: center middle;
        background: rgba(0,0,0,0.88);
    }}

    #trace-modal-dialog {{
        width: 96%;
        height: 90%;
        background: {INK};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #trace-modal-header {{
        height: 3;
        align: center middle;
    }}

    #trace-modal-title {{
        width: 1fr;
        color: {SIGNAL};
        text-style: bold;
    }}

    #trace-modal-summary {{
        height: 2;
        color: {MUTED};
    }}

    #trace-modal-workspace {{
        height: 1fr;
    }}

    #trace-modal-tree-panel {{
        width: 45%;
        background: {PANEL};
        border: solid {LINE};
        padding: 0 1;
    }}

    #trace-modal-tree {{
        height: 1fr;
        background: {INK};
    }}

    #trace-modal-detail-panel {{
        width: 1fr;
        background: {PANEL};
        border: solid {LINE};
        padding: 0 1;
        margin-left: 1;
    }}

    #trace-modal-detail-scroll {{
        height: 1fr;
    }}

    #trace-modal-footer {{
        height: 2;
        padding-top: 1;
        color: {DIM};
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "close", show=False),
        Binding("r", "reload", "reload", show=False),
    ]

    def __init__(
        self,
        session_id: str,
        store,
        spans,
        loader: Callable[[], tuple[list, list[str]]],
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.store = store
        self.spans = spans
        self.loader = loader
        self.answer_by_span: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="trace-modal-dialog"):
            yield Static(
                f"TRACE  /  SESSION {self.session_id[:8]}",
                id="trace-modal-title",
            )
            yield Static(self._summary(), id="trace-modal-summary")
            with Horizontal(id="trace-modal-workspace"):
                with Vertical(id="trace-modal-tree-panel"):
                    yield Static("SPAN TREE", classes="ledger-kicker")
                    yield Tree("session", id="trace-modal-tree")
                with Vertical(id="trace-modal-detail-panel"):
                    yield Static("SPAN DETAIL", classes="ledger-kicker")
                    with VerticalScroll(id="trace-modal-detail-scroll"):
                        yield SpanDetail()
            yield Static(
                "↑↓ SELECT · ENTER EXPAND · R RELOAD · ESC CLOSE",
                id="trace-modal-footer",
            )

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        try:
            self.spans, answers = self.loader()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.answer_by_span = answers_for_requests(self.spans, answers)
        tree = self.query_one("#trace-modal-tree", Tree)
        tree.show_root = False
        tree.root.expand()
        for child in list(tree.root.children):
            child.remove()
        self._fill_tree(tree)
        self.query_one("#trace-modal-summary", Static).update(self._summary())
        self.query_one(SpanDetail).clear_detail()
        if self.spans:
            self.set_timer(0.01, self._select_default_span)
        tree.focus()

    def _fill_tree(self, tree: Tree) -> None:
        nodes_by_id: dict[str, object] = {}
        for span in self.spans:
            color = self._status_color(span["status"])
            label = f"● {span['name']}  {span['status']}  {self._detail(span)}"
            row = Text(label, style=color)
            if span["parent_id"] and span["parent_id"] in nodes_by_id:
                node = nodes_by_id[span["parent_id"]].add(
                    row, data=span["span_id"]
                )
            else:
                node = tree.root.add(row, data=span["span_id"])
            nodes_by_id[span["span_id"]] = node
            node.expand()

    def _detail(self, span) -> str:
        data = json.loads(span["data"] or "{}")
        duration = self.store.span_duration_ms(span["span_id"])
        cost = f"{duration}ms" if duration is not None else "未结束"
        if span["name"] == "llm_request":
            return f"{cost}  ttft={data.get('ttft_ms', '—')}ms"
        if span["name"] == "tool_call":
            return f"{cost}  {data.get('tool', '?')}"
        return cost

    def _status_color(self, status: str) -> str:
        if status == "ok":
            return TOOL
        if status == "error":
            return ERROR
        if status == "cancelled":
            return MUTED
        return WARN

    def _select_default_span(self) -> None:
        first = next(
            (
                span
                for span in self.spans
                if span["name"] in ("llm_request", "tool_call")
            ),
            self.spans[0],
        )
        tree = self.query_one("#trace-modal-tree", Tree)

        def find(node):
            if node.data == first["span_id"]:
                return node
            for child in node.children:
                found = find(child)
                if found is not None:
                    return found
            return None

        node = find(tree.root)
        if node is not None:
            tree.select_node(node)
            tree.scroll_to_node(node, animate=False)
            self._show_span(first["span_id"])

    def _show_span(self, span_id: str) -> None:
        span = next(
            (candidate for candidate in self.spans if candidate["span_id"] == span_id),
            None,
        )
        if span is None:
            return
        self.query_one(SpanDetail).show_span(
            span,
            self.store.span_duration_ms(span_id),
            self.answer_by_span.get(span_id, ""),
        )
        self.query_one("#trace-modal-detail-scroll", VerticalScroll).scroll_home(
            animate=False
        )

    @on(Tree.NodeHighlighted)
    def _node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data:
            self._show_span(event.node.data)

    def action_reload(self) -> None:
        self._load()
        self.notify("trace reloaded")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _summary(self) -> str:
        turns = sum(1 for span in self.spans if span["name"] == "turn")
        errors = sum(1 for span in self.spans if span["status"] == "error")
        cancelled = sum(1 for span in self.spans if span["status"] == "cancelled")
        running = sum(1 for span in self.spans if span["status"] == "running")
        return (
            f"SPANS {len(self.spans)} · TURNS {turns} · "
            f"ERRORS {errors} · CANCELLED {cancelled} · RUNNING {running}"
        )


class WorkspaceDiffScreen(ModalScreen[None]):
    """当前 Git 工作区的只读 diff pager。"""

    CSS = f"""
    WorkspaceDiffScreen {{
        align: center middle;
        background: rgba(0,0,0,0.88);
    }}

    #diff-dialog {{
        width: 96%;
        height: 90%;
        background: {INK};
        border: thick {SIGNAL};
        padding: 1 2;
    }}

    #diff-title {{
        height: 2;
        color: {SIGNAL};
        text-style: bold;
    }}

    #diff-summary {{
        height: 2;
        color: {MUTED};
    }}

    #diff-scroll {{
        height: 1fr;
        background: {INK};
        border: solid {LINE};
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
        scrollbar-background: {INK};
        scrollbar-corner-color: {INK};
    }}

    #diff-body {{
        width: 1fr;
        padding-top: 1;
    }}

    #diff-footer {{
        height: 2;
        padding-top: 1;
        color: {DIM};
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "close", show=False),
        Binding("r", "reload", "reload", show=False),
        Binding("up", "scroll_up", "up", show=False),
        Binding("down", "scroll_down", "down", show=False),
        Binding("pageup", "page_up", "page up", show=False),
        Binding("pagedown", "page_down", "page down", show=False),
        Binding("home", "scroll_home", "home", show=False),
        Binding("end", "scroll_end", "end", show=False),
    ]

    def __init__(self, loader: Callable[[], dict]) -> None:
        super().__init__()
        self.loader = loader

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Static("DIFF  /  GIT WORKSPACE", id="diff-title")
            yield Static("READING GIT STATUS…", id="diff-summary")
            with VerticalScroll(id="diff-scroll"):
                yield Static("READING WORKSPACE…", id="diff-body")
            yield Static(
                "↑↓/PAGE↑↓ SCROLL · HOME/END JUMP · R RELOAD · ESC CLOSE",
                id="diff-footer",
            )

    def on_mount(self) -> None:
        self._load()

    def _load(self) -> None:
        try:
            snapshot = self.loader()
        except Exception as exc:
            self.query_one("#diff-summary", Static).update(
                f"WORKSPACE UNAVAILABLE · {exc}"
            )
            self.query_one("#diff-body", Static).update(
                Text(f"WORKSPACE READ FAILED\n{exc}", style=ERROR)
            )
            self.query_one("#diff-scroll", VerticalScroll).focus()
            return
        self.query_one("#diff-summary", Static).update(self._summary(snapshot))
        self.query_one("#diff-body", Static).update(self._diff_text(snapshot))
        pager = self.query_one("#diff-scroll", VerticalScroll)
        pager.scroll_home(animate=False)
        pager.focus()

    def _summary(self, snapshot: dict) -> str:
        tracked = sum(
            1 for file in snapshot["files"] if file["status"] != "untracked"
        )
        return (
            f"ROOT {snapshot['root']} · TRACKED {tracked} · "
            f"UNTRACKED {snapshot['untracked_count']}"
        )

    def _diff_text(self, snapshot: dict) -> Text:
        text = Text()
        if not snapshot["files"]:
            text.append("没有工作区变更。\n", style=DIM)
            return text
        for file in snapshot["files"]:
            status_style = ERROR if file["status"] in ("deleted", "conflicted") else TOOL
            text.append(f"● {file['status'].upper()}  ", style=f"bold {status_style}")
            if file["old_path"]:
                text.append(f"{file['old_path']} → ", style=DIM)
            text.append(f"{file['path']}\n", style=PAPER)
            text.append(
                f"  +{file['additions']} −{file['deletions']}"
                + ("  BINARY" if file["binary"] else "")
                + ("  PATCH TRUNCATED" if file["truncated"] else "")
                + "\n",
                style=MUTED,
            )
            for hunk in file["hunks"]:
                text.append(f"{hunk['header']}\n", style=f"bold {DIM}")
                for line in hunk["lines"]:
                    old_line = "" if line["old_line"] is None else str(line["old_line"])
                    new_line = "" if line["new_line"] is None else str(line["new_line"])
                    marker = {
                        "context": " ",
                        "delete": "-",
                        "add": "+",
                        "meta": "\\",
                    }[line["kind"]]
                    style = MUTED
                    if line["kind"] == "add":
                        style = TOOL
                    elif line["kind"] == "delete":
                        style = ERROR
                    text.append(f"{old_line:>5} {new_line:>5} ", style=DIM)
                    text.append(f"{marker} {line['text']}\n", style=style)
            text.append("\n", style=DIM)
        return text

    def action_reload(self) -> None:
        self._load()
        self.notify("workspace diff reloaded")

    def action_scroll_up(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#diff-scroll", VerticalScroll).scroll_end(animate=False)

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


class ApprovalScreen(ModalScreen[bool]):
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
        max-height: 86%;
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
        max-height: 18;
        margin-bottom: 1;
        padding: 1 1;
        color: {PAPER};
        background: {INK};
        border: solid {LINE};
    }}

    #approval-actions {{
        height: 4;
        align: right middle;
    }}

    #approval-deny, #approval-accept {{
        width: 12;
        min-width: 12;
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
                f"{self.tool_name} 请求在宿主机执行。ACCEPT 才真正调用工具。",
                id="approval-copy",
            )
            yield Static(
                json.dumps(self.arguments, ensure_ascii=False, indent=2),
                id="approval-arguments",
            )
            with Horizontal(id="approval-actions"):
                yield Button("DENY", id="approval-deny")
                yield Button("ACCEPT", id="approval-accept")

    def on_mount(self) -> None:
        self.query_one("#approval-accept", Button).focus()

    def action_deny(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#approval-deny")
    def _deny(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#approval-accept")
    def _accept(self) -> None:
        self.dismiss(True)


def _format_session_time(value: str) -> str:
    """把 UTC ISO 时间压成选择器里够用的本地时间。"""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "unknown time"


def _user_text(message: DomainMessage) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


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
        self.approval_mode = getattr(conversation, "approval_mode", "approve")
        self.steering_mode = "queue"
        self.steering_turn: RunningTurn | None = None
        self.queued_prompts: deque[str] = deque()
        self.input_history: list[str] = [
            text
            for message in history
            if message.role == "user"
            if (text := _user_text(message))
        ]
        self._history_index: int | None = None
        self._history_draft = ""
        self._pending_approval_id: str | None = None
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
                yield Static("THINKING", classes="ledger-label")
                yield Static(self.effort, id="ledger-effort", classes="ledger-value")
                yield Static("APPROVAL", classes="ledger-label")
                yield Static(
                    self.approval_mode.upper(),
                    id="ledger-approval",
                    classes="ledger-value",
                )
                yield Static("STEERING", classes="ledger-label")
                yield Static(
                    self.steering_mode.upper(),
                    id="ledger-steering",
                    classes="ledger-value",
                )
                yield Static("QUEUED", classes="ledger-label")
                yield Static("0", id="ledger-queued", classes="ledger-value")
                yield Static("", classes="ledger-rule")
                yield Static("TURNS", classes="ledger-label")
                yield Static(str(self.turn_count), id="ledger-turns", classes="ledger-value")
                yield Static("TOOL CALLS", classes="ledger-label")
                yield Static(str(self.tool_count), id="ledger-tools", classes="ledger-value")
                yield Static("ELAPSED", classes="ledger-label")
                yield Static("—", id="ledger-elapsed", classes="ledger-value")
                yield Static("INPUT TOKENS", classes="ledger-label")
                yield Static("0", id="ledger-input", classes="ledger-value")
                yield Static("OUTPUT TOKENS", classes="ledger-label")
                yield Static("0", id="ledger-output", classes="ledger-value")
                yield Static("CACHE READ", classes="ledger-label")
                yield Static("0", id="ledger-cache-read", classes="ledger-value")
                yield Static("CACHE WRITE", classes="ledger-label")
                yield Static("0", id="ledger-cache-write", classes="ledger-value")
                yield Static("CACHE HIT", classes="ledger-label")
                yield Static("0‰", id="ledger-cache-hit", classes="ledger-value")
                yield Static("COST", classes="ledger-label")
                yield Static(
                    self._format_cost(),
                    id="ledger-cost",
                    classes="ledger-value",
                )
                yield Static("STOP REASON", classes="ledger-label")
                yield Static("—", id="ledger-stop", classes="ledger-value")
                yield Static("", classes="ledger-rule")
                yield Static(
                    "ENTER       send\n"
                    "SHIFT+ENTER newline\n"
                    "CTRL+J      newline\n"
                    "CTRL+N      focus input\n"
                    "CTRL+R      input history\n"
                    "CTRL+G      external editor\n"
                    "CTRL+O      copy last answer\n"
                    "CTRL+,      /config\n"
                    "ALT+T       thinking effort\n"
                    "SHIFT+TAB   approval mode\n"
                    "ALT+S       steering mode\n"
                    "ESC         stop active turn\n"
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
                "/resume SESSIONS · /trace SPANS · /diff WORKSPACE · /copy ANSWER · / ALL COMMANDS",
                id="composer-help",
            )

    async def on_mount(self) -> None:
        if self.history:
            await self._mount_history()
        composer = self.query_one("#composer", PromptComposer)
        composer.command_key_handler = self._command_key
        composer.history_key_handler = self._history_key
        composer.escape_handler = self._escape_key
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
        if not question:
            return
        composer = self.query_one("#composer", PromptComposer)
        composer.load_text("")
        if self.streaming:
            await self._steer(question)
            return
        await self._start_turn(question)

    async def _start_turn(
        self, question: str, *, show_user: bool = True, append_user: bool = True
    ) -> None:
        self._last_failed_question = None
        empty = self.query("#empty")
        if empty:
            await empty.remove()

        timeline = self.query_one("#timeline", VerticalScroll)
        if show_user:
            await timeline.mount(UserMessage(question))
        self._assistant = None
        self._reasoning = None
        self._tool_cards = {}

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
        self._run_turn(question, running, append_user=append_user)

    async def _steer(self, question: str) -> None:
        """流式期间发来的消息按当前档位进入当前轮或下一轮。"""
        if self.steering_mode == "cooperative":
            running = self.steering_turn
            if running is not None and running.inject(question):
                await self._show_command_result(
                    "COOPERATIVE",
                    f"已送入当前轮，下一次模型请求前生效：\n{question}",
                )
                return
            self.queued_prompts.append(question)
            self._update_ledger()
            await self._show_command_result(
                "QUEUE",
                f"当前轮刚好收尾，消息顺延为下一轮：\n{question}",
            )
            return

        self.queued_prompts.append(question)
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
        self, question: str, running: RunningTurn, append_user: bool = True
    ) -> None:
        """网络和工具执行在 worker 线程；所有 widget 修改都回主线程。

        append_user=False 是重试（P0 重试去重）：失败轮的 user 已在
        JSONL 里，不再追加同文一条；turn 内部校验历史尾部必须是 user。
        """
        outcome: tuple[str, str]
        try:
            for event in self.conversation.turn(
                question,
                origin="retry" if not append_user else "",
                append_user=append_user,
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
        self.queued_prompts.extend(prompts)
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
                self._assistant = AssistantMessage()
                await self.query_one("#timeline", VerticalScroll).mount(self._assistant)
            self._assistant.append_text(fragment)
            self.query_one("#timeline", VerticalScroll).scroll_end(animate=False)
            return
        if kind == "usage":
            self.turn_usage = dict(event.get("usage", {}))
            self._update_ledger()
            return
        if kind == "steering_injected":
            self._finish_live_blocks()
            timeline = self.query_one("#timeline", VerticalScroll)
            await timeline.mount(UserMessage(event["text"]))
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

            def resolved(approved: bool | None) -> None:
                self._approval_closed(approval_id, approved)

            self.push_screen(
                ApprovalScreen(
                    tool_name,
                    event.get("input", {}),
                    event.get("mode", self.approval_mode),
                ),
                resolved,
            )
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

    async def _finish_turn(self, stop_reason: str) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self.stop_reason = stop_reason
        if await self._start_queued_turn():
            return
        self._set_running_state("COMPLETE")
        self._update_ledger()
        self._send_button(disabled=False, label="RUN  ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _finish_cancelled(self) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self.stop_reason = "cancelled"
        await self._show_command_result(
            "STOPPED",
            "当前轮已在检查点停止；半截回答未落盘，已完成的结果保留。",
        )
        if await self._start_queued_turn():
            return
        self._set_running_state("READY")
        self._update_ledger()
        self._send_button(disabled=False, label="RUN  ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _finish_error(self, message: str, question: str) -> None:
        self._finish_live_blocks()
        self.streaming = False
        self.steering_turn = None
        self._last_failed_question = question
        self.stop_reason = "error"
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(RetryErrorCard(message, question))
        timeline.scroll_end(animate=False)
        self._set_running_state("ERROR")
        self._update_ledger()
        if await self._start_queued_turn():
            return
        self._send_button(disabled=False, label="RUN  ↵")
        self.query_one("#composer", PromptComposer).focus()

    async def _start_queued_turn(self) -> bool:
        if not self.queued_prompts:
            return False
        question = self.queued_prompts.popleft()
        await self._start_turn(question)
        return True

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
        self.query_one("#ledger-queued", Static).update(str(len(self.queued_prompts)))
        self.query_one("#ledger-stop", Static).update(self.stop_reason)
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
            f"{self.turn_usage.get('cache_hit_ratio', 0)}‰"
        )
        self.query_one("#ledger-cost", Static).update(self._format_cost())
        if not self.streaming:
            self.query_one("#ledger-elapsed", Static).update(
                f"{round((time.monotonic() - self.turn_started) * 1000)}ms"
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

    @on(Button.Pressed, "#send")
    async def _send_pressed(self) -> None:
        text = self.query_one("#composer", PromptComposer).text.strip()
        if text:
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
            elif self._last_failed_question:
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
        return (
            f"session   {self.session_id}\n"
            f"model     {self.conversation.chat.model}\n"
            f"thinking  {self.effort}\n"
            f"approval  {self.approval_mode}\n"
            f"steering  {self.steering_mode}\n"
            f"queued    {len(self.queued_prompts)}\n"
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
                f"（命中 {usage.get('cache_hit_ratio', '—')}‰）",
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
            spans = store.spans_for(self.session_id)
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
                store.spans_for(self.session_id),
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
        self, title: str, body: str, *, error: bool = False
    ) -> None:
        empty = self.query("#empty")
        if empty:
            await empty.remove()
        timeline = self.query_one("#timeline", VerticalScroll)
        await timeline.mount(CommandResult(title, body, error=error))
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
            SettingsScreen(current, self.settings_saver),
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
                f"MODE {self.approval_mode.upper()}"
            )
        self.query_one("#ledger-approval", Static).update(
            self.approval_mode.upper()
        )
        descriptions = {
            "approve": "每次工具调用都要 Accept",
            "auto": "只读工具自动放行，bash 需确认",
            "yolo": "全部工具直接执行",
        }
        suffix = " · 已自动放行当前审批" if auto_approved else ""
        self.notify(
            f"执行等级：{self.approval_mode.upper()} · "
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
            self.steering_mode.upper()
        )
        descriptions = {
            "queue": "当前轮结束后再跑",
            "cooperative": "下一次请求前并入当前轮",
            "preemptive": "下一个检查点打断当前轮",
        }
        self.notify(
            f"Steering：{self.steering_mode.upper()} · {descriptions[self.steering_mode]}"
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

    def _approval_closed(self, approval_id: str, approved: bool | None) -> None:
        if self._pending_approval_id == approval_id:
            self._pending_approval_id = None
        if self.approval is not None:
            self.approval.decide(approval_id, bool(approved))
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
    ).run()
