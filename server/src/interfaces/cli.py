"""终端入口。这一层叫 interfaces。

只做输入输出。一轮对话的全部编排在 application/conversation.py：
读一行 -> conv.turn 产出片段 -> 打印。

除了对话，还提供一个只读命令 show_trace：把一个会话的执行轨迹
（store.py 的 spans 表）和正文（JSONL）拼成一棵树打印出来，
对应 main.py 的 trace 子命令。spans 记「怎么走的」，JSONL 记「说了什么」，
分开看对不上，回放必须两边一起读。
"""
import json
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

        # 事件只管打印；落盘、历史、日志都在 Conversation 里。
        # 事件是 dict（形状见 conversation.py 顶部），和 web 端收到的是同一份。
        try:
            for event in conv.turn(question):
                if event["kind"] == "tool_use":
                    # 工具调用不是文字流：单独一行提示调了什么、参数是什么。
                    args = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
                    print(f"\n[调用 {event['name']}({args})] ", end="", flush=True)
                    continue
                if event["kind"] == "tool_result":
                    # 结果跟在调用后面。出错带个标记，方便看出是工具失败。
                    mark = "出错 " if event["is_error"] else ""
                    print(f"\n[结果 {mark}{event['output']}  {event['elapsed_ms']}ms] ", end="", flush=True)
                    continue
                # end="" 不换行，flush=True 立刻显示，否则会攒一批才一起出来。
                print(event["text"], end="", flush=True)
        except Exception as e:
            # 出错的 span 已由 Conversation 标成 error。这里只管展示：
            # 这轮作废，继续等下一句输入。
            print(f"\n[出错了] {e}")
            continue
        # 回答打完后补一个换行，把下一次的提示符挪到新行。
        print()
        # 单独打印结束原因，方便确认这轮是正常说完还是被掐断。
        print(conv.stop_reason)


def show_trace(session_id: str) -> None:
    """打印一个会话的执行轨迹，spans 和正文拼在一起。main.py trace 子命令进来这里。

    一轮长这样（缩进表示父子）：

        turn  ok  6121ms
          llm_request  ok  1380ms  ttft=900ms  stop=tool_use
          tool_call  ok  1ms  calculator("12*11") = 132
          llm_request  ok  2969ms  ttft=923ms  stop=end_turn
            └─ 132。

    spans 记控制和耗时，模型的回答只在 JSONL 里，所以 llm_request 下面
    补一行它产出的正文。工具的入参出参存在 span 的 data 里，不需要再翻正文。
    耗时走 store.span_duration_ms，和 web 端 SSE 里的 elapsed_ms 是同一个数。
    """
    store = Store(DATA / "agent.db")
    spans = store.spans_for(session_id)
    if not spans:
        # 没跑过的会话、乱写的 id 都落到这里，不必区分。
        raise SystemExit("这个会话没有 trace 记录。")

    # 正文按 llm_request 的产出顺序排好：每条 assistant 消息是一次模型请求的产物，
    # 和 llm_request span 一一对应。用户消息和工具结果不是模型产出的，跳过。
    answers = [
        _answer_text(message)
        for message in Transcript(DATA / "sessions").read(session_id)
        if message.role == "assistant"
    ]

    # 先按 parent_id 归堆：children[某个 span_id] = 它的子 span 列表。
    # 根 span 的 parent_id 在库里是 NULL，Python 读出来是 None，
    # 所以 None 这个键正好装着所有根，这里不用特判。
    children: dict[str | None, list] = {}
    for row in spans:
        children.setdefault(row["parent_id"], []).append(row)

    # 用列表包一层是为了闭包里能改：walk 每遇到一个 llm_request 就取走下一条回答。
    cursor = [0]

    def walk(span, depth: int) -> None:
        """递归打印一棵子树。depth 是缩进层数。"""
        # 耗时只在 store.span_duration_ms 一处算，和前端收到的 elapsed_ms 是同一个数。
        cost_ms = store.span_duration_ms(span["span_id"])
        cost = f"{cost_ms}ms" if cost_ms is not None else "未结束"
        data = json.loads(span["data"] or "{}")
        print("  " * depth + f"{span['name']}  {span['status']}  {cost}{_detail(span['name'], data)}")
        # 模型这次说了什么：回答只在 JSONL 里，spans 不存内容。
        if span["name"] == "llm_request" and cursor[0] < len(answers) and answers[cursor[0]]:
            print("  " * (depth + 1) + "└─ " + answers[cursor[0]])
            cursor[0] += 1
        elif span["name"] == "llm_request":
            cursor[0] += 1
        # 子 span 晚于父 span 开始，归堆时保持了出现顺序，直接遍历就是时间序。
        for child in children.get(span["span_id"], []):
            walk(child, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)


def _detail(name: str, data: dict) -> str:
    """span 后面跟的那截详情。没有就返回空串。"""
    if name == "llm_request":
        parts = []
        if data.get("ttft_ms") is not None:
            parts.append(f"ttft={data['ttft_ms']}ms")
        if data.get("stop_reason"):
            parts.append(f"stop={data['stop_reason']}")
        # 用量：in 是没走缓存的部分，read/write 是缓存读和写。
        # 三者相加才是真正的输入总量，单看 in 会低估。
        if "input_tokens" in data:
            parts.append(
                f"in={data['input_tokens']} out={data['output_tokens']}"
                f" cache={data['cache_read_tokens']}/{data['cache_write_tokens']}"
                f" hit={data['cache_hit_ratio']}‰"
            )
        if data.get("message"):
            parts.append(data["message"])
        return ("  " + "  ".join(parts)) if parts else ""
    if name == "tool_call":
        # 入参压成一行：calculator({"expression": "12*11"}) = 132。
        args = ", ".join(f"{k}={v!r}" for k, v in data.get("input", {}).items())
        out = data.get("output", "")
        return f"  {data.get('tool', '?')}({args}) = {out}"
    return ""


def _answer_text(message) -> str:
    """一条 assistant 消息里能读的文字：文本块拼起来，思考块不展示。

    只调了工具、没说话的那次返回空串，回放时不占一行。
    """
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    # 回答可能很长，回放只给个开头，全文在 JSONL 里。
    return text if len(text) <= 80 else text[:80] + "…"


if __name__ == "__main__":
    main()
