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
    前端拿到 session_id 后保存，下次请求带上，就是在续同一次对话。
"""
import json
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.llm import AnthropicChat
from infrastructure.store import Store
from infrastructure.transcript import Transcript
# 数据目录固定在 server/data。启动时的工作目录必须是 server，main.py 满足这一点。
DATA = Path("data")
MODEL = "group/never-stuck"

# FastAPI 应用本身。路由都登记在它上面。
app = FastAPI()
# 前端的页面和这个服务不在同一个地址上，浏览器会先发一个预检请求问允不允许。
# 不加这几行，浏览器里的请求会被直接拦掉（从终端里发则不受影响）。
app.add_middleware(
    CORSMiddleware,
    # 允许任意来源。这是本地开发的取法，部署出去时应改成具体的地址。
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    """POST /chat 的请求体。FastAPI 按这个定义解析 JSON。"""

    # 用户这一轮的问题。
    question: str
    # 空字符串表示新会话。前端续接时把上次拿到的 id 传回来。
    session_id: str = ""


def chat_events(question: str, session_id: str):
    """产生 SSE 的每一条。写法和 cli.py 的循环一致，只是把 print 换成了 yield。

    这是一个生成器。StreamingResponse 每拿到一个 yield 的字符串就往外推一次。
    """
    transcript = Transcript(DATA / "sessions")
    store = Store(DATA / "agent.db")
    # 没带 session_id 就是新会话，分配一个 UUID。
    if not session_id:
        session_id = str(uuid.uuid4())
    # 正文文件不存在才登记。续接时文件已在，跳过，避免主键冲突。
    if not transcript.path_for(session_id).exists():
        store.create_session(session_id, MODEL, str(transcript.path_for(session_id)))

    # 用户的问题先落盘。
    user = Message("user", [TextBlock(question)])
    transcript.append(session_id, user)
    # 再整体读回。这样 history 里自然包含刚刚这条问题，不用单独 append。
    history = transcript.read(session_id)

    chat = AnthropicChat(url="http://127.0.0.1:3425", key="magpie", model=MODEL)
    # 片段一边推给前端，一边拼成完整的块，最后存盘。
    blocks: list[ThinkingBlock | TextBlock] = []
    for kind, text in chat.stream(history):
        # 每个片段立刻作为一条 SSE 事件推出去。
        yield _event({"kind": kind, "text": text})
        # 和上一个块同类就拼上去，不同类就新开一个块。
        if blocks and blocks[-1].type == kind:
            if isinstance(blocks[-1], ThinkingBlock):
                blocks[-1].thinking += text
            else:
                blocks[-1].text += text
        elif kind == "thinking":
            blocks.append(ThinkingBlock(text))
        else:
            blocks.append(TextBlock(text))

    # 回复完整后落盘，并记一条日志。
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
