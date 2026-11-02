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
import base64
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from application.sandbox import CHILD_ENV, SANDBOX
from domain.message import ImageBlock
from domain.tool import Tool
from infrastructure.config import load_mcp_servers
from infrastructure.mcp_client import MCPManager
# 单条命令的墙上时间上限。模型偶尔会提交 `cat`（没参数就等 stdin）或
# `python`（进交互解释器）这类「看起来在跑、其实永远不结束」的命令，
# 没有超时整轮对话就挂在它身上。
TIMEOUT_SECONDS = 10

# 后台任务工具（耐心）。import 放在这里而不是文件头：jobs.py 反过来要
# 本文件的 SANDBOX / CHILD_ENV，两边在文件头互相 import 会断——
# 这里那些常量已经定义完，环成立。REGISTRY 在下面引用 jobs 的函数。
# /memory 命令和 remember 工具共用的读写（2026-11-03 从 conversation.py 搬来，
# memory.md 是 sandbox 里的数据，归这里管；conversation 注入 system 时来读)：它是用户界面命令，
# 不是给模型的工具——模型自己写记忆走 bash（echo >> memory.md），
# 不需要也不应该经过工具注册表。两端界面（TUI / desktop 的 POST /memory）
# 共用这两个函数，保证写入格式只有一份。
MEMORY_PATH = SANDBOX / "memory.md"


def read_memory(path: Path | None = None) -> str:
    """读记忆文件全文（不存在返回空串）。/memory 不带参数时展示用。"""
    target = path if path is not None else MEMORY_PATH
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


def append_memory(note: str, author: str = "用户", path: Path | None = None) -> str:
    """把用户主动记下的一条追加进 memory.md，返回写入的那一行。

    模型写的记忆和用户写的记忆要能区分（模型读时会看到来源），
    所以加「用户 + 本地时间」前缀。模型自己 echo 的行没有这个标记。
    本地时间而不是 UTC：这是给人看的便签，不是给 trace 对账的。
    """
    target = path if path is not None else MEMORY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    clean_note = " ".join(note.split())
    line = f"- [{author} {datetime.now().strftime('%Y-%m-%d %H:%M')}] {clean_note}"
    # 文件可能没有结尾换行（模型 echo >> 时不会带）；先补一个再追加。
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    prefix = "" if not existing else "" if existing.endswith("\n") else "\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}{line}\n")
    return line


from application import jobs  # noqa: E402


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
            # 见 CHILD_ENV 的说明：不给进程环境，密钥不进工具输出。
            env=CHILD_ENV,
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


# ---------------------------------------------------------------------------
# screenshot（眼睛，2026-11-03）：截屏给模型看。
# ---------------------------------------------------------------------------

# 缩图上限（最长边像素）。全屏原始 PNG 一张几千 K、进上下文约 5k token；
# 缩到这个尺寸肉眼看布局够用，token 掉一个量级。想看得更细再调大。
SCREENSHOT_MAX_EDGE = 1000

# 截屏 + 缩图的墙上时间上限。screencapture 偶发卡在权限弹窗上，兜住。
SCREENSHOT_TIMEOUT_SECONDS = 15


