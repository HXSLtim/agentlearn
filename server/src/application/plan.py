"""Plan mode 的进程内确认状态。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

# 只放「工具本身无写操作」的白名单。MCP 工具没有机器可读副作用声明，
# 在 plan mode 一律不暴露；bash 即使看起来是 cat/ls 也不做命令过滤。
PLAN_ALLOWED_TOOLS = frozenset(
    {"now_time", "screenshot", "read_skill", "wait_job", "job_status"}
)


@dataclass
class PlanRecord:
    plan_id: str
    session_id: str
    text: str
    confirmed: bool = False
    claimed: bool = False


class PlanBroker:
    """保存模型产出的计划，并把「确认」和「执行」分成两步。"""

    def __init__(self) -> None:
        self._plans: dict[str, PlanRecord] = {}
        self._guard = threading.Lock()

    def create(self, session_id: str, text: str) -> str:
        clean = text.strip()
        if not clean:
            raise ValueError("计划文本不能为空。")
        plan_id = uuid.uuid4().hex
        with self._guard:
            self._plans[plan_id] = PlanRecord(plan_id, session_id, clean)
        return plan_id

    def get(self, plan_id: str) -> PlanRecord | None:
        with self._guard:
            plan = self._plans.get(plan_id)
            return PlanRecord(**plan.__dict__) if plan is not None else None

    def confirm(self, plan_id: str, session_id: str) -> PlanRecord | None:
        with self._guard:
            plan = self._plans.get(plan_id)
            if plan is None or plan.session_id != session_id or plan.claimed:
                return None
            plan.confirmed = True
            return PlanRecord(**plan.__dict__)

    def consume_confirmed(self, plan_id: str, session_id: str) -> PlanRecord | None:
        """一次性取走已确认计划；重复执行不能复用同一次确认。"""
        with self._guard:
            plan = self._plans.get(plan_id)
            if (
                plan is None
                or plan.session_id != session_id
                or not plan.confirmed
                or plan.claimed
            ):
                return None
            plan.claimed = True
            return PlanRecord(**plan.__dict__)
