"""终端入口。这一层叫 interfaces。

只做输入输出：从键盘读一行，把模型交出来的片段打印到屏幕。
不碰 SDK，也不知道背后用的是哪家的模型。

一轮对话的流程：
1. 把你的问题追加进历史，同时写进 JSONL。
2. 把到目前为止的全部历史发给模型。
3. 模型的回复一边打印，一边用 merge_fragment 拼回内容块。
4. 回复也追加进历史、写进 JSONL，并在 SQLite 里记一条日志。

因为历史会累积，第二轮模型能看见第一轮说过的话。
输入空行结束。下次用同一个 session_id 启动，就能从文件里把历史读回来续上。

这份循环逻辑在 web/server.py 里还有一份（把 print 换成 SSE 推送）。
下一步建 application/conversation.py 时会把两份收成一份，入口只剩输入输出。
"""
import json
import uuid
from pathlib import Path

from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.config import load_config
from infrastructure.llm import build_chat, merge_fragment
from infrastructure.store import Store
from infrastructure.transcript import Transcript

# 数据目录固定在 server/data，和代码放在一起。
# 路径相对 server 目录，所以启动时的工作目录必须是 server。main.py 就是从这里跑的。
DATA = Path("data")


def main(session_id: str | None = None) -> None:
    # session_id 由 main.py 传入，用来续接已有会话。
    # 不传就新开一个，id 用 UUID，文件名就是这个 id。
    # 例：uv run python main.py cli 3f2a9c1e-7b4d-4e6a-9c8f-1a2b3c4d5e6f
    session_id = session_id or str(uuid.uuid4())

    # 配置读一次。provider 决定用哪个客户端类。
    config = load_config()
    # 正文存 data/sessions/<session_id>.jsonl，索引和日志存 data/agent.db。
    transcript = Transcript(DATA / "sessions")
    store = Store(DATA / "agent.db")
    chat = build_chat(config)

    # 先校验 session_id（必须是 UUID），非法直接退出，不进入对话循环。
    try:
        session_path = transcript.path_for(session_id)
    except ValueError as e:
        raise SystemExit(f"{e}")
    # 正文文件不存在才是新会话，需要在索引里登记。
    # 续接旧会话时再登记会因为主键重复而报错。
    if not session_path.exists():
        store.create_session(session_id, config.model_id, str(session_path))
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

        # 片段边打印边并成完整的块，规则在 merge_fragment 里。
        blocks: list[TextBlock | ThinkingBlock] = []
        try:
            for kind, text in chat.stream(history):
                # end="" 不换行，flush=True 立刻显示，否则会攒一批才一起出来。
                print(text, end="", flush=True)
                merge_fragment(blocks, kind, text)
        except Exception as e:
            # 出错不退出整个会话：这一轮作废，继续等下一句输入。
            # 半截回复不存盘（和 web 端一致）；已落盘的 user 消息留着，
            # 下次请求模型会看到这次没答上的问题。
            print(f"\n[出错了] {e}")
            store.log(session_id, "error", json.dumps({"message": str(e)}))
            continue
        # 回答打完后补一个换行，把下一次的提示符挪到新行。
        print()

        # 拼出来的思考块 signature 是空字符串（见 merge_fragment 的说明）。
        # 当前中转本来就不返回这个值，所以现在没问题；
        # 换成会校验的接口后，要从最终消息里把它填回来，否则续会话会被拒。
        assistant = Message("assistant", blocks)
        transcript.append(session_id, assistant)
        history.append(assistant)
        # 记一条日志。data 统一用 json.dumps 生成，不手拼字符串。
        store.log(session_id, "request", json.dumps({"stop_reason": chat.stop_reason}))
        # 单独打印结束原因，方便确认这轮是正常说完还是被掐断。
        print(chat.stop_reason)


if __name__ == "__main__":
    main()
