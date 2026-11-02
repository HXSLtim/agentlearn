"""工具注册表和前两个工具：now_time 和 bash。

「定义」和「执行」都在这里：Tool 是说明书（发给模型看），
函数是本体（我们自己跑）。Conversation 拿 all_tools() 随请求发给模型，
收到 tool_use 后拿名字回 REGISTRY 找函数执行。

calculator（第 2 层的第一个工具，ast 白名单求值）已按路线换下——
第 3 层练的是多工具与并行，需要两个「互不相干、无法合并成一次调用」的工具；
算术并行的场景已被 bash 覆盖（echo $((123*456+789))），留着只会稀释场景。
它的写法（ast 白名单拒绝 eval）记在 docs/learning-path.md 第 2 层和
CLAUDE.md 约定 5，工具代码历史在 git（commit 46ef2e1）。

两个工具分别代表工具的两类典型形态：
- now_time：无参数、无副作用、永不失败——最简单的一档。
- bash：有参数、有副作用、会失败——真实工具的样子（第 3 层的失败验收靠它）。
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from domain.tool import Tool

# bash 的工作目录。注意：这只是「起跑点」，不是安全边界——
# 命令用绝对路径或 ../ 就能走到任何地方（离线实测 cat ../../config.ini 能读到）。
# 学习项目接受这一点：模型反正是我们自己的，工具进程跑在我们自己的账号下，
# 和自己开终端没有更多权限。真要对外服务，隔离是容器/独立用户的事（记在 TODO）。
# 目录固定在 server/data/sandbox，好歹让 ls 出来的默认视图是干净的，
# 也避免工具的临时文件把项目目录弄乱。data/ 整个在 gitignore 里。
SANDBOX = Path("data") / "sandbox"

# 单条命令的墙上时间上限。模型偶尔会提交 `cat`（没参数就等 stdin）或
# `python`（进交互解释器）这类「看起来在跑、其实永远不结束」的命令，
# 没有超时整轮对话就挂在它身上。
TIMEOUT_SECONDS = 10


def now_time() -> str:
    """报当前时间。没有参数，永远成功。"""
    # 统一 UTC（和 spans 的 _now 同一习惯），ISO 格式。模型要本地时间自己减。
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bash(command: str) -> str:
    """跑一条 shell 命令，返回 stdout + stderr。

    这是学习项目里「真实工具」的样子：有副作用、会失败、耗时不可控。
    失败不打断循环——run_tool 会把异常文本变成 is_error 结果交回模型。
    """
    SANDBOX.mkdir(parents=True, exist_ok=True)
    if not command.strip():
        raise ValueError("空命令")
    # shell=True 把整条命令交给 /bin/sh -c：管道 |、重定向 >、命令替换 $(...)
    # 都可用，和真实终端一致。放弃 shlex.split + 直接 exec 的方案，因为那等于
    # 向模型承诺一个「残缺的 shell」（$((...))、> 全是字面量），description
    # 没法如实写。安全上两者无差别——python -c 本来就能执行任意代码。
    # text=True 让 stdout/stderr 是字符串而不是字节；capture 住才有东西返回。
    # 超时 kill 的是 shell 进程，它的个别子进程可能残留（如后台 &），学习期接受。
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=SANDBOX,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        # 超时路径上 text=True 不总是生效：shell=True 时 TimeoutExpired 的
        # stdout/stderr 可能是 bytes 而不是 str（实测 macOS python3.14 如此，
        # trace 里那次 10008ms 的报错「can't concat str to bytes」就是它）。
        # 统一在这里转成 str，别把类型错误报给模型。
        partial = _as_text(e.stdout) + _as_text(e.stderr)
        # 说清超时和上限，模型看懂了才会改提交一条更快的命令，而不是原样重试。
        raise ValueError(f"命令超过 {TIMEOUT_SECONDS} 秒没结束，已终止。已输出: {partial!r}")
    # 返回码放进结果：很多命令「成功」但非零退出（grep 没匹配到），
    # 只给 stdout 模型分不清是空结果还是失败。
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"[exit {proc.returncode}] {out.strip() or '(无输出)'}"


def _as_text(data) -> str:
    """bytes 转 str（errors=replace 不抛错），None 和 str 原样处理。

    只在超时路径用：那条路上 subprocess 可能给 bytes。"""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data


NOW_TIME = Tool(
    name="now_time",
    description="获取当前的 UTC 时间（ISO 格式）。任何涉及「现在几点/今天日期」的问题都必须用它，不要猜。",
    input_schema={"type": "object", "properties": {}},
)

BASH = Tool(
    name="bash",
    description=(
        "在专属工作目录里跑一条 shell 命令，返回 stdout、stderr 和退出码。"
        "支持管道、重定向、$() 和 $((算术))，例如 echo $((123*456+789))。"
        "文件读写、python -c 一次性脚本都可以。命令有 10 秒超时。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令，例如 echo $((123*456+789))",
            }
        },
        "required": ["command"],
    },
)

# 注册表：工具名 -> (定义, 函数)。以后加工具就是在这里添一行，
# 循环和协议层都不用动——它们只认 Tool 和注册表，不认识具体工具。
REGISTRY: dict[str, tuple[Tool, object]] = {
    NOW_TIME.name: (NOW_TIME, now_time),
    BASH.name: (BASH, bash),
}


def all_tools() -> list[Tool]:
    """全部工具的定义。Conversation 每次请求模型随请求带过去。"""
    return [tool for tool, _ in REGISTRY.values()]


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """按名字执行一个工具，返回 (结果文本, 是否出错)。永不抛异常。

    出错也返回字符串（is_error=True）交回模型，而不是把异常抛出去打断
    整轮对话——模型看到错误描述能自己换路子（第 3 层专门练这个）。
    span 记录不在这里做：那是 Conversation（知道 turn 上下文）的职责。
    """
    entry = REGISTRY.get(name)
    if entry is None:
        # 模型幻觉出了一个不存在的工具名。如实告诉它有哪些可用，让它调整。
        return f"没有叫 {name} 的工具。可用：{', '.join(REGISTRY)}", True
    _, func = entry
    try:
        # **args 把模型给的字典展开成关键字参数：{"command": "..."}
        # 正好对上 bash(command=...)。给错参数名会 TypeError，落进 except。
        return str(func(**args)), False
    except Exception as e:
        return f"工具执行出错: {e}", True