def screenshot() -> tuple[str, ImageBlock]:
    """截一张全屏，返回 (文字说明, 图块)。

    流程：screencapture 落临时 PNG -> sips 缩到最长边 1000px ->
    再转 JPEG（质量 75）-> 读出来 base64 进 ImageBlock -> 临时文件删掉
    （截图不落盘，看过即弃；要留图的让模型自己用 bash 存）。
    转 JPEG 省的是载荷：PNG 一张 ~625KB，JPEG 同尺寸 ~100KB，
    JSONL 里的 base64 和请求体都小一个量级；上下文 token 按像素
    算（约 长×宽/750），和文件格式无关，缩图那步才省 token。
    macOS 专属命令，前提是「终端有屏幕录制权限」——没有时
    screencapture 会出一张黑的或直接报错，错误会变成 is_error
    结果交回模型。
    """
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "screen.png"
        jpg = Path(tmp) / "screen.jpg"
        subprocess.run(
            ["screencapture", "-x", str(png)],
            capture_output=True, text=True, timeout=SCREENSHOT_TIMEOUT_SECONDS,
            env=CHILD_ENV,
        )
        if not png.exists():
            raise ValueError("screencapture 没有产出文件（多半是没给终端屏幕录制权限）")
        # -Z 缩最长边、原文件就地改；先查尺寸只为在说明里报一声。
        subprocess.run(
            ["sips", "-Z", str(SCREENSHOT_MAX_EDGE), str(png)],
            capture_output=True, text=True, timeout=SCREENSHOT_TIMEOUT_SECONDS,
            env=CHILD_ENV,
        )
        # -s format jpeg + --out：格式转换必须给新文件名（就地转会让
        # 扩展名和内容打架）。formatOptions 75 是质量档，文字截图够清晰。
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "75",
             str(png), "--out", str(jpg)],
            capture_output=True, text=True, timeout=SCREENSHOT_TIMEOUT_SECONDS,
            env=CHILD_ENV,
        )
        if not jpg.exists():
            raise ValueError("sips 转 JPEG 失败")
        data = base64.b64encode(jpg.read_bytes()).decode("ascii")
    return "一张当前屏幕的截图（见附图）。", ImageBlock(media_type="image/jpeg", data=data)


def remember(text: str) -> str:
    """往跨会话记忆追加一条（remember 工具，D27）。

    auto 档自动放行——这是刻意的例外：bash 整体被拒时，模型唯一被
    预授权的写操作就是这一条受控追加（只进 memory.md、带作者时间戳、
    行为可审计）。无人值守的「存在」靠它把观察到的东西攒下来；
    权限模型上它等价于 /memory 命令，都是窄口子，不是开 bash。
    """
    if not text.strip():
        raise ValueError("要记的内容不能为空")
    line = append_memory(text, author="agent")
    return f"已记进跨会话记忆：{line}"


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
SCREENSHOT = Tool(
    name="screenshot",
    description=(
        "截取当前整个屏幕的一张图并交给模型看。任何「看看我屏幕上/现在"
        "界面上是什么」的需求都用它，不要凭空猜。截图会自动缩图，"
        "不落盘（临时文件即用即删）。macOS 需要「屏幕录制」权限。"
    ),
    input_schema={"type": "object", "properties": {}},
)

START_JOB = Tool(
    name="start_job",
    description=(
        "把一条 shell 命令放到后台跑，立刻返回任务 id（不等待）。"
        "预计超过 10 秒的活用这个（bash 工具 10 秒就超时杀掉）：装依赖、"
        "跑测试、批量文件操作。跑起来后用 wait_job 等它、job_status 查进度、"
        "stop_job 停掉；期间也可以先干别的。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要跑的命令，同 bash 的语法"}
        },
        "required": ["command"],
    },
)

WAIT_JOB = Tool(
    name="wait_job",
    description=(
        "等一个后台任务结束。seconds 是这次最多等的秒数（默认 30、上限 120）；"
        "超时不是失败——任务还在跑，返回它目前的输出，可以再等一次或先干别的。"
        "没别的可干时就用它，比反复 job_status 省轮数。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "start_job 返回的任务 id"},
            "seconds": {"type": "number", "description": "这次最多等多少秒，默认 30"},
        },
        "required": ["job_id"],
    },
)

JOB_STATUS = Tool(
    name="job_status",
    description=(
        "查一个后台任务的状态（还在跑 / 已结束，exit 码）和目前的输出，"
        "不等待。每次查询算一轮工具调用——急着要结果就用 wait_job。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "start_job 返回的任务 id"}
        },
        "required": ["job_id"],
    },
)

