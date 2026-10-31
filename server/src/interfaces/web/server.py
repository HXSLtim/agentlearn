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
"""
import json
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from application.conversation import Conversation
from infrastructure.config import LLMConfig, load_config
from infrastructure.llm import build_chat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

# 数据目录固定在 server/data。相对路径由 main.py 启动时把工作目录
# 钉在 server/ 来锚定（见 main.py 的说明）。
DATA = Path("data")

# 配置惰性加载：import 本模块不触发读文件。main.py 启动 cli 模式时也会
# import 这个模块（拿 web_main），模块级读配置会让 cli 的报错出现在
# import 阶段，还会让不配 config.ini 就跑测试变得不可能。
_cached_config: LLMConfig | None = None


def get_config() -> LLMConfig:
    """读一次配置，之后缓存复用。第一次调用发生在 web 启动或第一个请求。"""
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
    allow_origins=["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    """POST /chat 的请求体。FastAPI 按这个定义解析 JSON。"""

    # 用户这一轮的问题。
    question: str
    # 空字符串表示新会话。前端续接时把上次拿到的 id 传回来。
    # 不是 UUID 的值由端点校验并返回 422（见下面的 chat 函数）。
    session_id: str = ""


def chat_events(question: str, session_id: str):
    """产生 SSE 的每一条。这是一个生成器。

    编排（登记会话、落盘、历史、日志）都在 Conversation 里，
    这里只把它产出的事件原样推进 SSE——事件已经是结构化的 dict
    （形状见本文件顶部），前端拿得到工具的入参、出参、耗时和是否出错，
    不需要自己再拼。
    """
    # 没带 session_id 就是新会话，分配一个 UUID。
    if not session_id:
        session_id = str(uuid.uuid4())
    # 每个请求各开一套连接。本地单用户够用；并发上来再换连接池。
    conv = Conversation(
        build_chat(get_config()),
        Transcript(DATA / "sessions"),
        Store(DATA / "agent.db"),
        session_id,
    )
    try:
        for event in conv.turn(question):
            # 每个事件立刻作为一条 SSE 推出去。
            yield _event(event)
    except Exception as e:
        # 200 已经发出，没法再给状态码，只能用事件告诉前端出错了。
        # error 日志已由 Conversation 记过。
        yield _event({"kind": "error", "message": str(e)})
        return
    # 最后一条事件告诉前端：结束了、为什么结束、这次会话的 id 是什么。
    yield _event({"kind": "done", "stop_reason": conv.stop_reason, "session_id": session_id})


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
            Transcript(DATA / "sessions").path_for(request.session_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return StreamingResponse(
        chat_events(request.question, request.session_id),
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
