"""给前端用的 HTTP 入口。

前端是 app/ 里的 Tauri 应用，跑在另一个来源上，通过这个接口来用模型。
只做输入输出：请求进 -> Conversation 跑一轮 -> 片段以 SSE 推出去。
编排（落盘、历史、日志）全在 application/conversation.py。

POST /chat
    请求体：{"question": "...", "session_id": "..."}
    session_id 为空就新开一个会话。
    响应是 text/event-stream，每条事件一个 JSON 对象：
        {"kind": "thinking" | "text", "text": "..."}     流式片段
        {"kind": "tool_use", "id": "...", "name": "...", "input": {...}}
            模型要调工具，执行前就到。id 是这一次调用的标识，input 是解析好的参数
        {"kind": "tool_approval", "id": "...", "approval_id": "...",
         "name": "...", "mode": "approve"}
            当前执行等级需要人工批准。前端 POST /approval 回填后才继续。
        {"kind": "tool_result", "id": "...", "name": "...", "output": "...",
         "is_error": false, "denied": false, "elapsed_ms": 1}
            工具跑完。id 和对应的 tool_use 相同：一次回复里并行多个工具时，
            前端靠 id 把结果填回各自的卡片，不能靠事件顺序猜。
            elapsed_ms 与 `main.py trace` 回放的耗时是同一个数
    全部推完再补一条：
        {"kind": "done", "stop_reason": "...", "session_id": "..."}
    中途出错则补一条（200 已发出，改不了状态码，只能用事件报错）：
        data: {"kind": "error", "message": "..."}
    前端拿到 session_id 后保存，下次请求带上，就是在续同一次对话。
    session_id 不是 UUID 时，端点直接返回 422，不开始推流。

    请求体还可以带 "steering"（默认 "queue"），决定同会话连发消息怎么处理：
        queue        等当前轮跑完再跑（本连接正常流式输出自己的轮）
        cooperative  消息并入当前轮（下一个请求间隙生效）；本连接立刻收到
                     {"kind": "steering_ack", ...} 和 done(stop_reason="injected")，
                     真正的回应在当前轮的流上（steering_injected 事件之后）
        preemptive   抢占当前轮：当前轮的连接收到 {"kind": "steering_cancelled"}
                     和 done(stop_reason="cancelled")，半截回答不落盘；
                     本连接等它退场后正常跑自己的轮
    没有轮次在跑时三档等价，直接开跑。档位的等待颗粒度和线程模型见
    application/session_manager.py。

GET /settings
    返回可展示的模型设置和密钥来源，不包含真实密钥。

PUT /settings
    写入 config.ini 的 [llm] 已知键；api_key 为空表示保留原值。
    保存后清掉配置缓存，下一轮模型请求使用新配置。

POST /approval
    请求体：{"approval_id": "...", "approved": true}
    tool_approval 事件的人工决定；审批过期或重复提交返回 404。

POST /approval-mode
    请求体：{"session_id": "...", "mode": "approve|auto|yolo"}
    运行中实时切换执行等级，立即影响这一轮后续工具调用；如果正在等待的
    审批在新等级下已不需要人工确认，服务端会直接放行并返回 auto_approved。
    没有运行轮时暂存到 session 槽位，下一次 begin 时采用。
"""
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from domain.message import ImageBlock
from application.approval import APPROVAL_MODES, ApprovalBroker
from application.conversation import Conversation, TurnCancelled
from application.jobs import restore_jobs
from application.tools import (
    REGISTRY,
    SANDBOX,
    _load_skills,
    _mcp_manager,
    all_tools,
    append_memory,
    read_memory,
)
from application.proactivity import ProactivityRunner, UNATTENDED_MAX_TOOL_ROUNDS
from application.session_manager import MODES, SessionManager
from application.trace_view import answers_for_requests, answer_texts
from infrastructure.config import (
    LLMConfig,
    SettingsValues,
    load_config,
    load_settings,
    load_triggers,
    save_settings,
)
from infrastructure.llm import build_chat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

