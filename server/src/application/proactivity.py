"""主动性的调度循环（D27 第一阶段）：常驻线程，扣扳机、跑无人值守轮。

「请求来了才存在 → daemon + 事件循环」落在这里，但形态故意最小：
一个线程，每 granularity 秒醒一次，问每个触发器「世界有新消息吗」，
有且当前能跑，就把消息喂给 run_turn（由 web 进程组装：SessionManager
的会话锁 + auto 权限地板 + Conversation），跑成回来才让触发器记账。

run_turn 是注入的回调而不是直接 import：怎么构造 Conversation
（单例 transcript/store/broker、无 SSE 消费者）是接口层的事，
application 层不该知道 FastAPI 长什么样。方向和 Conversation
被两端共用是同一个原则倒过来用。

串行是刻意的：同时只跑一个无人值守轮。触发器天然稀疏（定时最少
60 秒、文件变化靠人、任务完成靠任务跑完），串行闸门换来的是
「无人值守的并发」这个不用付的复杂度。用户自己的轮次不受影响——
它们各自持有会话锁，两边靠锁排队会合。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from application.triggers import build_trigger

# 无人值守轮的工具轮数上限。比有人值守的默认 8 小：auto 档下模型
# 能干的只有观察和回答，4 轮足够表达；上限是烧钱的保险丝不是能力设定。
UNATTENDED_MAX_TOOL_ROUNDS = 4

# 同一个触发器连续失败这么多次后丢弃这几次触发（记账但不跑）：
# 配置坏了会永远失败，每 5 秒重试一晚上既是日志风暴也是无意义功。
MAX_CONSECUTIVE_FAILURES = 3

# 每次醒来问一遍所有触发器的间隔。
GRANULARITY_SECONDS = 5.0

# 触发器的「人生」写在哪：每次无人值守轮一行，谁触发、跑到哪、结果如何。
# 这是给操作者看的（不在 sandbox 里——那是模型的院子），一晚上过去
# `cat data/notices.log` 就知道它干了什么。
NOTICES_PATH = Path("data") / "notices.log"


def trigger_session_id(name: str) -> str:
    """每个触发器一个固定会话：uuid5（名字派生的确定性 UUID）。

    会话必须跨重启稳定（「存在」不能每晚失忆重开），但让操作者在
    config.ini 里手抄一串 UUID 又蠢又容易错。uuid5 同名同 id、是合法
    UUID（过 transcript.path_for 校验）、不同名不撞——三条全中。
    副作用：同一个触发器的「人生」可以在 TUI /resume 里找到续聊，
    有人的轮次和无人值守轮共用同一个会话和记忆。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentlearn/trigger/{name}"))


class ProactivityRunner(threading.Thread):
    """常驻调度线程。daemon=True：进程退出不等它。"""

    def __init__(self, specs: list[dict], run_turn) -> None:
        super().__init__(daemon=True, name="proactivity")
        self._triggers = [(build_trigger(spec), spec["name"]) for spec in specs]
        self._run_turn = run_turn
        self._failures: dict[str, int] = {}
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(GRANULARITY_SECONDS):
            # 单次 tick 里的异常不杀线程：调度器死掉 = 主动性悄悄消失，
            # 那是最难察觉的故障形态。记下来，下个周期继续。
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                _notice(f"scheduler tick failed: {exc}")

    def tick(self) -> None:
        """问一遍所有触发器。独立成方法：离线测试不用起线程。"""
        for trigger, name in self._triggers:
            message = trigger.check()
            if message is None:
                continue
            if self._failures.get(name, 0) >= MAX_CONSECUTIVE_FAILURES:
                # 放弃这几次触发但继续记账，下一次变化/到点再来。
                # 「丢弃」写进 notices：静默吞触发是调试地狱。
                trigger.mark()
                _notice(f"trigger={name} dropped after {MAX_CONSECUTIVE_FAILURES} failures")
                self._failures[name] = 0
                continue
            result = self._run_turn(trigger_session_id(name), message, name)
            if result.get("ran"):
                trigger.mark()
                self._failures[name] = 0
                _notice(
                    f"trigger={name} stop_reason={result.get('stop_reason', '?')}"
                )
            else:
                self._failures[name] = self._failures.get(name, 0) + 1
                _notice(f"trigger={name} run failed: {result.get('note', '?')}")


def _notice(line: str) -> None:
    """往 notices.log 追加一行（时间戳在这层统一加）。"""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        NOTICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with NOTICES_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        # 写不进日志不该杀调度循环；没有更好的去处，吞掉。
        pass
