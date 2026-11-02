"""第 4 层：上下文压缩。历史太长时把前缀压成摘要，请求只发「摘要 + 尾部」。

形态决定（D22）：JSONL 只追加、永不改写——它是唯一事实来源，崩溃后还能重放。
压缩因此不走「改写历史文件」，走派生层：data/sessions/<id>.summary.json 记
一份模型生成的摘要 + 它覆盖到第几条消息（covered）。发请求时：
摘要拼进 system、messages 只带 covered 之后的尾部。摘要是派生状态——
删掉这个文件，对话一字不少，只是请求重新变长。

切割只能落在「轮次边界」上：一条 user 纯文本消息（新一轮提问）之前。
切在别处会把 assistant(tool_use) 和 user(tool_result) 拆散，
Anthropic 会因为配对缺失拒掉整个请求（D19 的配对不变式）。

token 数是估算不是精确值（见 estimate_tokens）：它只决定「什么时候压」，
真实用量仍由 llm_request span 里的 usage 记账，两相对照才有验收曲线。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from domain.message import Message, TextBlock, ToolResultBlock
from infrastructure.adapter import estimate_tokens

# 「摘要 + 尾部」的目标预算，单位是估算 token。不是模型窗口大小——
# 窗口留给回答和工具循环的余量，这个值是「工作集」：历史超过它就压。
# 拍脑袋的起点，第四层验收（几十轮会话曲线平下来）后可以再调。
DEFAULT_BUDGET_TOKENS = 24000

# 还没有摘要时，给「将来那份摘要」预留的估算量。压缩决策要看的是压完之后
# 的大小，摘要还没生成，只能按上限估。
SUMMARY_TOKENS_RESERVE = 400

# 摘要本身的长度上限（字符），写进提示词让模型收敛。
SUMMARY_CHAR_LIMIT = 600

# 摘要里单条工具结果的截断长度：过程性输出（ls 全列、进度条）不值得进摘要。
RESULT_SNIPPET = 160


@dataclass
class Summary:
    """一份会话摘要：正文 + 覆盖到第几条消息（history[:covered] 已被它代表）。"""

    covered: int
    text: str


def load_summary(path: Path) -> Summary | None:
    """读摘要文件。不存在或坏了返回 None——摘要丢了就当没压过，退回全长历史。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Summary(covered=int(data["covered"]), text=str(data["summary"]))
    except (ValueError, KeyError, TypeError):
        return None


def save_summary(path: Path, covered: int, text: str) -> None:
    """写摘要文件（整文件覆盖——它是派生状态，重写无害，JSONL 才是不可改写的）。"""
    path.write_text(
        json.dumps({"covered": covered, "summary": text}, ensure_ascii=False),
        encoding="utf-8",
    )


def is_turn_start(message: Message) -> bool:
    """这条消息是不是一轮的开头：user 的纯文本消息（新一轮提问）。

    软中断在圈界追加的 user 消息也是这个形状，同样是合法的切割点——
    从它之后开始发，等于「插话之前的部分进了摘要」。
    tool_result 消息（user 角色、内容是工具结果）不是：切在它前面会把
    配对拆散。
    """
    if message.role != "user":
        return False
    has_text = any(b.type == "text" for b in message.content)
    has_result = any(b.type == "tool_result" for b in message.content)
    return has_text and not has_result


def estimate_history_tokens(history: list[Message]) -> int:
    """一段历史的估算 token。json.dumps 整段再估：连 JSON 结构开销一起算，
    方向是宁可高估——高估只会早一点压缩，不会把请求撑爆。"""
    return estimate_tokens(json.dumps([m.to_dict() for m in history], ensure_ascii=False))


def plan_cut(
    history: list[Message],
    covered: int,
    summary_text: str,
    budget: int,
) -> int | None:
    """决定切在哪：返回尾部的起始下标，None = 不用压。

    规则：
    - 现状（摘要 + 全部历史）不超预算就不动。
    - 候选切割点 = 轮次边界（is_turn_start）里、covered 之后的那些。
      covered 之前的消息只存在于摘要里，不能再切——否则同一段历史
      既在摘要里又在尾部，模型看到两遍。
    - 尾部必须包含当前轮的提问（最后一个轮次边界 L 之前），切掉它
      等于把这轮的问题压没了。
    - 从最保守的切法开始试，第一个塞得进预算的就停：压得越少，
      原文保留越多。
    """
    summary_est = estimate_tokens(summary_text) if summary_text else SUMMARY_TOKENS_RESERVE
    if summary_est + estimate_history_tokens(history) <= budget:
        return None
    turn_starts = [i for i, m in enumerate(history) if is_turn_start(m)]
    if not turn_starts:
        return None
    # L = 当前轮提问的位置：turn() 总是先落盘提问再请求，L 一定存在。
    last = turn_starts[-1]
    candidates = sorted(i for i in turn_starts if covered < i <= last)
    for i in candidates:
        if summary_est + estimate_history_tokens(history[i:]) <= budget:
            return i
    # 全都塞不进（当前一轮自己就超预算）：退到只剩当前轮，至少别更糟。
    return last if last > covered else None


def render_for_summary(messages: list[Message]) -> str:
    """把要压掉的消息渲染成给模型看的文本。

    thinking 块整块跳过：摘要要的是事实和结论，思考过程不是事实，
    而且它通常占了大头——跳过它，同样预算能多留下几轮原文。
    """
    lines: list[str] = []
    for m in messages:
        who = "用户" if m.role == "user" else "助手"
        for b in m.content:
            if b.type == "text":
                lines.append(f"{who}：{b.text}")
            elif b.type == "tool_use":
                lines.append(f"{who}调用工具 {b.name}({json.dumps(b.input, ensure_ascii=False)})")
            elif b.type == "tool_result":
                snippet = b.content[:RESULT_SNIPPET] + ("…" if len(b.content) > RESULT_SNIPPET else "")
                # 带图的结果写个标记：摘要里说明「这里曾有一张图」，事实不丢。
                marker = "[图] " if b.image is not None else ""
                lines.append(f"工具结果{'（出错）' if b.is_error else ''}：{marker}{snippet}")
            elif b.type == "image":
                # 用户发来的图（@file / 粘贴）：和工具结果的图同一个标记法，
                # 摘要里留「这里曾有一张图」的事实，图本身压掉。
                lines.append(f"{who}发来一张图")
        if not m.content:
            lines.append(f"{who}：（空）")
    return "\n".join(lines)


def build_summary_prompt(old_summary: str, dropped_text: str) -> str:
    """生成「请把这段历史并入摘要」的提示词。二次压缩时旧摘要先给回模型，
    它在旧摘要的基础上续写，而不是从头再读一遍全部原文。"""
    parts = [
        "你在为一次对话做上下文压缩。把下面这段对话历史并成一份摘要，"
        f"给后来继续这次对话的你看。要求：不超过 {SUMMARY_CHAR_LIMIT} 字；"
        "只留事实——用户的目标、关键数字/名字/路径、做过的决定、工具的结论；"
        "不要过程叙述，不要客套。如果有旧摘要，先合并旧摘要再补新内容，"
        "过期的信息可以删。",
    ]
    if old_summary:
        parts.append("【旧摘要】\n" + old_summary)
    parts.append("【要并入的对话】\n" + dropped_text)
    parts.append("直接输出新摘要正文，不要任何前后缀。")
    return "\n\n".join(parts)
