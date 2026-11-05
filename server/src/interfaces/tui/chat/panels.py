"""全屏只读检视器：span 轨迹与 Git 工作区 diff。"""

from __future__ import annotations

import json

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