STOP_JOB = Tool(
    name="stop_job",
    description=(
        "停掉一个后台任务（连它的子进程一起）。任务跑偏了、不要了、"
        "或者用户喊停的时候用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "start_job 返回的任务 id"}
        },
        "required": ["job_id"],
    },
)

REMEMBER = Tool(
    name="remember",
    description=(
        "把一条结论写进你的跨会话记忆（memory.md，下次会话仍然看得见）。"
        "只记值得长期记住的事实和偏好，一次一条，写清来龙去脉。"
        "它只追加、不能改写别的记忆。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要记住的内容，一句话说完整（会带时间戳存档）",
            }
        },
        "required": ["text"],
    },
)

REGISTRY: dict[str, tuple[Tool, object]] = {
    NOW_TIME.name: (NOW_TIME, now_time),
    BASH.name: (BASH, bash),
    SCREENSHOT.name: (SCREENSHOT, screenshot),
    REMEMBER.name: (REMEMBER, remember),
    START_JOB.name: (START_JOB, jobs.start_job),
    WAIT_JOB.name: (WAIT_JOB, jobs.wait_job),
    JOB_STATUS.name: (JOB_STATUS, jobs.job_status),
    STOP_JOB.name: (STOP_JOB, jobs.stop_job),
}

# ---------------------------------------------------------------------------
# Skills（第 6 层）：按需加载的指令包。
# ---------------------------------------------------------------------------

# 技能住在 skills/ 目录（main.py 把工作目录钉在 server/，所以就是
# server/skills/）。一个技能一个 .md 文件，头部是两行「键: 值」的
# 简化 frontmatter，其余全是正文：
#
#     ---
#     name: report
#     description: 运行报告的固定格式和落款
#     ---
#     正文：教模型这类任务怎么做，怎么写、怎么验收……
#
# 常驻上下文的只有「名字 + 说明」（挂在 read_skill 工具的 description 里，
# 随工具定义每次重申）；正文要模型自己调 read_skill 拉进来——读进来是
# 普通的 tool_result，长会话里会被第 4 层的压缩自然收走，不占常驻预算。
# 正文里可以引用同目录的脚本（「跑 scripts/foo.py」），执行走 bash，
# 技能机制本身不碰执行——和 MCP 的边界一致：加载归加载，执行归工具。
SKILLS_DIR = Path("skills")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆简化 frontmatter：首行 --- 到下一个 --- 之间按「键: 值」收，剩下是正文。

    不引 YAML 库：技能头部只有两个字符串键，手写三行解析比引依赖诚实。
    没有 frontmatter 也接受——name 用文件名，description 为空。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1:]).strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    # 只有开头没有闭合：当没有 frontmatter 处理，整段还给正文。
    return {}, text


def _load_skills() -> list[tuple[str, str, Path]]:
    """扫描技能目录。目录不存在（还没写过技能）返回空列表。

    每圈 all_tools() 都重扫：加技能 = 丢一个 .md 文件进来，不用改代码、
    不用重启——和 MCP「加配置即得工具」是同一个设计取向。
    """
    if not SKILLS_DIR.is_dir():
        return []
    skills: list[tuple[str, str, Path]] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = meta.get("name") or path.stem
        skills.append((name, meta.get("description", ""), path))
    return skills


def read_skill(name: str) -> str:
    """读取一个技能的正文。不存在时抛 ValueError（run_tool 转成 is_error）。"""
    for skill_name, _, path in _load_skills():
        if skill_name == name:
            body = _parse_frontmatter(path.read_text(encoding="utf-8"))[1]
            return body or "（技能正文为空）"
    available = ", ".join(n for n, _, _ in _load_skills()) or "（无）"
    raise ValueError(f"没有叫 {name} 的技能。可用：{available}")


