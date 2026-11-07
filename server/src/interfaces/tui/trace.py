"""Trace 的终端检查器。

`main.py trace <session_id>` 在交互终端进入这里。左边是 span 树，右边是
当前 span 的耗时、token、入参、出参和模型回答。数据仍然只来自
infrastructure/store.py 与 transcript.py，这一层不改写任何轨迹。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from .formatting import (
    cache_hit_percent,
    format_duration,
    span_status_label,
)
from application.traceView import (
    answer_texts,
    answers_for_requests,
    span_payloads,
)
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, Tree

from infrastructure.store import Store
from infrastructure.transcript import Transcript
from .theme import (
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

DATA = Path("data")


class SpanDetail(Static):
    def __init__(self) -> None:
        super().__init__(id="span-detail")

    def clear_detail(self) -> None:
        self.update(
            Text.assemble(
                ("选择一个 span\n\n", f"bold {SIGNAL}"),
                ("用 ↑ / ↓ 在左侧树中选择一步。\nspans 说明“怎么走的”，回答正文来自 JSONL。", MUTED),
            )
        )

    def show_span(
        self,
        span,
        duration_ms: int | None,
        answer: str,
    ) -> None:
        data = span["data"] or {}
        status = span["status"]
        status_color = TOOL if status == "ok" else (ERROR if status == "error" else MUTED if status == "cancelled" else WARN)
        # 只有还在跑的才说「未结束」；被清扫的孤儿 span 没有真实结束
        # 时间，显示「—」而不是假装它跑到了现在。
        duration = (
            format_duration(duration_ms)
            if duration_ms is not None
            else ("进行中" if status == "running" else "—")
        )
        status_text = span_status_label(status)
        text = Text.assemble(
            (span["name"].upper(), f"bold {PAPER}"),
            ("  /  ", DIM),
            (status_text, f"bold {status_color}"),
            ("  /  ", DIM),
            (duration, SIGNAL),
            ("\n", DIM),
            (f"span_id  {span['span_id']}\n", DIM),
            (f"开始     {_local_time(span['start_time'])}\n", MUTED),
            (f"结束     {_local_time(span['end_time']) if span['end_time'] else '—'}\n", MUTED),
        )

        if span["name"] == "llm_request":
            text.append("\n模型请求\n", style=f"bold {SIGNAL}")
            text.append(
                f"模型        {data.get('model', '—')}\n"
                f"消息        {data.get('messages', '—')}\n"
                f"首字        {format_duration(data.get('ttft_ms'))}\n"
                f"结束原因    {data.get('stop_reason', '—')}\n"
                f"思考强度    {data.get('effort', '—')}\n"
                f"token       输入 {data.get('input_tokens', '—')} / "
                f"输出 {data.get('output_tokens', '—')}\n"
                f"缓存        读 {data.get('cache_read_tokens', '—')} / "
                f"写 {data.get('cache_write_tokens', '—')} / "
                f"命中 {cache_hit_percent(data.get('cache_hit_ratio'))}\n",
                style=MUTED,
            )
            if answer:
                text.append("\n回答\n", style=f"bold {TOOL}")
                text.append(answer, style=PAPER)
        elif span["name"] == "tool_call":
            text.append("\n工具调用\n", style=f"bold {SIGNAL}")
            text.append(f"工具        {data.get('tool', '—')}\n", style=MUTED)
            text.append("\n输入\n", style=f"bold {TOOL}")
            text.append(_pretty(data.get("input", {})), style=PAPER)
            text.append("\n\n输出\n", style=f"bold {TOOL}")
            text.append(str(data.get("output", "—")), style=ERROR if status == "error" else PAPER)
        elif data:
            text.append("\n数据\n", style=f"bold {TOOL}")
            text.append(_pretty(data), style=PAPER)

        if data.get("message"):
            text.append("\n\n错误信息\n", style=f"bold {ERROR}")
            text.append(str(data["message"]), style=ERROR)
        self.update(text)


class TraceInspector(App):
    """一个只读的 span 树检查器。"""

    TITLE = "AgentLearn Trace"
    SUB_TITLE = "span inspector"
    CSS = f"""
    Screen {{
        background: {INK};
        color: {PAPER};
    }}

    #trace-topbar {{
        height: 3;
        background: {PANEL};
        border-bottom: solid {LINE};
        padding: 0 2;
        align: center middle;
    }}

    #trace-brand {{
        width: auto;
        color: {PAPER};
        text-style: bold;
    }}

    #trace-meta {{
        width: 1fr;
        color: {MUTED};
        padding-left: 3;
    }}

    #trace-state {{
        width: auto;
        color: {TOOL};
    }}

    #trace-summary {{
        height: 3;
        background: {PANEL};
        border-bottom: solid {LINE};
        padding: 0 2;
        align: center middle;
        color: {MUTED};
    }}

    #trace-workspace {{
        height: 1fr;
    }}

    #tree-panel {{
        width: 46%;
        background: {INK};
        border-right: solid {LINE};
        padding: 1 1;
    }}

    #tree-panel .panel-title, #detail-panel .panel-title {{
        height: 1;
        color: {SIGNAL};
        text-style: bold;
        margin-bottom: 1;
    }}

    #trace-tree {{
        height: 1fr;
        background: transparent;
        color: {PAPER};
        scrollbar-color: {SIGNAL};
        scrollbar-background: {INK};
        scrollbar-corner-color: {INK};
        overflow-x: hidden;
    }}

    #trace-tree .tree--cursor {{
        background: {PANEL};
        color: {PAPER};
        text-style: none;
    }}

    #trace-tree:focus .tree--cursor {{
        background: {LINE};
        color: {PAPER};
        text-style: bold;
    }}

    #trace-tree .tree--guides-selected {{
        color: {SIGNAL};
    }}

    #detail-panel {{
        width: 54%;
        background: {PANEL};
        padding: 1 2;
    }}

    #detail-scroll {{
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-color: {SIGNAL};
    }}

    #span-detail {{
        width: 100%;
        height: auto;
        padding: 0 1 1 1;
    }}

    #trace-footer {{
        height: 3;
        background: {PANEL};
        border-top: solid {LINE};
        padding: 0 2;
        color: {DIM};
        align: center middle;
    }}

    Screen.-narrow #trace-workspace {{
        layout: vertical;
    }}

    Screen.-narrow #tree-panel {{
        width: 100%;
        height: 45%;
        border-right: none;
        border-bottom: solid {LINE};
    }}

    Screen.-narrow #detail-panel {{
        width: 100%;
        height: 55%;
        padding: 1 1;
    }}
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "quit", show=False),
        Binding("r", "reload", "reload", show=False),
    ]

    def __init__(
        self,
        session_id: str,
        store: Store,
        spans,
        answers: list[str],
        loader: Callable[[], tuple[list, list[str]]] | None = None,
    ) -> None:
        super().__init__()
        apply_agentlearn_theme(self)
        self.session_id = session_id
        self.store = store
        self.spans = spans
        self.answers = answers
        self.loader = loader
        self.answer_by_span = self._answers_for_requests()

    def compose(self) -> ComposeResult:
        with Horizontal(id="trace-topbar"):
            yield Static(
                Text.assemble(
                    (" AGENTLEARN ", f"bold {INK} on {SIGNAL}"),
                    ("  /  TRACE", f"bold {PAPER}"),
                ),
                id="trace-brand",
            )
            yield Static(f"会话 {self.session_id}", id="trace-meta")
            yield Static("● 只读", id="trace-state")
        yield Static(self._summary(), id="trace-summary")
        with Horizontal(id="trace-workspace"):
            with Vertical(id="tree-panel"):
                yield Static("Span 树", classes="panel-title")
                yield Tree("会话", id="trace-tree")
            with Vertical(id="detail-panel"):
                yield Static("Span 详情", classes="panel-title")
                with VerticalScroll(id="detail-scroll"):
                    yield SpanDetail()
        yield Static(
            "↑↓ 选择 · Enter 展开 · R 刷新 · Ctrl+Q 退出",
            id="trace-footer",
        )

    def on_mount(self) -> None:
        tree = self.query_one("#trace-tree", Tree)
        tree.show_root = False
        tree.root.expand()
        self.query_one(SpanDetail).clear_detail()
        self._update_narrow(self.size.width)
        self._fill_tree(tree)
        tree.focus()

    def on_resize(self, event: events.Resize) -> None:
        self._update_narrow(event.size.width)

    def _update_narrow(self, width: int) -> None:
        self.screen.set_class(width < 104, "-narrow")

    def _fill_tree(self, tree: Tree) -> None:
        nodes_by_id: dict[str, object] = {}
        for span in self.spans:
            label, color = self._span_label(span)
            row = Text(label, style=color)
            if span["parent_id"] and span["parent_id"] in nodes_by_id:
                node = nodes_by_id[span["parent_id"]].add(row, data=span["span_id"])
            else:
                node = tree.root.add(row, data=span["span_id"])
            nodes_by_id[span["span_id"]] = node
            node.expand()
        if self.spans:
            # 树获得焦点和布局后会自己把光标放回根节点；等一帧再选默认项。
            self.set_timer(0.01, self._select_default_span)

    def _select_default_span(self) -> None:
        """选中第一段有内容的请求，turn 只作为容器。"""
        first = next(
            (
                span
                for span in self.spans
                if span["name"] in ("llm_request", "tool_call")
            ),
            self.spans[0],
        )
        tree = self.query_one("#trace-tree", Tree)

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
            self._show_span_id(first["span_id"])

    def _span_label(self, span) -> tuple[str, str]:
        data = span["data"] or {}
        duration = self.store.span_duration_ms(span["span_id"])
        status = span["status"]
        cost = (
            format_duration(duration)
            if duration is not None
            else ("进行中" if status == "running" else "—")
        )
        color = TOOL if status == "ok" else (ERROR if status == "error" else MUTED if status == "cancelled" else WARN)
        name = span["name"]
        detail = ""
        if name == "llm_request":
            detail = f"  首字={format_duration(data.get('ttft_ms'))}"
        elif name == "tool_call":
            detail = f"  {data.get('tool', '?')}"
        return f"● {name}  {span_status_label(status)}  {cost}{detail}", color

    @on(Tree.NodeHighlighted)
    def _node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        span_id = event.node.data
        if not span_id:
            return
        self._show_span_id(span_id)

    def _show_span_id(self, span_id: str) -> None:
        span = next((row for row in self.spans if row["span_id"] == span_id), None)
        if span is None:
            return
        self.query_one(SpanDetail).show_span(
            span,
            self.store.span_duration_ms(span_id),
            self.answer_by_span.get(span_id, ""),
        )
        self.query_one("#detail-scroll", VerticalScroll).scroll_home(animate=False)

    def action_reload(self) -> None:
        if self.loader is None:
            return
        try:
            self.spans, self.answers = self.loader()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        tree = self.query_one("#trace-tree", Tree)
        for child in list(tree.root.children):
            child.remove()
        self.answer_by_span = self._answers_for_requests()
        self._fill_tree(tree)
        self.query_one("#trace-summary", Static).update(self._summary())
        self.notify("trace reloaded", severity="information")

    def _answers_for_requests(self) -> dict[str, str]:
        return answers_for_requests(self.spans, self.answers)

    def _summary(self) -> str:
        turns = sum(1 for span in self.spans if span["name"] == "turn")
        errors = sum(1 for span in self.spans if span["status"] == "error")
        running = sum(1 for span in self.spans if span["status"] == "running")
        duration = sum(
            self.store.span_duration_ms(span["span_id"]) or 0
            for span in self.spans
            if span["name"] == "turn"
        )
        return (
            f"SPANS {len(self.spans)}   ·   TURNS {turns}   ·   "
            f"错误 {errors}   ·   运行中 {running}   ·   总时长 {duration}ms"
        )


def _pretty(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)



def _local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _answer_texts(messages) -> list[str]:
    # 薄别名：调用点写的是它，逻辑在 application.traceView（web 端点共用）。
    return answer_texts(messages)


def run_trace(session_id: str) -> None:
    """加载 trace 并启动只读检查器。

    trace 不读 config.ini，也不构造模型客户端；会话没有跑过模型时，
    应该仍然能离线查看已经存在的 spans。
    """
    transcript = Transcript(DATA / "sessions")
    messages = transcript.read(session_id)
    store = Store(DATA / "agent.db")
    spans = span_payloads(store, session_id)
    if not spans:
        raise SystemExit("这个会话没有 trace 记录。")

    def load() -> tuple[list, list[str]]:
        fresh_messages = transcript.read(session_id)
        return span_payloads(store, session_id), _answer_texts(fresh_messages)

    TraceInspector(
        session_id,
        store,
        spans,
        _answer_texts(messages),
        load,
    ).run()
