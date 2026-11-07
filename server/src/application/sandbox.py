"""sandbox 常量：bash / jobs / screenshot 共用的执行环境基线。

原来长在 tools.py，jobs.py 反过来 import 它们，tools 的 REGISTRY 又
引用 jobs 的函数——一个只能按特定顺序导入的环（jobs 先导入就炸，
离线测试抓的）。常量谁都要、谁都不该拥有，独立成模块，环就没了。
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path

# bash / 后台任务的默认工作目录。注意：这只是「起跑点」，不是安全边界——
# 命令用绝对路径或 ../ 就能走到任何地方（离线实测 cat ../../config.ini
# 能读到）。学习项目接受这一点：模型反正是我们自己的，工具进程跑在
# 我们自己的账号下，和自己开终端没有更多权限。真要对外服务，隔离是
# 容器/独立用户的事（记在 TODO / D17）。
# 默认固定在 server/data/sandbox，好歹让 ls 出来的默认视图是干净的，
# 也避免工具的临时文件把项目目录弄乱。data/ 整个在 gitignore 里。
# 会话可以改起跑点（sessions.workdir，D35）：改的是这个会话之后
# 每一轮，不是某一条命令。没改过的会话仍从这里起跑。
SANDBOX = Path("data") / "sandbox"

# 当前这次工具调用的起跑目录。Conversation 在 run_tool 前后 set/reset，
# 没人设过就是默认 sandbox。用 contextvar 而不是函数参数：run_tool 把
# 模型给的字典原样展开成关键字，cwd 若做成 bash 的参数，模型每一跳都能
# 改目录——那又变回「某一轮 / 某一条命令」了。
_workdir_override: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "tool_workdir", default=None
)


def bind_workdir(path: Path) -> contextvars.Token:
    """这一跳工具用这个目录。调用方必须在同一线程里 reset。"""
    return _workdir_override.set(path)


def reset_workdir(token: contextvars.Token) -> None:
    _workdir_override.reset(token)


def current_workdir() -> Path:
    """bash / start_job 此刻该 cd 去哪。

    默认 sandbox 不存在就建（第一次跑工具不该因为目录还没建就失败）。
    会话指定的目录不存在不偷偷建——那是用户打错路径，建出来只会多一个
    空文件夹，把错误藏掉。
    """
    override = _workdir_override.get()
    if override is not None:
        return override
    SANDBOX.mkdir(parents=True, exist_ok=True)
    return SANDBOX.resolve()


def normalize_workdir(raw: str) -> Path:
    """把用户填的路径收成一个已存在的绝对目录。不合法就抛 ValueError。

    空字符串不是这里的事：那是「回到默认 sandbox」，由调用方在进来之前
    就分流掉。相对路径拒绝——相对的是 server 进程的 cwd，用户在界面上
    看不到那个基准，写出来一定和自己想的不是同一个地方。
    """
    text = raw.strip()
    if not text:
        raise ValueError("工作目录不能为空。")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("工作目录要写绝对路径，例如 /Users/you/Documents/某项目。")
    try:
        path = path.resolve()
    except OSError as exc:
        raise ValueError(f"工作目录解析失败：{exc}") from exc
    if not path.is_dir():
        raise ValueError(f"不是已存在的目录：{path}")
    return path

# 子进程只给这一个环境变量：PATH 得留着（不然 python3、find 都找不到），
# 其余全部剥掉。原因：进程环境里可能有 LLM_API_KEY、代理地址这类值，
# 而工具输出会原样进模型上下文并存进 JSONL——一条 `env` 就把密钥送进
# 对话历史了。这不是沙箱（上面说了没有隔离），只是缩小波及面：
# 注入的命令读不到进程环境。副作用是 HOME/临时变量也没了，
# 一次性脚本基本不受影响。
CHILD_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
