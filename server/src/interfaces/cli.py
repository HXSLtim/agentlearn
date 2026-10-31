"""终端入口。这一层叫 interfaces。

只做输入输出。一轮对话的全部编排在 application/conversation.py：
读一行 -> conv.turn 产出片段 -> 打印。
"""
import uuid
from pathlib import Path

from application.conversation import Conversation
from infrastructure.config import load_config
from infrastructure.llm import build_chat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

# 数据目录固定在 server/data。相对路径由 main.py 启动时把工作目录
# 钉在 server/ 来锚定（见 main.py 的说明），从哪里启动都等价。
DATA = Path("data")


def main(session_id: str | None = None) -> None:
    # session_id 由 main.py 传入，用来续接已有会话。
    # 不传就新开一个，id 用 UUID，文件名就是这个 id。
    # 例：uv run python main.py cli 3f2a9c1e-7b4d-4e6a-9c8f-1a2b3c4d5e6f
    session_id = session_id or str(uuid.uuid4())

    config = load_config()
    transcript = Transcript(DATA / "sessions")
    store = Store(DATA / "agent.db")
    # Conversation 构造时会校验 session_id，非法给一句提示再退出。
    try:
        conv = Conversation(build_chat(config), transcript, store, session_id)
    except ValueError as e:
        raise SystemExit(f"{e}")

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

        # 片段只管打印；落盘、历史、日志都在 Conversation 里。
        try:
            for kind, text in conv.turn(question):
                # end="" 不换行，flush=True 立刻显示，否则会攒一批才一起出来。
                print(text, end="", flush=True)
        except Exception as e:
            # error 日志已由 Conversation 记过。这里只管展示：
            # 这轮作废，继续等下一句输入。
            print(f"\n[出错了] {e}")
            continue
        # 回答打完后补一个换行，把下一次的提示符挪到新行。
        print()
        # 单独打印结束原因，方便确认这轮是正常说完还是被掐断。
        print(conv.stop_reason)


if __name__ == "__main__":
    main()
