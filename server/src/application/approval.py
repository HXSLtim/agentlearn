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
    approved: bool | None = None


class ApprovalBroker:
    """跨线程等待一次批准；approval_id 是界面回填时的凭据。"""

    def __init__(self, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def requires_approval(self, mode: str, tool_name: str) -> bool:
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        if mode == "yolo":
            return False
        if mode == "auto":
            return tool_name not in AUTO_APPROVED_TOOLS
        return True

    def open(self, tool_name: str = "") -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._pending[approval_id] = _PendingApproval(threading.Event(), tool_name)
        return approval_id

    def wait(self, approval_id: str) -> bool | None:
        """True 批准，False 拒绝，None 超时。调用后清掉 pending。"""
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return False
        pending.event.wait(self.timeout_seconds)
        with self._lock:
            self._pending.pop(approval_id, None)
        return pending.approved

    def decide(self, approval_id: str, approved: bool) -> bool:
        """从界面线程回填决定；找不到说明已超时、取消或重复点击。"""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            pending.approved = approved
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