# 数据目录固定在 server/data。相对路径由 main.py 启动时把工作目录
# 钉在 server/ 来锚定（见 main.py 的说明）。
DATA = Path("data")

# 配置惰性加载：import 本模块不触发读文件。main.py 会按模式分别 import
# TUI 或 desktop 实现；模块级读配置会让 TUI 启动也受 HTTP 服务的配置影响，
# 还会让不配 config.ini 就跑测试变得不可能。
_cached_config: LLMConfig | None = None


def get_config() -> LLMConfig:
    """读一次配置，之后缓存复用。第一次调用发生在 desktop 启动或设置保存后。"""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


# 主动性调度线程的把手（D27）：desktop server 常驻时才有意义——TUI 关了
# 进程就没了，「不在场也延续」需要一直开着的进程。没配 [trigger:*] 段
# 时 specs 为空、线程每 5 秒空醒一次，行为和以前没有差别。
# lifespan 是 FastAPI 的启停钩子（on_event 已弃用）：yield 前是启动，
# 后是收尾。TUI 模式不 import 本模块，天然不会起这个线程。
_proactivity: ProactivityRunner | None = None


@asynccontextmanager
async def _lifespan(_: FastAPI):
    global _proactivity
    restore_jobs()
    _proactivity = ProactivityRunner(load_triggers(), _run_unattended)
    _proactivity.start()
    yield
    _proactivity.stop()


