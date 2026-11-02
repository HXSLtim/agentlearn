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
        {"kind": "tool_result", "id": "...", "name": "...", "output": "...",
         "is_error": false, "elapsed_ms": 1}
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
"""
import json
import uuid
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from application.conversation import Conversation, TurnCancelled
from application.session_manager import MODES, SessionManager
from infrastructure.config import (
    LLMConfig,
    SettingsValues,
    load_config,
    load_settings,
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


# FastAPI 应用本身。路由都登记在它上面。
app = FastAPI()
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
    allow_methods=["GET", "POST", "PUT"],
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


class SettingsRequest(BaseModel):
    """PUT /settings 的请求体。api_key 为空时保留现有密钥。"""

    provider: str
    base_url: str
    model_id: str
    max_tokens: int
    effort: str = ""
    api_key: str | None = None


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


def chat_events(question: str, session_id: str, steering: str):
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
        handle = slot.begin()
        try:
            # 构造放在 try 里：建客户端可能失败（典型是本机 SOCKS 代理缺
            # socksio），让它变成一条 error 事件推给前端，而不是连接中途断掉。
            conv = Conversation(
                build_chat(get_config()),
                _transcript,
                _store,
                session_id,
                steering=handle,
            )
            try:
                for event in conv.turn(question):
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
    return StreamingResponse(
        chat_events(request.question, request.session_id, request.steering),
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
