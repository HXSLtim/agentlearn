"""协议适配器：SDK 的原始形状 <-> domain 里的类型。

两家协议的形状差别全收在这里，函数不发网络请求，只做转换：

出站（发给模型的请求）：
    message_to_anthropic / message_to_openai：一条消息翻成请求里的字典。
    tool_to_anthropic / tool_to_openai：工具定义翻成各家的形状。
    注意 message_to_openai 返回的是字典列表——OpenAI 那边一条我们的消息
    可能要拆成多条（见函数内说明），Anthropic 方向恒为一条。
入站（模型返回的内容）：
    block_from_anthropic：Anthropic 响应块 -> domain 类型。
    merge_tool_call_delta / tool_uses_from_acc：OpenAI 流式的 tool_calls
    是逐片到达的 JSON 字符串，这两个函数负责拼。

对照：domain/message.py 的 Message.from_dict 也按 type 分发，但职责不同——
那个把 JSONL 里我们自己存的字典还原成对象（四种块都可能遇到），
block_from_anthropic 把 SDK 响应的块翻译成对象（模型永远不会返回
tool_result，所以只有三种）。
"""
import json

from domain.message import ContentBlock, Message, TextBlock, ThinkingBlock, ToolUseBlock
from domain.tool import Tool


def block_from_anthropic(raw: dict) -> ContentBlock:
    """把 Anthropic 返回的一个内容块变成自己的类型。

    raw 是块的字典。SDK 只会返回三种块，对不上就报错，不静默丢掉。
    """
    if raw["type"] == "text":
        return TextBlock(text=raw["text"])
    if raw["type"] == "thinking":
        # signature 要原样保留，下一轮发回时服务端靠它校验历史没被改过。
        return ThinkingBlock(thinking=raw["thinking"], signature=raw.get("signature", ""))
    if raw["type"] == "tool_use":
        return ToolUseBlock(id=raw["id"], name=raw["name"], input=raw["input"])
    raise ValueError(f"不认识的 Anthropic 内容块: {raw['type']}")


def message_to_anthropic(message: Message) -> dict:
    """一条 Message -> Anthropic 请求要的字典。

    domain 类型的字段名当初就是照着 Anthropic 的线上格式定的，
    所以出站翻译今天是恒等：Message.to_dict() 直接就是它要的形状。
    仍然单独留这个函数、不让调用方直接调 to_dict()，
    因为 OpenAI 方向不是恒等，调用方不应该需要记住哪家是哪家。
    将来 Anthropic 改格式，改动也只落在这里。
    """
    return message.to_dict()


def message_to_openai(message: Message) -> list[dict]:
    """一条 Message -> OpenAI 请求要的字典列表。

    为什么是列表：两家的工具结果走法完全不同——
    Anthropic 把 tool_result 作为内容块放进下一条 user 消息；
    OpenAI 没有块的概念，要求每个结果单独一条 role="tool" 的消息，
    挂在 tool_call_id 上。所以我们的「一条 user 消息带 N 个结果块」
    到那边要拆成 N 条。调用方（llm.py）把所有消息的结果摊平即可。

    思考块没有对应位置（OpenAI 的 reasoning 不回传历史），出站丢弃——
    两家对「上下文里保留什么」的规则不同，这正是协议差异，收在这里。
    """
    out: list[dict] = []
    # 先把内容块按类型归拢。
    text = "".join(b.text for b in message.content if b.type == "text")
    tool_uses = [b for b in message.content if b.type == "tool_use"]
    tool_results = [b for b in message.content if b.type == "tool_result"]

    if message.role == "assistant":
        msg: dict = {"role": "assistant", "content": text if text else None}
        if tool_uses:
            # 助手发起的调用在这边是 tool_calls 数组，arguments 是 JSON 字符串
            # （我们 domain 里的 input 是字典，这里要转回去）。
            msg["tool_calls"] = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)},
                }
                for b in tool_uses
            ]
        out.append(msg)
        return out

    # user 消息：先文字（如果有），再每个工具结果单独一条 role="tool"。
    if text:
        out.append({"role": "user", "content": text})
    for b in tool_results:
        out.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content})
    # 纯提问的 user 消息只有第一条文字；循环走完天然就是一条，不用特判。
    return out