# FastAPI 应用本身。路由都登记在它上面。
app = FastAPI(lifespan=_lifespan)
# 前端的页面和这个服务不在同一个地址上，浏览器会先发一个预检请求问允不允许。
# 不加这几行，浏览器里的请求会被直接拦掉（从终端里发则不受影响）。
app.add_middleware(
    CORSMiddleware,
    # 只放行真正会发起请求的来源。
    # localhost:1420 是 tauri dev 时 vite 的地址，另外两个是 Tauri 窗口自己的来源。
    # 用 "*" 的话，任意网页都能在浏览器里向这个服务发请求，调用你的模型和工具。
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


# 进程级单例三件：
# - Store 一条连接全进程复用（原来每个请求各建一个）。省的只是建连接开销，
#   SQLite 写锁本来就是库级的；线程安全靠 Store 自己的锁（见 store.py）。
# - SessionManager 是 steering 的裁判，状态天生就是跨请求的。
# - Transcript 无状态（纯文件读写），单例只为少造对象。
_store = Store(DATA / "agent.db")
_transcript = Transcript(DATA / "sessions")
_manager = SessionManager()
_approval = ApprovalBroker()


def _run_unattended(session_id: str, message: str, trigger_name: str) -> dict:
    """跑一轮无人值守对话（D27）：主动性触发器扣扳机后的执行端。

    和 /chat 的差别只有三件事，件件都是无人值守的代价：
    - 没有 SSE 消费者——事件直接丢弃，落盘（JSONL + spans）就是全部
      输出，人回来后靠 /resume、trace 和 data/notices.log 看发生了什么；
    - 权限地板钉死 auto——bash / start_job 这些副作用工具全被拒，
      remember 是唯一被预授权的写（D27 第一阶段，容器化前不放宽）；
    - 会话锁拿不到就放弃这一 tick（run_turn 返回 ran=false），
      不排队等人——调度线程被一个长轮次挂住，所有触发器都停摆。
    返回 {"ran", "stop_reason", "note", "token_count"} 给调度器记账；
    token_count 是本轮全部成功模型请求的 usage 实数总和，供预算熔断使用。
    """
    slot = _manager.slot(session_id)
    # acquire(timeout=0)：会话正被人用（或上一轮还没收尾）就立刻让路。
    if not slot.turn_lock.acquire(timeout=0):
        return {"ran": False, "note": "session busy"}
    try:
        handle = slot.begin("auto")
        try:
            conv = Conversation(
                build_chat(get_config()),
                _transcript,
                _store,
                session_id,
                steering=handle,
                approval=_approval,
                approval_mode="auto",
                max_tool_rounds=UNATTENDED_MAX_TOOL_ROUNDS,
            )
        except Exception as exc:  # 建客户端失败（如代理配置坏），这轮作废
            handle.close()
            return {"ran": False, "note": f"build client: {exc}"}
        try:
            for _event_ in conv.turn(message, origin=f"trigger:{trigger_name}"):
                pass  # 丢弃事件：见 docstring
            return {
                "ran": True,
                "stop_reason": conv.stop_reason or "?",
                "token_count": conv.turn_usage_tokens,
            }
        except TurnCancelled:
            # 无人值守轮也可能被 POST /stop 打断（人有优先权，任何时候）。
            return {
                "ran": True,
                "stop_reason": "cancelled",
                "token_count": conv.turn_usage_tokens,
            }
        except Exception as exc:
            return {
                "ran": False,
                "note": str(exc),
                "token_count": conv.turn_usage_tokens,
            }
        finally:
            handle.close()
    finally:
        slot.turn_lock.release()




# 抢占者等旧轮退场的上限。正常情况旧轮在下一个检查点就退了；这个值
# 只防厂商请求挂死时把新请求一起拖死。
_PREEMPT_WAIT_SECONDS = 120


class ChatRequest(BaseModel):
    """POST /chat 的请求体。FastAPI 按这个定义解析 JSON。"""

    # 用户这一轮的问题。
    question: str
    # 空字符串表示新会话。前端续接时把上次拿到的 id 传回来。
    # 不是 UUID 的值由端点校验并返回 422（见下面的 chat 函数）。
    session_id: str = ""
    # 同会话连发消息时的处理档位（见 session_manager.py）：
    #   queue（默认，等当前轮跑完）/ cooperative（下一个请求间隙并入当前轮）
    #   / preemptive（抢占当前轮，半截回答不落盘）。
    # 没有轮次在跑时三档等价：直接开跑。
    steering: str = "queue"
    # approve / auto / yolo，见 application/approval.py。默认最保守。
    approval_mode: str = "approve"
    # 附图（@file / 粘贴图片的后端半边）：每项 {"media_type": "image/jpeg",
    # "data": "<base64>"}，追加进这条 user 消息的图块（文前图后）。
    # 与 retry 互斥（重试不落新消息）；只支持 queue 档——cooperative
    # 的 inbox 只搬文字。
    images: list[dict] = []
    # 重试上一轮（P0「重试去重」契约）：失败的轮次已经把 user 消息落盘，
    # 重试不再追加一条同文 user，对既有历史重新发起请求（question 传空串）。
    # 历史尾部必须是 user 消息（纯文本或 tool_result——后者意味着从工具
    # 循环的中断点续跑），否则 409；只支持 queue 档（重试不该再插话/抢占）。
    retry: bool = False


class SettingsRequest(BaseModel):
    """PUT /settings 的请求体。api_key 为空时保留现有密钥。"""

    provider: str
    base_url: str
    model_id: str
    max_tokens: int
    effort: str = ""
    api_key: str | None = None


class ApprovalRequest(BaseModel):
    """一次 tool_approval 的人工决定。"""

    approval_id: str
    approved: bool


class ApprovalModeRequest(BaseModel):
    """运行中切换执行等级；与一次具体审批的回填分开。"""

    session_id: str
    mode: str


class MemoryRequest(BaseModel):
    """POST /memory 的请求体：/memory 命令带的那句话。"""

    text: str


class StopRequest(BaseModel):
    """POST /stop 的请求体：要停的是哪个会话的当前轮。"""

    session_id: str


class SessionPatchRequest(BaseModel):
    """PATCH /sessions/{id} 的请求体：rename 和 archive 是两个可选字段。

    归档（软删除）走这里；永久删除是独立的 DELETE——两个动作不同动词，
    前端才不会把「收进_archive」和「彻底没了」混在一个按钮里。
    """

    title: str | None = None
    archived: bool | None = None
    pinned: bool | None = None


class SessionForkRequest(BaseModel):
    """POST /sessions/{id}/fork 的请求体：保留源会话的前几条消息。"""

    through: int


@app.get("/settings")
def get_settings():
    """返回可展示的设置；真实密钥永远不出现在响应里。"""
    return asdict(load_settings())


@app.put("/settings")
def put_settings(request: SettingsRequest):
    """保存设置，并让下一次模型请求使用新配置。"""
    global _cached_config
    try:
        snapshot = save_settings(SettingsValues(**request.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _cached_config = None
    get_config()
    return asdict(snapshot)


@app.get("/memory")
def get_memory():
    """读跨会话记忆全文。desktop 的 /memory（不带参数）展示用。"""
    return {"text": read_memory()}


@app.post("/memory")
def post_memory(request: MemoryRequest):
    """用户主动记一条进 memory.md（/memory 命令），下一圈请求就生效。

    不等当前轮：记忆是每圈圈界重读的，追加只影响之后发出的请求，
    和正在飞的那一个没有竞态。
    """
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="要记的内容不能为空。")
    line = append_memory(request.text)
    return {"ok": True, "line": line}


# ---------------------------------------------------------------------------
# 会话索引与回放（前端接线需求，2026-11-03）。desktop 不再拿 localStorage
# 当唯一会话索引：服务端有全部事实（SQLite 索引 + JSONL 正文），这些端点
# 只是把它暴露出去。形状和 TODO「前端对后端的接线需求」里的契约一致，
# 前端按契约接线，改这里要同步改契约。
# ---------------------------------------------------------------------------


def _validated_session(session_id: str) -> str:
    """校验 session_id（UUID + 防路径穿越）并确认已登记，返回原值。

    不在索引里的会话（前端编的、打错的）返回 404 而不是 422：
    格式问题是客户端的错，存在性问题是资源没了。
    """
    try:
        _transcript.path_for(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not _store.has_session(session_id):
        raise HTTPException(status_code=404, detail="没有这个会话。")
    return session_id


@app.get("/sessions")
def get_sessions(archived: bool = False):
    """按最近活动倒序的会话索引。desktop 的会话轨数据源。

    archived=true 是归档视图（软删除的会话，可恢复）；默认只给活跃的。
    """
    return [
        {
            "id": row["id"],
            "title": row["title"] or "",
            "model": row["model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "turns": row["turns"],
            "stop_reason": row["stop_reason"] or "",
            "archived": bool(row["archived"]),
            "pinned": bool(row["pinned"]),
        }
        for row in _store.list_sessions(limit=100, include_archived=archived)
    ]


@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """一个会话的完整内容块（首屏恢复用）。JSONL 仍是唯一事实来源。"""
    _validated_session(session_id)
    return [message.to_dict() for message in _transcript.read(session_id)]


@app.post("/stop")
def post_stop(request: StopRequest):
    """停掉当前运行轮：设取消标志，轮在下一个检查点收线。

    沿用抢占档的 TurnCancelled 语义：已完成的 tool_result 保留、
    不留半截 assistant 正文，SSE 会收到 steering_cancelled + done。
    没有运行轮时是幂等 no-op（active=false），前端不用先问「在跑吗」。
    """
    try:
        _transcript.path_for(request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    running = _manager.slot(request.session_id).current()
    if running is None:
        return {"ok": True, "active": False, "cancelled": False}
    running.cancel.set()
    return {"ok": True, "active": True, "cancelled": True}


@app.get("/sessions/{session_id}/trace")
def get_session_trace(session_id: str):
    """spans 树 + 按顺序配对的 assistant 回答（只读 trace 面板用）。"""
    _validated_session(session_id)
    spans = []
    for row in _store.spans_for(session_id):
        spans.append(
            {
                "span_id": row["span_id"],
                "parent_id": row["parent_id"],
                "name": row["name"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "status": row["status"],
                # data 是 JSON 字符串（存库形状），出站解析回对象。
                "data": json.loads(row["data"]) if row["data"] else {},
                "duration_ms": _store.span_duration_ms(row["span_id"]),
            }
        )
    messages = _transcript.read(session_id)
    paired = answers_for_requests(spans, answer_texts(messages))
    return {"spans": spans, "answers": paired}


def _session_row(session_id: str) -> dict | None:
    """把一行索引翻成契约形状；不在索引里返回 None。"""
    for row in _store.list_sessions(limit=1000, include_archived=True):
        if row["id"] == session_id:
            return {
                "id": row["id"],
                "title": row["title"] or "",
                "model": row["model"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "turns": row["turns"],
                "stop_reason": row["stop_reason"] or "",
                "archived": bool(row["archived"]),
                "pinned": bool(row["pinned"]),
            }
    return None


@app.patch("/sessions/{session_id}")
def patch_session(session_id: str, request: SessionPatchRequest):
    """rename / 归档 / 取消归档。返回更新后的整行。"""
    _validated_session(session_id)
    if request.title is None and request.archived is None and request.pinned is None:
        raise HTTPException(status_code=422, detail="没给要改的字段（title / archived / pinned）。")
    if request.title is not None:
        clean = " ".join(request.title.split())
        if not clean:
            raise HTTPException(status_code=422, detail="标题不能为空。")
        if not _store.rename_session(session_id, clean):
            raise HTTPException(status_code=404, detail="没有这个会话。")
    if request.archived is not None:
        if not _store.set_archived(session_id, request.archived):
            raise HTTPException(status_code=404, detail="没有这个会话。")
    if request.pinned is not None:
        if not _store.set_pinned(session_id, request.pinned):
            raise HTTPException(status_code=404, detail="没有这个会话。")
    return _session_row(session_id)


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """永久删除：索引、spans、JSONL 正文、压缩摘要一起没。

    和归档（PATCH archived=true）是两个动作，故意不共用动词。
    有轮次在跑时拒绝（409）：正在追加的正文文件被删掉，那轮的落盘
    会把文件重新建出来，留下一个索引里没有、磁盘上存在的幽灵会话。
    """
    _validated_session(session_id)
    if _manager.slot(session_id).current() is not None:
        raise HTTPException(status_code=409, detail="这个会话有轮次在跑，先停掉再删。")
    _store.delete_session(session_id)
    _transcript.delete(session_id)
    return {"ok": True}


@app.post("/sessions/{session_id}/fork")
def fork_session(session_id: str, request: SessionForkRequest):
    """从指定消息点复制出新会话：保留前 through 条，返回新 id。

    spans 不复制——trace 是「这次运行怎么跑出来的」，分支的树从
    第一轮 turn 重新长。新会话标题加「（分支）」后缀，好认也随时可改。
    """
    source = _validated_session(session_id)
    messages = _transcript.read(source)
    if request.through < 1 or request.through > len(messages):
        raise HTTPException(
            status_code=422,
            detail=f"through 要在 1..{len(messages)} 之间（消息总数 {len(messages)}）。",
        )
    row = _session_row(source) or {}
    new_id = str(uuid.uuid4())
    _store.create_session(
        new_id, row.get("model", ""), str(_transcript.path_for(new_id)),
        title=(row.get("title") or "") + "（分支）",
    )
    for message in messages[: request.through]:
        _transcript.append(new_id, message)
    return {"ok": True, "session_id": new_id, "copied": request.through}


def _report_conversation(session_id: str) -> Conversation:
    """给 /context、/compact 组装一个「只为读写而存在」的 Conversation。

    和 /chat 同一套依赖（单例 + 当前配置）；这两个端点不跑对话，
    只是借 Conversation 对摘要/历史的封装。构造失败（配置坏）让
    FastAPI 转 500——对只读工具来说，配置坏了就是服务坏了。
    """
    return Conversation(
        build_chat(get_config()), _transcript, _store, session_id
    )


@app.get("/capabilities")
def get_capabilities():
    """运行时能力面板（P1）：MCP 健康、工具清单、环境边界、版本诊断。

    TUI /status 与 desktop RUN LOG 共用的语义就是这份 JSON。
    全部只读、不启动任何进程；安全相关的边界如实陈述（D17 的取舍
    是「已知且接受」，面板不该粉饰成「有沙箱」）。
    """
    config = get_config()
    builtin = {tool.name for tool, _ in REGISTRY.values()}
    mcp_tools = _mcp_manager().health()
    skills = [
        {"name": name, "description": description}
        for name, description, _ in _load_skills()
    ]
    return {
        "tools": [
            {
                "name": tool.name,
                "source": "builtin" if tool.name in builtin
                          else "mcp" if "__" in tool.name
                          else "skill",
                "description": tool.description.split("\n")[0][:120],
            }
            for tool in all_tools()
        ],
        "mcp": mcp_tools,
        "skills": skills,
        "environment": {
            "cwd": str(Path.cwd()),
            "sandbox": str(SANDBOX),
            "sandbox_boundary": (
                "工作目录起跑点，不是安全边界：绝对路径和 ../ 都能绕过"
                "（D17 已接受的取舍，容器化前不放宽）"
            ),
            "memory_file": str(SANDBOX / "memory.md"),
        },
        "runtime": {
            "python": sys.version.split()[0],
            "provider": config.provider,
            "model": config.model_id,
            "effort": config.effort or "",
            "triggers": len(load_triggers()),
        },
    }


@app.get("/sessions/search")
def search_sessions(q: str):
    """按内容搜会话（P1 会话库）：线性扫 JSONL 的文字块。

    没上 SQLite FTS：几十个会话的正文全读一遍是毫秒级，FTS 的索引
    维护（每次 append 都要更新）比线性扫描贵得多——和「不上连接池」
    同一个账。会话多了真慢再迁移，正文反正要按需读。
    返回每会话至多一条命中（第一处 + 前后文截断），总量封顶 50。
    """
    needle = q.strip()
    if not needle:
        raise HTTPException(status_code=422, detail="搜索词不能为空。")
    hits: list[dict] = []
    for row in _store.list_sessions(limit=1000, include_archived=True):
        try:
            messages = _transcript.read(row["id"])
        except Exception:
            continue
        for index, message in enumerate(messages):
            text = "".join(
                b.text for b in message.content
                if b.type == "text" and b.text
            )
            pos = text.find(needle)
            if pos < 0:
                continue
            start = max(0, pos - 40)
            snippet = text[start: pos + 60].replace("\n", " ")
            hits.append({
                "session_id": row["id"],
                "title": row["title"] or "",
                "message_index": index,
                "role": message.role,
                "snippet": ("…" if start > 0 else "") + snippet + "…",
            })
            break  # 每会话只报第一处命中，够认出是哪个会话
        if len(hits) >= 50:
            break
    return {"q": needle, "hits": hits}


@app.get("/sessions/{session_id}/context")
def get_session_context(session_id: str):
    """上下文构成（P1「上下文与用量」）：估算 + 上次请求的实数。

    不跑模型、不改状态；有轮次在跑时给出的快照可能落后半轮，无妨——
    这是仪表读数不是账本。
    """
    _validated_session(session_id)
    return _report_conversation(session_id).context_report()


@app.post("/sessions/{session_id}/compact")
def post_session_compact(session_id: str):
    """手动压缩（/compact）：不等预算，立刻把能压的压掉。

    会花一次模型请求（摘要续写）。有轮次在跑时拒绝（409）：摘要文件
    会被运行轮的圈界压缩和这里互相踩——两边都读旧摘要、写新摘要，
    后写的赢，先写的那次压缩白花钱还可能丢内容。
    """
    _validated_session(session_id)
    if _manager.slot(session_id).current() is not None:
        raise HTTPException(status_code=409, detail="这个会话有轮次在跑，结束后再压缩。")
    result = _report_conversation(session_id).compact_now()
    return {"ok": True, **result}


@app.post("/approval")
def approve_tool(request: ApprovalRequest):
    """回填 TUI/desktop 的执行审批；幂等性由 broker 的 pending 表负责。"""
    if not _approval.decide(request.approval_id, request.approved):
        raise HTTPException(status_code=404, detail="审批已过期、取消或已处理。")
    return {"ok": True}


@app.post("/approval-mode")
def set_approval_mode(request: ApprovalModeRequest):
    """实时切换当前轮的执行等级；没有运行轮时只确认下一轮可用的值。"""
    if request.mode not in APPROVAL_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode 只能是 {APPROVAL_MODES} 之一。",
        )
    try:
        _transcript.path_for(request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    slot = _manager.slot(request.session_id)
    running, approval_ids = slot.set_approval_mode(request.mode)
    if running is None:
        return {
            "ok": True,
            "active": False,
            "mode": request.mode,
            "auto_approved": [],
        }

    auto_approved = _approval.auto_approve_if_allowed(approval_ids, request.mode)
    return {
        "ok": True,
        "active": True,
        "mode": request.mode,
        "auto_approved": auto_approved,
    }


def chat_events(
    question: str,
    session_id: str,
    steering: str,
    approval_mode: str,
    retry: bool = False,
    images: list[dict] | None = None,
):
    """产生 SSE 的每一条。这是一个生成器。

    编排（登记会话、落盘、历史、日志）都在 Conversation 里，
    这里做两件事：把 turn() 产出的事件原样推进 SSE，以及按 steering
    档位决定这条请求和「正在跑的那轮」怎么相处（档位语义见
    session_manager.py）。这个会话没有轮次在跑时，三档殊途同归：
    都落到下面的 turn_lock 里跑自己的一整轮。
    """
    # 没带 session_id 就是新会话，分配一个 UUID。
    if not session_id:
        session_id = str(uuid.uuid4())
    slot = _manager.slot(session_id)

    # 软中断：有轮次在跑且消息成功入队 → 并入当前轮，本连接只拿 ack。
    # 消息的真正回应出现在当前轮的流上（steering_injected 事件之后），
    # 这条连接的 done 是 injected，前端不应该再在这条连接上等回答。
    if steering == "cooperative":
        running = slot.current()
        if running is not None and running.inject(question):
            yield _event({"kind": "steering_ack", "mode": "cooperative", "text": question})
            yield _event({"kind": "done", "stop_reason": "injected", "session_id": session_id})
            return

    # 抢占：通知当前轮退场（它在下一个检查点收尾、span 记 cancelled），
    # 等它交出会话锁，然后自己跑。
    if steering == "preemptive":
        running = slot.current()
        if running is not None:
            running.cancel.set()
            running.done.wait(timeout=_PREEMPT_WAIT_SECONDS)

    # 到这里都是「跑自己的一整轮」：queue 直接等锁；抢占者等旧轮退场后
    # 拿锁；软中断竞争失败（旧轮恰好收尾，inject 返回 False）也落到这里。
    # 先来后到是近似的：threading.Lock 不保证 FIFO。
    with slot.turn_lock:
        handle = slot.begin(approval_mode)
        try:
            # 构造放在 try 里：建客户端可能失败（典型是本机 SOCKS 代理缺
            # socksio），让它变成一条 error 事件推给前端，而不是连接中途断掉。
            conv = Conversation(
                build_chat(get_config()),
                _transcript,
                _store,
                session_id,
                steering=handle,
                approval=_approval,
                approval_mode=approval_mode,
            )
            try:
                blocks = [
                    ImageBlock(media_type=i["media_type"], data=i["data"])
                    for i in (images or [])
                ]
                for event in conv.turn(
                    question,
                    origin="retry" if retry else "",
                    append_user=not retry,
                    images=blocks,
                ):
                    # 每个事件立刻作为一条 SSE 推出去。
                    yield _event(event)
            except TurnCancelled:
                # 被后来的抢占者打断：如实告诉这条连接的消费者，然后收线。
                yield _event({"kind": "steering_cancelled"})
                yield _event({"kind": "done", "stop_reason": "cancelled", "session_id": session_id})
                return
            except Exception as e:
                # 200 已经发出，没法再给状态码，只能用事件告诉前端出错了。
                # error 日志已由 Conversation 记过。
                yield _event({"kind": "error", "message": str(e)})
                return
            # 最后一条事件告诉前端：结束了、为什么结束、这次会话的 id 是什么。
            yield _event({"kind": "done", "stop_reason": conv.stop_reason, "session_id": session_id})
        finally:
            # 无论正常、出错、被打断还是客户端断开，槽位一定交还——
            # 否则这个会话从此再也跑不了新一轮。
            slot.finish()


def _event(payload: dict) -> str:
    """把一个字典格式化成 SSE 的一条。

    SSE 规定一条事件以 data: 开头，以一个空行结尾，所以这里是两个换行。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """POST /chat 的处理函数。请求体自动按 ChatRequest 解析。"""
    # 校验必须在构造 StreamingResponse 之前：SSE 一旦开始，200 已经发出去，
    # 再抛错客户端只会看到连接断掉，分不清「答完了」和「崩了」。
    # chat_events 是生成器，校验放在它里面就晚了，必须放在这里。
    if request.session_id:
        try:
            _transcript.path_for(request.session_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    # steering 档位同样推流前校验：白名单之外直接 422。
    if request.steering not in MODES:
        raise HTTPException(status_code=422, detail=f"steering 只能是 {MODES} 之一。")
    if request.approval_mode not in APPROVAL_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"approval_mode 只能是 {APPROVAL_MODES} 之一。",
        )
    # 重试的前置校验也推流前做：可重试是会话当前的状态，不是请求格式，
    # 状态不满足用 409（见 _validated_session 的 404/422 之分）。
    if request.images and request.retry:
        raise HTTPException(status_code=422, detail="重试不落新消息，不能带图。")
    if request.images and request.steering != "queue":
        raise HTTPException(
            status_code=422, detail="带图的输入只支持 queue 档（软中断只搬文字）。"
        )
    for image in request.images:
        if not image.get("media_type", "").startswith("image/"):
            raise HTTPException(status_code=422, detail="images 每项要有 image/* 的 media_type。")
        if not image.get("data"):
            raise HTTPException(status_code=422, detail="images 每项要有 base64 data。")
    if request.retry:
        if not request.session_id:
            raise HTTPException(status_code=422, detail="重试必须带 session_id。")
        try:
            _transcript.path_for(request.session_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if request.steering != "queue":
            raise HTTPException(
                status_code=422, detail="重试只支持 queue 档：重跑失败轮不该再插话或抢占。"
            )
        history = _transcript.read(request.session_id)
        if not history or history[-1].role != "user":
            raise HTTPException(
                status_code=409,
                detail="没有可重试的轮次：历史尾部不是用户消息（可能已重试过或已成功）。",
            )
    return StreamingResponse(
        chat_events(
            request.question,
            request.session_id,
            request.steering,
            request.approval_mode,
            request.retry,
            request.images,
        ),
        # media_type 告诉客户端这是一段事件流，而不是一次性的 JSON。
        media_type="text/event-stream",
    )


def main(port: int = 8000) -> None:
    """启动 HTTP 服务。端口由 main.py 传入。

    启动时先读一次配置：坏了当场报，不带进第一个请求里。
    """
    get_config()
    uvicorn.run(app, port=port)


if __name__ == "__main__":
    main()
