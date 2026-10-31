"""给前端用的 HTTP 入口。

前端是 app/ 里的 Tauri 应用，跑在另一个来源上，通过这个接口来用模型。
和 cli.py 做的事情相同：读入问题，把片段送出去，顺手把消息存下来。
区别只在传输方式。终端是打印，这里是 SSE，一种服务器持续往外推事件的 HTTP 响应。

POST /chat
    请求体：{"question": "...", "session_id": "..."}
    session_id 为空就新开一个会话。
    响应是 text/event-stream，每条事件一行，形状为：
        data: {"kind": "thinking" | "text", "text": "..."}
    全部推完再补一条：
        data: {"kind": "done", "stop_reason": "...", "session_id": "..."}
    中途出错则补一条（200 已发出，改不了状态码，只能用事件报错）：
        data: {"kind": "error", "message": "..."}
    前端拿到 session_id 后保存，下次请求带上，就是在续同一次对话。
    session_id 不是 UUID 时，端点直接返回 422，不开始推流。

这份循环逻辑在 cli.py 里还有一份（把 SSE 推送换回 print）。
下一步建 application/conversation.py 时会把两份收成一份，入口只剩输入输出。
"""
import json
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.config import load_config
from infrastructure.llm import build_chat, merge_fragment
from infrastructure.store import Store
from infrastructure.transcript import Transcript

# 数据目录固定在 server/data。启动时的工作目录必须是 server，main.py 满足这一点。
DATA = Path("data")
# 配置在服务启动时读一次，之后所有请求共用。读不到直接启动失败，不带默认值。
CONFIG = load_config()

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
    """产生 SSE 的每一条。写法和 cli.py 的循环一致，只是把 print 换成了 yield。

    这是一个生成器。StreamingResponse 每拿到一个 yield 的字符串就往外推一次。
    """
    # 每个请求各开一套连接。本地单用户够用；并发上来再换连接池。
    transcript = Transcript(DATA / "sessions")
    store = Store(DATA / "agent.db")
    # 没带 session_id 就是新会话，分配一个 UUID。
    if not session_id:
        session_id = str(uuid.uuid4())
    # 正文文件不存在才登记。续接时文件已在，跳过，避免主键冲突。
    if not transcript.path_for(session_id).exists():
        store.create_session(session_id, CONFIG.model_id, str(transcript.path_for(session_id)))

    # 用户的问题先落盘。
    user = Message("user", [TextBlock(question)])
    transcript.append(session_id, user)
    # 再整体读回。这样 history 里自然包含刚刚这条问题，不用单独 append。
    history = transcript.read(session_id)

    chat = build_chat(CONFIG)
    # 片段一边推给前端，一边并成完整的块，规则在 merge_fragment 里。
    blocks: list[TextBlock | ThinkingBlock] = []
    try:
        for kind, text in chat.stream(history):
            # 每个片段立刻作为一条 SSE 事件推出去。
            yield _event({"kind": kind, "text": text})
            merge_fragment(blocks, kind, text)
    except Exception as e:
        # 200 已经发出，没法再给状态码，只能用事件告诉前端出错了。
        # 半截回复不落盘：存一半的对话和用户看到的不一致，宁可不要；
        # 已落盘的 user 消息留着，下次请求模型会看到这次没答上的问题。
        store.log(session_id, "error", json.dumps({"message": str(e)}))
        yield _event({"kind": "error", "message": str(e)})
        return

    # 回复完整后落盘，并记一条日志。
    # 拼出来的思考块 signature 是空字符串，见 merge_fragment 的说明。
    transcript.append(session_id, Message("assistant", blocks))
    store.log(session_id, "request", json.dumps({"stop_reason": chat.stop_reason}))
    # 最后一条事件告诉前端：结束了、为什么结束、这次会话的 id 是什么。
    yield _event({"kind": "done", "stop_reason": chat.stop_reason, "session_id": session_id})


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
    """启动 HTTP 服务。端口由 main.py 传入。"""
    uvicorn.run(app, port=port)


if __name__ == "__main__":
    main()
