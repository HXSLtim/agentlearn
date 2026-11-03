"""工具执行审批。

执行等级是会话运行态，不写进 config.ini。三个等级，切换只影响后续工具调用：

- approve：每次工具调用都暂停，等用户 Accept 或 Deny
- auto：无副作用工具（查时间、查/等任务）加 remember 自动放行，其余要 Accept
- yolo：全部工具直接执行，不询问

Conversation 在真正调用 run_tool 前问这个 broker；TUI 用模态框回填结果，
desktop 用 POST /approval 回填。等待发生在执行线程里，模型请求本身不参与审批。
拒绝和超时都变成带 is_error 的 tool_result 交回模型，不抛异常。
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

APPROVAL_MODES = ("approve", "auto", "yolo")
APPROVAL_SCOPES = ("once", "session")
APPROVAL_REASON_MAX_LENGTH = 500
# auto 档自动放行的依据是「工具自己声明无副作用」——列表里的都是只读或
# 等待类：查任务状态、等任务结束都不改任何东西。start_job / stop_job
# 和 bash 一样有副作用，留在需要确认的那边。
# remember 是唯一刻意的例外：它有写操作，但写口收窄成「往 memory.md
# 追加一行带时间戳的记录」（D27）——无人值守轮在 auto 档下靠它留下
# 记忆，等价于用户自己的 /memory 命令，是窄授权不是开后门。
AUTO_APPROVED_TOOLS = frozenset({"now_time", "job_status", "wait_job", "remember"})


@dataclass
class _PendingApproval:
    event: threading.Event
    tool_name: str
    session_id: str
    approved: bool | None = None
    reason: str = ""


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""


class ApprovalBroker:
    """跨线程等待一次批准；approval_id 是界面回填时的凭据。"""

    def __init__(self, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, _PendingApproval] = {}
        self._session_grants: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def requires_approval(self, mode: str, tool_name: str) -> bool:
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        if mode == "yolo":
            return False
        if mode == "auto":
            return tool_name not in AUTO_APPROVED_TOOLS
        return True

    def open(self, tool_name: str = "", session_id: str = "") -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._pending[approval_id] = _PendingApproval(
                threading.Event(), tool_name, session_id
            )
        return approval_id

    def is_session_approved(self, session_id: str, tool_name: str) -> bool:
        """这个会话是否已经对同名工具建立过显式授权。"""
        with self._lock:
            return (session_id, tool_name) in self._session_grants

    def wait(self, approval_id: str) -> bool | None:
        """True 批准，False 拒绝，None 超时。调用后清掉 pending。"""
        decision = self.wait_with_reason(approval_id)
        return decision.approved if decision is not None else None

    def wait_with_reason(self, approval_id: str) -> ApprovalDecision | None:
        """等待完整决定；拒绝时把界面给的说明一并交回执行线程。"""
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return None
        pending.event.wait(self.timeout_seconds)
        with self._lock:
            self._pending.pop(approval_id, None)
        return ApprovalDecision(pending.approved is True, pending.reason)

    def decide(
        self,
        approval_id: str,
        approved: bool,
        *,
        scope: str = "once",
        reason: str = "",
    ) -> bool:
        """按作用域从界面线程回填决定。

        once 只影响当前 approval_id；session 在批准当前调用的同时，给
        `(session_id, tool_name)` 建立进程内授权，本会话后续同名工具不再询问。
        拒绝不建立授权，但可携带一段说明；授权不落盘，进程重启后失效。
        """
        if scope not in APPROVAL_SCOPES:
            raise ValueError(f"approval scope 只能是 {APPROVAL_SCOPES} 之一")
        clean_reason = reason.strip()
        if approved and clean_reason:
            raise ValueError("批准结果不能附带拒绝理由。")
        if len(clean_reason) > APPROVAL_REASON_MAX_LENGTH:
            raise ValueError(
                f"拒绝理由不能超过 {APPROVAL_REASON_MAX_LENGTH} 字符。"
            )
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            if scope == "session" and approved and pending.session_id:
                self._session_grants.add((pending.session_id, pending.tool_name))
            pending.approved = approved
            pending.reason = clean_reason
            pending.event.set()
        return True

    def auto_approve_if_allowed(
        self, approval_ids: Iterable[str], mode: str
    ) -> list[str]:
        """执行等级提高后，放行指定审批里已不再需要人工确认的那些。

        只处理调用方给定的 approval_id，避免同进程别的会话被误放行。
        返回实际放行的 id，界面可据此撤掉已经过期的 Accept 按钮。
        """
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        approved: list[str] = []
        with self._lock:
            for approval_id in dict.fromkeys(approval_ids):
                pending = self._pending.get(approval_id)
                if pending is None or self.requires_approval(mode, pending.tool_name):
                    continue
                pending.approved = True
                pending.event.set()
                approved.append(approval_id)
        return approved

    def cancel(self, approval_id: str) -> None:
        """调用方断开时释放等待，避免 pending 永久留在内存。"""
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None:
                return
            pending.approved = False
            pending.event.set()

    def cancel_all(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.approved = False
            item.event.set()
