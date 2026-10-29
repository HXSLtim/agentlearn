"""终端入口。这一层叫 interfaces。

只做输入输出：从键盘读一行，把模型交出来的片段打印到屏幕。
不碰 SDK，也不知道背后用的是哪家的模型。

一轮对话的流程：
1. 把你的问题追加进历史，同时写进 JSONL。
2. 把到目前为止的全部历史发给模型。
3. 模型的回复一边打印，一边拼回内容块。
4. 回复也追加进历史、写进 JSONL，并在 SQLite 里记一条日志。

因为历史会累积，第二轮模型能看见第一轮说过的话。
输入空行结束。下次用同一个 session_id 启动，就能从文件里把历史读回来续上。
"""
import uuid
from pathlib import Path

from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.llm import AnthropicChat, OpenAIChat
from infrastructure.store import Store
from infrastructure.transcript import Transcript
# 数据目录固定在 server/data，和代码放在一起。
# 路径相对 server 目录，所以启动时的工作目录必须是 server。main.py 就是从这里跑的。
DATA = Path("data")
# 当前中转用的模型名。
MODEL = "group/never-stuck"


def main(session_id: str | None = None) -> None:
    # session_id 由 main.py 传入，用来续接已有会话。
    # 不传就新开一个，id 用 UUID，文件名就是这个 id。
    # 例：uv run python main.py cli 3f2a9c1e-7b4d-4e6a-9c8f-1a2b3c4d5e6f
    session_id = session_id or str(uuid.uuid4())

    # 正文存 data/sessions/<session_id>.jsonl，索引和日志存 data/agent.db。
    transcript = Transcript(DATA / "sessions")
    store = Store(DATA / "agent.db")
    # 两个类的用法完全一样。换 OpenAIChat 时把 url 改成带 /v1 的地址。
    chat = AnthropicChat(url="http://127.0.0.1:3425", key="magpie", model=MODEL)

    # 正文文件不存在才是新会话，需要在索引里登记。
    # 续接旧会话时再登记会因为主键重复而报错。
    if not transcript.path_for(session_id).exists():
        store.create_session(session_id, MODEL, str(transcript.path_for(session_id)))
    # 读出已有的全部消息，作为这次启动的历史。新会话读出来是空列表。
    history = transcript.read(session_id)
    print(f"会话 {session_id}，已有 {len(history)} 条消息。空行结束。")

    while True:
        try:
            question = input("请输入问题：").strip()
        except EOFError:
            # 输入被重定向或管道提前关闭时，input 会抛 EOFError。照样正常结束。
            break
        # 空行作为结束信号。
        if not question:
            break

        # 用户的问题是一条只含一个文本块的消息。
        user = Message("user", [TextBlock(question)])
        # 先落盘再放进历史，中途崩溃也不会丢掉已经说过的话。
        transcript.append(session_id, user)
        history.append(user)

        # 流交出来的是零碎片段，存盘需要的是完整的块。
        # 连续的同类片段拼到同一个块里：思考连着思考，文本连着文本。
        blocks: list[ThinkingBlock | TextBlock] = []
        for kind, text in chat.stream(history):
            # end="" 不换行，flush=True 立刻显示，否则会攒一批才一起出来。
            print(text, end="", flush=True)
            # 和上一个块同类，就追加到那个块上。
            if blocks and blocks[-1].type == kind:
                if isinstance(blocks[-1], ThinkingBlock):
                    blocks[-1].thinking += text
                else:
                    blocks[-1].text += text
            # 不同类，新开一个块。
            elif kind == "thinking":
                blocks.append(ThinkingBlock(text))
            else:
                blocks.append(TextBlock(text))
        # 回答打完后补一个换行，把下一次的提示符挪到新行。
        print()

        # signature 不在流式片段里，只在最终消息里。
        # 当前中转返回的是空的，所以这里先留空。
        # 换成会校验的接口后，必须从最终消息里把它填回来，否则续会话会被拒。
        assistant = Message("assistant", blocks)
        transcript.append(session_id, assistant)
        history.append(assistant)
        # 记一条日志，内容是这次的结束原因。
        store.log(session_id, "request", f'{{"stop_reason": "{chat.stop_reason}"}}')
        # 单独打印结束原因，方便确认这轮是正常说完还是被掐断。
        print(chat.stop_reason)


if __name__ == "__main__":
    main()