def _skill_tool() -> Tool:
    """read_skill 的说明书。技能索引就嵌在这里——它是 Skills 唯一的
    常驻上下文开销，每个技能一行，说明写得越准模型选得越对。"""
    index = "\n".join(
        f"- {name}：{description or '（无说明）'}" for name, description, _ in _load_skills()
    ) or "（暂无技能）"
    return Tool(
        name="read_skill",
        description=(
            "读取一个技能的详细指令。技能教某类任务怎么做（固定格式、"
            "项目约定、操作流程）。任务匹配某个技能的说明时，先读它再动手。\n"
            f"可用技能：\n{index}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名，见上面的清单"}
            },
            "required": ["name"],
        },
    )


# MCP 工具的进程级登记处（第 5 层）。惰性建：第一次 all_tools() 时读
# config.ini 的 [mcp:*] 段、起子进程。没有配置段 = 没有外部工具，
# 和以前的行为完全一样。
_mcp: MCPManager | None = None


def _mcp_manager() -> MCPManager:
    global _mcp
    if _mcp is None:
        _mcp = MCPManager(load_mcp_servers())
    return _mcp


def _mcp_tool_name(alias: str, tool_name: str) -> str:
    """外部工具的全名：别名__工具名。内置工具名永远不含 __，
    路由不会撞（run_tool 先查 REGISTRY 再按前缀分流）。"""
    return f"{alias}__{tool_name}"


def all_tools() -> list[Tool]:
    """全部工具的定义（内置 + MCP）。Conversation 每次请求模型随请求带过去。

    MCP 工具的 inputSchema 就是 JSON Schema，和 domain.Tool 的
    input_schema 同一个形状，零翻译。单个服务器起不来就跳过——
    模型至少还有内置工具，坏服务器的错误会在它被点名调用时报出来。
    """
    tools = [tool for tool, _ in REGISTRY.values()]
    # read_skill 动态生成：技能索引（名字+说明）随工具说明走，
    # 加了新技能文件，下一次请求的说明里就有它。
    tools.append(_skill_tool())
    for alias, entry in _mcp_manager().list_tools():
        tools.append(Tool(
            name=_mcp_tool_name(alias, entry["name"]),
            description=entry.get("description") or "",
            input_schema=entry.get("inputSchema") or {"type": "object", "properties": {}},
        ))
    return tools


def run_tool(name: str, args: dict) -> tuple[str, bool, ImageBlock | None]:
    """按名字执行一个工具，返回 (结果文本, 是否出错, 附带图片)。永不抛异常。

    出错也返回字符串（is_error=True）交回模型，而不是把异常抛出去打断
    整轮对话——模型看到错误描述能自己换路子（第 3 层专门练这个）。
    第三个值是眼睛（2026-11-03）：screenshot 这类工具除了文字还带一张图，
    纯文本工具永远是 None。span 记录不在这里做：那是 Conversation
    （知道 turn 上下文）的职责。
    """
    entry = REGISTRY.get(name)
    if entry is not None:
        _, func = entry
        try:
            # **args 把模型给的字典展开成关键字参数：{"command": "..."}
            # 正好对上 bash(command=...)。给错参数名会 TypeError，落进 except。
            result = func(**args)
            # 截图类工具返回 (文字, ImageBlock)，纯文本工具直接返回值。
            if isinstance(result, tuple):
                text, image = result
                return str(text), False, image
            return str(result), False, None
        except Exception as e:
            return f"工具执行出错: {e}", True, None
    # read_skill 不是 REGISTRY 里的静态条目（说明书每圈动态生成），
    # 在这里单独分派，行为和内置工具一致：出错转 is_error。
    if name == "read_skill":
        try:
            return read_skill(**args), False, None
        except Exception as e:
            return f"工具执行出错: {e}", True, None
    # 不在内置注册表：试试 MCP 的复合名（别名__工具名）。
    if "__" in name:
        alias, _, tool_name = name.partition("__")
        text, is_error = _mcp_manager().call(alias, tool_name, args)
        return text, is_error, None
    # 模型幻觉出了一个不存在的工具名。如实告诉它有哪些可用，让它调整。
    available = [t.name for t in all_tools()]
    return f"没有叫 {name} 的工具。可用：{', '.join(available)}", True, None