def tool_to_anthropic(tool: Tool) -> dict:
    """工具定义 -> Anthropic 的形状。domain 的字段名照它定的，接近恒等。"""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def tool_to_openai(tool: Tool) -> dict:
    """工具定义 -> OpenAI 的形状。

    差别有两处：外面套一层 {"type": "function"}，schema 的键叫 parameters
    不叫 input_schema。
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def merge_tool_call_delta(acc: dict[int, dict], deltas) -> None:
    """把一个流式 chunk 里 delta.tool_calls 的片段并进累积器，就地修改。

    OpenAI 的工具调用不走 content，走 delta.tool_calls，而且分片：
    第一个片段带 index/id/name 和 arguments 的开头，后续片段只有 index
    和 arguments 的下一截。所以按 index 归位逐片拼接。
    deltas 是 SDK 给的 ChoiceDeltaToolCall 列表，字段名是 OpenAI 的事，
    所以这个拼接收在 adapter 而不是 llm.py。
    """
    for d in deltas:
        slot = acc.setdefault(d.index, {"id": "", "name": "", "arguments": ""})
        if d.id:
            slot["id"] = d.id
        if d.function and d.function.name:
            slot["name"] = d.function.name
        if d.function and d.function.arguments:
            slot["arguments"] += d.function.arguments


def tool_uses_from_acc(acc: dict[int, dict]) -> list[ToolUseBlock]:
    """累积器 -> 工具调用块列表，按 index 排序（index 就是模型给出的顺序）。

    arguments 拼完是一段 JSON 字符串，这里解析回字典——和 Anthropic 的
    input 对齐，循环层拿到的永远是同一种形状。
    """
    return [
        ToolUseBlock(id=slot["id"], name=slot["name"], input=json.loads(slot["arguments"] or "{}"))
        for _, slot in sorted(acc.items())
    ]


def usage_from_anthropic(usage) -> dict:
    """Anthropic 的 usage 对象 -> 统一的用量字典。

    三个输入字段各算一块，互不重叠：
        input_tokens        这次既没读缓存也没写缓存的部分
        cache_write_tokens  这次新写进缓存的（cache_creation_input_tokens，约 1.25 倍价）
        cache_read_tokens   这次从缓存读出来的（cache_read_input_tokens，约 0.1 倍价）
    所以真正的输入总量是三者相加，不能只看 input_tokens——跑久了它只剩没缓存的
    那一小截，其余全在 cache_read 里，单看会把用量低估一个数量级。

    缓存字段要请求里放了 cache_control 才有数，没放就是 0，不是中转丢了。
    getattr：中转不一定原样透传这些字段，缺了就当 0，不让一次调用崩掉。
    """
    return _usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def usage_from_openai(usage) -> dict:
    """OpenAI 的 usage 对象 -> 同一个用量字典。

    和 Anthropic 的差别在于 prompt_tokens 是总量，缓存的部分已经算在里面，
    所以要减掉 cached_tokens 和 cache_write_tokens 才对得上那边的 input_tokens。
    缓存明细藏在 prompt_tokens_details 里，普通模型或中转可能整个不给。
    """
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(details, "cached_tokens", 0) or 0 if details else 0
    cache_write = getattr(details, "cache_write_tokens", 0) or 0 if details else 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    return _usage(
        input_tokens=prompt - cache_read - cache_write,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def _usage(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> dict:
    """两家翻完都进这里，保证字典的键一模一样。

    cache_hit_ratio 用千分位整数而不是小数：回放时直接看「千分之几」比看
    0.333 清楚，也避开浮点尾巴。分母是输入总量；一次请求什么都没读到
    （理论上看不到）时给 0 而不是除零。
    """
    total_input = input_tokens + cache_read + cache_write
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cache_hit_ratio": round(cache_read / total_input * 1000) if total_input else 0,
    }
