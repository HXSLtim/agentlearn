"""终端入口。这一层叫 interfaces。

只做输入输出。一轮对话的全部编排在 application/conversation.py：
读一行 -> conv.turn 产出片段 -> 打印。

除了对话，还提供一个只读命令 show_trace：把一个会话的执行轨迹
（store.py 的 spans 表）画成树打印出来，对应 main.py 的 trace 子命令。
"""
import uuid
from datetime import datetime
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
                if kind == "tool_use":
                    # 工具调用不是文字流：单独一行提示是哪个工具，
                    # 下一轮模型的回答会接着打印在后面。
                    print(f"\n[调用工具 {text}] ", end="", flush=True)
                    continue
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


def show_trace(session_id: str) -> None:
    """打印一个会话的执行轨迹树。main.py trace 子命令进来这里。

    轨迹长这样（缩进表示父子）：

        turn  ok  4.2s
          llm_request  ok  4.1s

    只读库不碰正文：想知道「说了什么」去看 JSONL，这里看「怎么走的」。
    """
    spans = Store(DATA / "agent.db").spans_for(session_id)
    if not spans:
        # 没跑过的会话、乱写的 id 都落到这里，不必区分。
        raise SystemExit("这个会话没有 trace 记录。")

    # 先按 parent_id 归堆：children[某个 span_id] = 它的子 span 列表。
    # 根 span 的 parent_id 在库里是 NULL，Python 读出来是 None，
    # 所以 None 这个键正好装着所有根，这里不用特判。
    children: dict[str | None, list] = {}
    for row in spans:
        children.setdefault(row["parent_id"], []).append(row)

    def walk(span, depth: int) -> None:
        """递归打印一棵子树。depth 是缩进层数。"""
        # 耗时 = 结束时间 - 开始时间。fromisoformat 能直接解析我们存的 ISO 字符串。
        if span["end_time"]:
            seconds = (
                datetime.fromisoformat(span["end_time"])
                - datetime.fromisoformat(span["start_time"])
            ).total_seconds()
            cost = f"{seconds * 1000:.0f}ms"
        else:
            # 没有结束时间 = span 还挂着：进程崩了，或者 SSE 被客户端掐断。
            cost = "未结束"
        print("  " * depth + f"{span['name']}  {span['status']}  {cost}")
        # 子 span 晚于父 span 开始，归堆时保持了出现顺序，直接遍历就是时间序。
        for child in children.get(span["span_id"], []):
            walk(child, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)


if __name__ == "__main__":
    main()
