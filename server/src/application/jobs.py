"""耐心（2026-11-03）：比 bash 十秒上限长的活，交给后台任务。

agent 自盘点五缺口的第二个。bash 工具的超时是 10 秒——那是防「看起来
在跑、其实永远不结束」的命令挂死整轮对话的；但真的有正经活要跑一分钟
（装依赖、跑测试），10 秒装不完。

形态选了轮询式（D26）：四个纯工具，循环本体零改动——

    start_job(command)            立刻返回 job_id，命令在后台跑
    wait_job(job_id, seconds)     阻塞等它结束（有上限），适合「没别的可干」
    job_status(job_id)            查状态和目前的输出，不阻塞
    stop_job(job_id)              手动停掉

模型的行为模式由工具说明教：长任务 start_job，等结果 wait_job 或轮询
job_status，等的时候可以干别的。轮询烧 MAX_TOOL_ROUNDS（每查一次算
一轮工具），wait_job 不烧——它是一次调用慢慢返回，和 bash 慢是同一种慢，
只是上限长得多。这个取舍写进说明，让模型自己选。

否掉的另一条路（事件注入：任务完成时像软中断一样把「job 好了」塞进
圈界）要 turn 级的暂停/唤醒，是新架构；轮询式今天就能用，代价诚实。

任务进程的账本只在内存（JOBS 字典）：server 重启后进程还活着但查不到
了——输出文件还在 data/sandbox/jobs/<id>.log，bash 里能看。学习期接受，
记在 TODO。
"""
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from application.sandbox import CHILD_ENV, SANDBOX

# 任务输出落这里：和 sandbox 同一棵树，bash（以及人）都能直接读。
JOBS_DIR = SANDBOX / "jobs"

# wait_job 一次最多等多久。上限要存在：工具是同步的，等太久等于把
# 这轮对话（和 SSE 连接）挂在一个调用上；要等更久就分多次 wait，
# 或者去干别的再来查。
WAIT_MAX_SECONDS = 120

# 内存里最多记多少个任务（含已结束的）。到了就清最老的已结束任务；
# 全在跑就先不清。防长寿命进程把账本养到无限大。
MAX_REMEMBERED_JOBS = 50


@dataclass
class Job:
    """一个后台任务：进程句柄 + 输出文件路径 + 结果（结束时回填）。"""

    id: str
    command: str
    proc: subprocess.Popen
    log_path: Path
    # poll() 谁先调谁回填，纯缓存；状态真源是 proc.poll()。
    exit_code: int | None = field(default=None)


# 账本 + 锁：web 的同步端点一线程一请求，两个请求可能同时查/停同一任务。
_lock = threading.Lock()
JOBS: dict[str, Job] = {}


def _prune_locked() -> None:
    """把最老的已结束任务清出账本（调用方必须已持锁）。"""
    if len(JOBS) < MAX_REMEMBERED_JOBS:
        return
    finished = [j for j in JOBS.values() if _poll(j) is not None]
    for job in finished[: len(JOBS) - MAX_REMEMBERED_JOBS + 1]:
        JOBS.pop(job.id, None)


def _poll(job: Job) -> int | None:
    """进程结束了吗。结束则回填 exit_code 并返回它，没结束返回 None。"""
    if job.exit_code is None:
        code = job.proc.poll()
        if code is not None:
            job.exit_code = code
    return job.exit_code


def _tail(path: Path, limit: int = 1500) -> str:
    """输出文件的末尾（截断到 limit 字符）。文件是任务进程在写的，
    读一半是常态——读到什么给什么，注明「（未完）」。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "(还没有输出)"
    return text[-limit:] if len(text) > limit else text or "(还没有输出)"


def _find(job_id: str) -> Job:
    with _lock:
        job = JOBS.get(job_id)
    if job is None:
        # 账本里没有：多半是 server 重启后的孤儿，或模型编的 id。
        raise ValueError(f"没有叫 {job_id} 的任务（重启过服务或 id 记错了）。")
    return job


def start_job(command: str) -> str:
    """把一条命令放到后台跑，立刻返回 job_id。"""
    if not command.strip():
        raise ValueError("空命令")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:8]
    log_path = JOBS_DIR / f"{job_id}.log"
    # 和 bash 工具同一套约束：sandbox 起跑、环境只给 PATH、shell=True。
    # 差别只有两个：不 capture 到内存（写文件，多大都装得下）、不等待。
    # start_new_session 让它进自己的进程组——stop_job 才能连子进程一起杀
    # （只杀 shell 的话，它起的后台子进程会残留，bash 工具就有这个坑）。
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=SANDBOX,
        env=CHILD_ENV,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    with _lock:
        JOBS[job_id] = Job(id=job_id, command=command, proc=proc, log_path=log_path)
        _prune_locked()
    return (
        f"任务已启动：{job_id}（{command}）。\n"
        f"wait_job(job_id=\"{job_id}\") 等它结束；job_status 查进度；"
        f"stop_job 停掉。输出也在 data/sandbox/jobs/{job_id}.log。"
    )


def _describe(job: Job) -> str:
    """一个任务当前的样子（状态 + 输出尾部）。"""
    code = _poll(job)
    if code is None:
        state = "还在跑"
    else:
        state = f"已结束（exit {code}）"
    head = f"任务 {job.id}（{job.command}）{state}。输出：\n"
    return head + _tail(job.log_path)


def job_status(job_id: str) -> str:
    """查一个任务的现状和目前的输出，不等待。"""
    return _describe(_find(job_id))


def wait_job(job_id: str, seconds: float = 30) -> str:
    """等一个任务结束（最多 seconds 秒，上限 120）。"""
    seconds = max(0.5, min(float(seconds), WAIT_MAX_SECONDS))
    job = _find(job_id)
    try:
        job.proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        # 超时不是错误：任务还在跑，如实报告目前的输出，模型自己决定
        # 再等一次还是先去干别的。
        return f"等了 {seconds:g} 秒还没结束。{_describe(job)}"
    return _describe(job)


def stop_job(job_id: str) -> str:
    """停掉一个任务（连它的子进程一起杀）。"""
    job = _find(job_id)
    if _poll(job) is not None:
        return f"任务 {job.id} 已经结束了，不用停。"
    # 杀整个进程组：kill 只到 shell 的话，sleep x && ... 里的子进程
    # 会变孤儿继续跑（bash 工具超时路径就有这个坑，这里修掉）。
    import signal
    try:
        os.killpg(job.proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        job.proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(job.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return f"任务 {job.id} 已停止。{_describe(job)}"
