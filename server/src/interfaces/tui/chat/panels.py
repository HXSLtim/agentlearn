"""全屏只读检视器：span 轨迹与 Git 工作区 diff。"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable

from rich.text import Text

from textual import on

from textual.app import ComposeResult

from textual.binding import Binding

from textual.containers import Horizontal, Vertical, VerticalScroll

from textual.screen import ModalScreen

from textual.widgets import (
    Static,
    Tree,
)

from ..trace import SpanDetail, answers_for_requests

from ..formatting import (
    diff_status_label,
    format_duration,
    span_status_label,
)
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
)


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
                f"轨迹  ·  会话 {self.session_id[:8]}",
                id="trace-modal-title",
            )
            yield Static(self._summary(), id="trace-modal-summary")
            with Horizontal(id="trace-modal-workspace"):
                with Vertical(id="trace-modal-tree-panel"):
                    yield Static("Span 树", classes="ledger-kicker")
                    yield Tree("会话", id="trace-modal-tree")
                with Vertical(id="trace-modal-detail-panel"):
                    yield Static("Span 详情", classes="ledger-kicker")
                    with VerticalScroll(id="trace-modal-detail-scroll"):
                        yield SpanDetail()
            yield Static(
                "↑↓ 选择 · Enter 展开 · R 刷新 · Esc 关闭",
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
            label = (
                f"● {span['name']}  {span_status_label(span['status'])}"
                f"  {self._detail(span)}"
            )
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
        # spans 来自 span_payloads：data 已解析成 dict（老代码自己 json.loads，
        # 那是还吃 sqlite3.Row 的年代）。
        data = span["data"] or {}
        duration = self.store.span_duration_ms(span["span_id"])
        cost = (
            format_duration(duration)
            if duration is not None
            else ("进行中" if span["status"] == "running" else "—")
        )
        if span["name"] == "llm_request":
            ttft = data.get("ttft_ms")
            suffix = f"  首字={format_duration(ttft)}" if ttft is not None else ""
            return f"{cost}{suffix}"
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
        self.notify("轨迹已刷新")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _summary(self) -> str:
        turns = sum(1 for span in self.spans if span["name"] == "turn")
        errors = sum(1 for span in self.spans if span["status"] == "error")
        cancelled = sum(1 for span in self.spans if span["status"] == "cancelled")
        running = sum(1 for span in self.spans if span["status"] == "running")
        return (
            f"span {len(self.spans)} · 轮次 {turns} · "
            f"错误 {errors} · 已取消 {cancelled} · 运行中 {running}"
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
        self._generation = 0
        self._closed = False
        self._announce = False

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Static("变更  ·  Git 工作区", id="diff-title")
            yield Static("正在读取 git status…", id="diff-summary")
            with VerticalScroll(id="diff-scroll"):
                yield Static("正在读取工作区…", id="diff-body")
            yield Static(
                "↑↓/Page↑↓ 滚动 · Home/End 跳转 · R 刷新 · Esc 关闭",
                id="diff-footer",
            )

    def on_mount(self) -> None:
        # git 读取不能占着 UI 线程。新会话一打开变更就在 on_mount 里同步
        # 跑完所有 git diff，事件循环进不来，Esc 关不掉，画面停在「正在读取」。
        self._start_load(announce=False)

    def _start_load(self, *, announce: bool) -> None:
        self._generation += 1
        self._announce = announce
        generation = self._generation
        loop = asyncio.get_running_loop()
        # 不用 Textual worker。关屏幕时 worker 的收尾会卡住消息泵，
        # Esc 进了 action_cancel，屏幕却不卸，面板就停在「正在读取」。
        threading.Thread(
            target=self._load_worker,
            args=(generation, loop),
            name="workspace-diff",
            daemon=True,
        ).start()

    def _load_worker(self, generation: int, loop: asyncio.AbstractEventLoop) -> None:
        try:
            snapshot = self.loader()
            error: Exception | None = None
        except Exception as exc:
            snapshot = None
            error = exc

        def apply() -> None:
            if self._closed or generation != self._generation or not self.is_mounted:
                return
            if error is not None:
                self._show_error(error)
            elif snapshot is not None:
                self._show_snapshot(snapshot)

        # 不要用 call_from_thread：它会等 UI 线程，而 UI 线程若正在等这次
        # 读取（或 pilot 在等队列排空），两边就死锁，面板关不掉。
        try:
            loop.call_soon_threadsafe(apply)
        except RuntimeError:
            return

    def _show_error(self, exc: Exception) -> None:
        self.query_one("#diff-summary", Static).update(f"工作区不可用 · {exc}")
        self.query_one("#diff-body", Static).update(
            Text(f"工作区读取失败\n{exc}", style=ERROR)
        )
        self.query_one("#diff-scroll", VerticalScroll).focus()

    def _show_snapshot(self, snapshot: dict) -> None:
        self.query_one("#diff-summary", Static).update(self._summary(snapshot))
        self.query_one("#diff-body", Static).update(self._diff_text(snapshot))
        pager = self.query_one("#diff-scroll", VerticalScroll)
        pager.scroll_home(animate=False)
        pager.focus()
        if self._announce:
            self.notify("工作区变更已刷新")

    def _summary(self, snapshot: dict) -> str:
        tracked = sum(
            1 for file in snapshot["files"] if file["status"] != "untracked"
        )
        omitted = snapshot.get("omitted_count") or 0
        suffix = f" · 未展开 {omitted}" if omitted else ""
        if snapshot.get("partial"):
            suffix += " · 读取未完成"
        return (
            f"{snapshot['root']} · 已跟踪 {tracked} · "
            f"未跟踪 {snapshot['untracked_count']}（已计入）{suffix}"
        )

    def _diff_text(self, snapshot: dict) -> Text:
        text = Text()
        if not snapshot["files"]:
            text.append("没有工作区变更。\n", style=DIM)
            return text
        for file in snapshot["files"]:
            status_style = ERROR if file["status"] in ("deleted", "conflicted") else TOOL
            text.append(
                f"● {diff_status_label(file['status'])}  ",
                style=f"bold {status_style}",
            )
            if file["old_path"]:
                text.append(f"{file['old_path']} → ", style=DIM)
            text.append(f"{file['path']}\n", style=PAPER)
            if file.get("omitted"):
                text.append("  差异未展开\n\n", style=MUTED)
                continue
            text.append(
                f"  +{file['additions']} −{file['deletions']}"
                + ("  二进制" if file["binary"] else "")
                + ("  补丁已截断" if file["truncated"] else "")
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
        self._start_load(announce=True)

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
        # 先作废在途结果，再关。读取还在 worker 里时也不能把 Esc 吃掉。
        self._closed = True
        self._generation += 1
        self.dismiss(None)
