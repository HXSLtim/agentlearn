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

任务账本有一份磁盘元数据（data/sandbox/jobs/<id>.json）：server 重启后
按 PID + 进程启动 token 认领。token 对不上（PID 复用/系统重启）就报告
不可接管，绝不按旧 PID 杀进程。
"""
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.sandbox import CHILD_ENV, SANDBOX, current_workdir

# 任务输出落这里：和 sandbox 同一棵树，bash（以及人）都能直接读。
JOBS_DIR = SANDBOX / "jobs"

# wait_job 一次最多等多久。上限要存在：工具是同步的，等太久等于把
# 这轮对话（和 SSE 连接）挂在一个调用上；要等更久就分多次 wait，
# 或者去干别的再来查。
WAIT_MAX_SECONDS = 120

# 内存里最多记多少个任务（含已结束的）。到了就清最老的已结束任务；
# 全在跑就先不清。防长寿命进程把账本养到无限大。
MAX_REMEMBERED_JOBS = 50
EXIT_UNKNOWN = "unknown"
MAX_JOB_LIFETIME_SECONDS = 30 * 60
LIFETIME_CHECK_SECONDS = 5


@dataclass
class Job:
    """一个后台任务：进程句柄或重启后认领的 PID + 输出文件路径 + 结果。"""

    id: str
    command: str
    proc: subprocess.Popen | None
    pid: int
    log_path: Path
    identity: str | None = None
    started_at: str | None = None
    expires_at: str | None = None
    # owned 进程是 int；重启后只知道它结束、拿不到退出码时是 EXIT_UNKNOWN。
    exit_code: int | str | None = None
    note: str = ""


# 账本 + 锁：web 的同步端点一线程一请求，两个请求可能同时查/停同一任务。
_lock = threading.Lock()
JOBS: dict[str, Job] = {}
_janitor_lock = threading.Lock()
_janitor_thread: threading.Thread | None = None


def _metadata_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _process_identity(pid: int) -> str | None:
    """进程启动 token + 进程组，用来防重启后的 PID 复用。"""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=,pgid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        return None
    return f"{' '.join(parts[:-1])}:{parts[-1]}"


def _write_metadata(job: Job) -> None:
    """原子写任务元数据；崩溃时要么旧文件在，要么新文件完整。"""
    data = {
        "version": 1,
        "id": job.id,
        "command": job.command,
        "pid": job.pid,
        "identity": job.identity,
        "log_path": str(job.log_path),
        "started_at": job.started_at or datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "expires_at": job.expires_at,
        "exit_code": job.exit_code,
    }
    path = _metadata_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _append_log_note(path: Path, note: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{stamp}] {note}\n")
    except OSError:
        pass


def _signal_process_group(job: Job) -> None:
    import signal

    try:
        os.killpg(job.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 3
    while _poll(job) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if _poll(job) is None:
        try:
            os.killpg(job.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    while _poll(job) is None and time.monotonic() < deadline:
        time.sleep(0.05)


def _expire_job(job: Job) -> None:
    if _poll(job) is not None:
        return
    _signal_process_group(job)
    job.note = f"达到 {MAX_JOB_LIFETIME_SECONDS // 60} 分钟寿命，已终止整个进程组"
    _append_log_note(job.log_path, job.note)
    _write_metadata(job)


def _lifetime_loop() -> None:
    while True:
        time.sleep(LIFETIME_CHECK_SECONDS)
        now = datetime.now(timezone.utc)
        with _lock:
            jobs = list(JOBS.values())
        for job in jobs:
            expires_at = _timestamp(job.expires_at)
            if (
                job.exit_code is None
                and expires_at is not None
                and expires_at <= now
            ):
                _expire_job(job)


def _ensure_lifetime_janitor() -> None:
    global _janitor_thread
    with _janitor_lock:
        if _janitor_thread is not None and _janitor_thread.is_alive():
            return
        _janitor_thread = threading.Thread(
            target=_lifetime_loop, daemon=True, name="job-lifetime"
        )
        _janitor_thread.start()


def restore_jobs() -> None:
    """server 启动时从磁盘账本认领旧任务。

    只在进程启动 token 完全一致时恢复 running；否则报告不可接管。
    这不是安全边界，只是防止 PID 复用后 stop_job 杀到无关进程。
    """
    if not JOBS_DIR.exists():
        return
    with _lock:
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(data["id"])
                command = str(data["command"])
                pid = int(data["pid"])
                identity = str(data.get("identity") or "")
                exit_code = data.get("exit_code")
                started_at = str(data.get("started_at") or "") or None
                expires_at = str(data.get("expires_at") or "") or None
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if job_id in JOBS:
                continue
            log_path = JOBS_DIR / f"{job_id}.log"
            if exit_code is not None:
                JOBS[job_id] = Job(
                    id=job_id, command=command, proc=None, pid=pid,
                    log_path=log_path, identity=identity or None,
                    exit_code=exit_code,
                    started_at=started_at,
                    expires_at=expires_at,
                )
                continue
            current_identity = _process_identity(pid)
            if current_identity is not None and current_identity == identity:
                JOBS[job_id] = Job(
                    id=job_id, command=command, proc=None, pid=pid,
                    log_path=log_path, identity=current_identity,
                    started_at=started_at,
                    expires_at=expires_at,
                )
            else:
                JOBS[job_id] = Job(
                    id=job_id, command=command, proc=None, pid=pid,
                    log_path=log_path, identity=identity or None,
                    exit_code=EXIT_UNKNOWN,
                    note="PID 不再是原任务进程，不能接管",
                    started_at=started_at,
                    expires_at=expires_at,
                )
    _ensure_lifetime_janitor()


def _prune_locked() -> None:
    """把最老的已结束任务清出账本（调用方必须已持锁）。"""
    if len(JOBS) < MAX_REMEMBERED_JOBS:
        return
    finished = [j for j in JOBS.values() if _poll(j) is not None]
    for job in finished[: len(JOBS) - MAX_REMEMBERED_JOBS + 1]:
        JOBS.pop(job.id, None)


def _poll(job: Job) -> int | str | None:
    """进程结束了吗。结束则回填 exit_code 并返回它，没结束返回 None。"""
    if job.exit_code is None:
        if job.proc is not None:
            code = job.proc.poll()
            if code is not None:
                job.exit_code = code
                _write_metadata(job)
        elif job.identity is not None:
            current_identity = _process_identity(job.pid)
            if current_identity != job.identity and not (
                current_identity is None and _pid_alive(job.pid)
            ):
                job.exit_code = EXIT_UNKNOWN
                job.note = "server 重启后进程已退出，退出码不可得"
                _write_metadata(job)
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
    # 和 bash 工具同一套约束：会话工作目录起跑、环境只给 PATH、shell=True。
    # cwd 在启动这一刻定死写进 Popen——任务活得比这一轮长，不能等以后再问
    # 「现在的会话目录是什么」。
    cwd = current_workdir()
    if not cwd.is_dir():
        raise ValueError(f"工作目录不存在：{cwd}")
    # 差别只有两个：不 capture 到内存（写文件，多大都装得下）、不等待。
    # start_new_session 让它进自己的进程组——stop_job 才能连子进程一起杀
    # （只杀 shell 的话，它起的后台子进程会残留，bash 工具就有这个坑）。
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        env=CHILD_ENV,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    started_at_dt = datetime.now(timezone.utc)
    started_at = started_at_dt.isoformat(timespec="seconds")
    expires_at = (
        started_at_dt + timedelta(seconds=MAX_JOB_LIFETIME_SECONDS)
    ).isoformat(timespec="seconds")
    identity = _process_identity(proc.pid)
    with _lock:
        job = Job(
            id=job_id, command=command, proc=proc, pid=proc.pid,
            log_path=log_path, identity=identity,
            started_at=started_at,
            expires_at=expires_at,
        )
        JOBS[job_id] = job
        _write_metadata(job)
        _prune_locked()
    _ensure_lifetime_janitor()
    return (
        f"任务已启动：{job_id}（{command}）。\n"
        f"wait_job(job_id=\"{job_id}\") 等它结束；job_status 查进度；"
        f"stop_job 停掉；超过 {MAX_JOB_LIFETIME_SECONDS // 60} 分钟自动终止。"
        f"输出也在 data/sandbox/jobs/{job_id}.log。"
    )


def _describe(job: Job) -> str:
    """一个任务当前的样子（状态 + 输出尾部）。"""
    code = _poll(job)
    if code is None:
        state = "还在跑"
    elif code == EXIT_UNKNOWN:
        state = f"不可接管（{job.note or '退出码不可得'}）"
    else:
        suffix = f"，{job.note}" if job.note else ""
        state = f"已结束（exit {code}{suffix}）"
    head = f"任务 {job.id}（{job.command}）{state}。输出：\n"
    return head + _tail(job.log_path)


def job_status(job_id: str) -> str:
    """查一个任务的现状和目前的输出，不等待。"""
    return _describe(_find(job_id))


def wait_job(job_id: str, seconds: float = 30) -> str:
    """等一个任务结束（最多 seconds 秒，上限 120）。"""
    seconds = max(0.5, min(float(seconds), WAIT_MAX_SECONDS))
    job = _find(job_id)
    if job.proc is None:
        deadline = time.monotonic() + seconds
        while _poll(job) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if _poll(job) is None:
            # 超时不是错误：任务还在跑，如实报告目前的输出，模型自己决定
            # 再等一次还是先去干别的。
            return f"等了 {seconds:g} 秒还没结束。{_describe(job)}"
    else:
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
    _signal_process_group(job)
    return f"任务 {job.id} 已停止。{_describe(job)}"
