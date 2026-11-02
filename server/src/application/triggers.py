"""主动性触发器（D27 第一阶段）：把「世界发生了什么」变成一条 user 消息。

三种触发源，同一个约定：check() 纯查询（没跑就不消费，消息不丢），
mark() 在一次无人值守轮真正跑完后才记账——触发和执行解耦，中间隔着
调度循环的串行闸门和会话锁，谁也不该为谁的失败买单。

关键设计：**内容由服务器读进上下文，不靠模型的 bash**。文件触发器
自己读文件、任务触发器自己读 log 尾部，模型拿到的是现成文字。
这不是偷懒——auto 档下 bash 本来就被拒（无人值守的权限地板，D27），
如果触发要靠模型自己去 cat，触发就等于给了它一个旁门。服务器读 =
能力在触发器手里，模型只有文字。

自触发为什么不可能（第一阶段的结构性论证）：无人值守轮跑在 auto 档，
模型能改的只有会话本身；文件触发器盯的路径模型写不了（bash 被拒），
start_job 被拒所以也不会新造出任务完成事件。触发→行动→再触发的环
在权限层就断了，不需要额外的防风暴计数器。第二阶段容器化放开手脚后，
这个论证失效，熔断（次数/预算上限）从「以后再说」变成「必须做」。
"""
from __future__ import annotations

import time
from pathlib import Path

# 注入的内容上限（字符）。触发器是喂上下文的，不是传文件的；
# 超了截断并注明——模型真要全文可以（在有人的轮里）自己去读。
CONTENT_CHAR_LIMIT = 4000

# jobdone 触发器带上的输出尾部长度，和 wait_job 看到的同量级。
JOB_TAIL_CHARS = 800


class IntervalTrigger:
    """每 every_seconds 秒触发一次。启动后先等满第一个周期（开机不突袭）。

    用 time.monotonic 而不是墙钟：睡眠/挂起不推进它，笔记本合盖一晚
    不会攒出一串补触发——「不在场也延续」不包括报复性补作业。
    """

    def __init__(self, name: str, every_seconds: float, message: str) -> None:
        self._name = name
        self._message = message
        self._every = every_seconds
        self._next_due = time.monotonic() + every_seconds

    def check(self) -> str | None:
        if time.monotonic() < self._next_due:
            return None
        return f"[trigger:{self._name}] {self._message}"

    def mark(self) -> None:
        self._next_due = time.monotonic() + self._every


class FileTrigger:
    """盯一个文件的内容变化（mtime + 大小做签名，轮询，不引 watchdog）。

    启动时刻的已有内容不触发——只认「启动之后发生的变化」；文件被
    删掉不触发（盯着的那个人消失不算消息），重新出现算变化。
    """

    def __init__(self, name: str, path: str, message: str) -> None:
        self._name = name
        self._path = Path(path)
        self._message = message
        self._signature = self._current_signature()

    def _current_signature(self) -> tuple[float, int] | None:
        try:
            stat = self._path.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def check(self) -> str | None:
        current = self._current_signature()
        if current == self._signature:
            return None
        # 读内容：读不到（恰好在被删的瞬间）就不触发，下个周期再看。
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if len(text) > CONTENT_CHAR_LIMIT:
            text = text[:CONTENT_CHAR_LIMIT] + "\n…（超长截断）"
        return f"[trigger:{self._name}] {self._message}\n\n---\n{text}"

    def mark(self) -> None:
        self._signature = self._current_signature()


class JobDoneTrigger:
    """任何一个后台任务跑完就触发（耐心那层的账本，见 jobs.py）。

    只看本进程的账本：desktop server 里跑的任务它看得见，TUI 里起的
    任务在另一个进程、看不见（账本在内存，那条欠账）。启动时已经
    结束的任务不触发，只认运行中→结束的这一次跳变。
    """

    def __init__(self, name: str, message: str) -> None:
        self._name = name
        self._message = message
        # 启动即已结束的先记成 seen：它们的历史属于过去。
        self._seen = {job.id for job in self._snapshot() if job.exit_code is not None}

    @staticmethod
    def _snapshot():
        # 延迟 import：jobs 反过来要 tools 的常量，环和 tools.py 里
        # 那个是同一个，这里再晚一步进就没有新环。
        from application import jobs

        with jobs._lock:
            out = list(jobs.JOBS.values())
        # 账本的 exit_code 是懒缓存（_poll 谁调谁回填）：一个没人
        # wait_job 的任务跑完了，exit_code 会一直是 None。触发器要
        # 自己问一遍，不然永远看不见「跑完」这件事。（离线测试抓的 bug）
        for job in out:
            jobs._poll(job)
        return out

    def check(self) -> str | None:
        finished = [
            job for job in self._snapshot()
            if job.exit_code is not None and job.id not in self._seen
        ]
        if not finished:
            return None
        from application import jobs

        parts = [f"[trigger:{self._name}] {self._message}"]
        for job in finished:
            tail = jobs._tail(job.log_path, JOB_TAIL_CHARS)
            parts.append(f"任务 {job.id}（{job.command}）exit {job.exit_code}。输出：\n{tail}")
        return "\n\n".join(parts)

    def mark(self) -> None:
        self._seen.update(
            job.id for job in self._snapshot() if job.exit_code is not None
        )


def build_trigger(spec: dict):
    """配置段 -> 触发器实例。kind 已在 config.load_triggers 校验过。"""
    if spec["kind"] == "interval":
        return IntervalTrigger(spec["name"], spec["every_seconds"], spec["message"])
    if spec["kind"] == "file":
        return FileTrigger(spec["name"], spec["path"], spec["message"])
    return JobDoneTrigger(spec["name"], spec["message"])
