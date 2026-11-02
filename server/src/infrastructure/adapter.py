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
import re

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
    所以出站翻译基本是恒等：Message.to_dict() 就是它要的形状。
    仍然单独留这个函数、不让调用方直接调 to_dict()，
    因为 OpenAI 方向不是恒等，调用方不应该需要记住哪家是哪家。

    唯一不恒等的一处：带图的 tool_result（眼睛，2026-11-03）。我们的存储
    形状是 content 字符串 + 可选的 image 字段；Anthropic 要求 content
    变成块列表（image 块在前、text 块在后）。to_dict 的形状要保持和
    JSONL 存盘一致（存储用我们的形状），线上翻译只在这里做——
    「存盘形状」和「线上形状」是两件事，差异不许渗出 adapter。
    """
    raw = message.to_dict()
    # 图块两种来源：tool_result 的附带图（眼睛，D25）和 user 消息里的
    # 顶层图块（@file / 粘贴图片，2026-11-03）——都要翻成线上形状。
    has_image = any(
        (b.type == "tool_result" and b.image is not None) or b.type == "image"
        for b in message.content
    )
    if not has_image:
        # 无图也不能直接恒等：asdict 会给每个 tool_result 带上 "image": null，
        # 线上多一个协议里没有的键。剥掉再交出去（JSONL 存盘不受影响）。
        content = [
            {k: v for k, v in block.items() if k != "image"}
            if block.get("type") == "tool_result" else block
            for block in raw["content"]
        ]
        raw["content"] = content
        return raw
    content = []
    for block in raw["content"]:
        if block.get("type") == "tool_result" and block.get("image"):
            image = block.pop("image")
            content.append({
                "type": "tool_result",
                "tool_use_id": block["tool_use_id"],
                "is_error": block.get("is_error", False),
                # 图在前文在后：Anthropic 的示例就是这个顺序，先图后说明。
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image["media_type"],
                            "data": image["data"],
                        },
                    },
                    {"type": "text", "text": block["content"]},
                ],
            })
        elif block.get("type") == "image":
            # user 顶层图块：存储形状是 media_type + data 两个字段，
            # 线上要包进 source（和 tool_result 里那张同一个包装）。
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block["media_type"],
                    "data": block["data"],
                },
            })
        else:
            content.append({k: v for k, v in block.items() if k != "image"})
    raw["content"] = content
    return raw


def history_to_anthropic(history: list[Message]) -> list[dict]:
    """整段历史 -> Anthropic 的 messages 数组，连续 user 消息合并成一条。

    什么时候会出现连续 user：软中断（cooperative steering）把新消息追加在
    user(tool_result) 后面——agent loop 的圈界既是「请求间隙」也是「插入点」。
    Anthropic 要求 user / assistant 交替，两条 user 连发会被拒；合并就是
    把内容块列表拼进同一条消息（tool_result 块 + text 块同条是合法的）。
    OpenAI 方向不需要这步：role="tool" 后面跟 role="user" 本来就合法，
    所以这个函数只服务 Anthropic 出站——协议差异收在 adapter 的约定。
    """
    out: list[dict] = []
    for message in history:
        raw = message_to_anthropic(message)
        if out and message.role == "user" and out[-1]["role"] == "user":
            out[-1]["content"] = [*out[-1]["content"], *raw["content"]]
        else:
            out.append(raw)
    return out


def prepend_system(messages: list[dict], system: str) -> list[dict]:
    """把 system 提示词放进 OpenAI 请求的消息列表（返回新列表，不改原列表）。

    两家对「系统提示词」的走法不同，这是记忆注入要过的第一道协议差异：
    Anthropic 是请求体里一个顶层的 system 字符串字段（llm.py 直接放，
    那是它的原生形状，不需要翻译）；OpenAI 没有这个字段，约定是消息列表
    的第一条 role="system"。同一个「system 文本」，两种挂法，差异收在这里。
    """
    return [{"role": "system", "content": system}, *messages]


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
    # 带图（@file / 粘贴）时 content 必须是数组：text 块 + image_url 块
    # （data URI），纯文字消息保持字符串形状省 token。
    images = [b for b in message.content if b.type == "image"]
    if images:
        parts: list[dict] = [{"type": "text", "text": text}] if text else []
        parts.extend({
            "type": "image_url",
            "image_url": {"url": f"data:{b.media_type};base64,{b.data}"},
        } for b in images)
        out.append({"role": "user", "content": parts})
    elif text:
        out.append({"role": "user", "content": text})
    for b in tool_results:
        out.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content})
        # 带图的工具结果：role="tool" 只能是文本，图作为紧随其后的一条
        # user 消息补发（data URI）。这是「眼睛」在两家协议里最大的分歧：
        # Anthropic 是 tool_result 里装 image 块，OpenAI 得另开一条消息。
        # 补发的消息紧跟在它所属的 tool 消息后面，模型仍能对上号
        # （上一条就是对应结果，图文相邻）。
        if b.image is not None:
            out.append({
                "role": "user",
                "content": [{
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{b.image.media_type};base64,{b.image.data}"
                    },
                }],
            })
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


# 我们自己的思考强度。循环和配置只认这五个词，协议字段不外泄。
# 空字符串和 off 都表示「这个请求不带强度参数」，让服务端用它的默认。
EFFORT_LEVELS = ("off", "low", "medium", "high", "max")

# Anthropic 的 output_config.effort 有 low/medium/high/xhigh/max。
# 我们不单列 xhigh：五个词够用，xhigh 夹在 high 和 max 之间，需要时再加一档。
# off 不翻译成 thinking disabled——一批模型（Opus 5.5 等）对 disabled 直接 400，
# 「关掉思考」不是可移植的一档。
_ANTHROPIC_EFFORT = {
    "": (None, "omit"),
    "off": (None, "omit"),
    "low": ("low", "low"),
    "medium": ("medium", "medium"),
    "high": ("high", "high"),
    "max": ("max", "max"),
}

# OpenAI 的 reasoning_effort 常见是 low/medium/high，没有 max。
# none / minimal 只在部分模型上合法，所以 off 也不发，跟 Anthropic 一样省略。
# max 夹到 high，trace 说明写成 max→high，看得出被夹过而不是静默丢了。
_OPENAI_EFFORT = {
    "": (None, "omit"),
    "off": (None, "omit"),
    "low": ("low", "low"),
    "medium": ("medium", "medium"),
    "high": ("high", "high"),
    "max": ("high", "max→high"),
}


def effort_to_anthropic(level: str) -> tuple[str | None, str]:
    """我们的档位 -> (output_config.effort 的值或 None, trace 上的说明)。

    None 表示这个请求不带 output_config。模型根本没有这个参数时，
    配置里留空，不要发——发出去是 400，夹档救不了「整个参数不存在」。
    """
    return _effort(level, _ANTHROPIC_EFFORT)


def effort_to_openai(level: str) -> tuple[str | None, str]:
    """我们的档位 -> (reasoning_effort 的值或 None, trace 上的说明)。"""
    return _effort(level, _OPENAI_EFFORT)


def _effort(level: str, table: dict) -> tuple[str | None, str]:
    if level not in table:
        raise ValueError(f"不认识的思考强度: {level!r}，只能是 {', '.join(EFFORT_LEVELS)} 或留空")
    return table[level]


# ---------------------------------------------------------------------------
# prompt caching 与 token 估算（第 4 层）
# ---------------------------------------------------------------------------

# Anthropic 对可缓存前缀有最短长度要求（Opus/Sonnet 1024、Haiku 2048）。
# 打了断点但前缀不够长，请求直接 400。取最严的 2048 当门槛，宁可少命中。
CACHE_MIN_TOKENS = 2048

_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """一段文本的 token 估算。中文字符按 1 个算，其余按 4 个字符折 1 个。

    是粗估不是计数（真值在响应的 usage 里，spans 记了账）。
    用途只有两个，都只吃误差不吃精确：压缩决策（什么时候压）和
    缓存断点门槛（够不够格打断点）。方向上宁可高估——高估只是早压一点。
    """
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) // 4


def mark_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """在 Anthropic 请求的 messages 上打缓存断点（就地修改，返回同一列表）。

    断点打在最后一条消息的最后一个内容块上：Anthropic 缓存的是
    「从头到断点」的整段前缀，工具循环每圈追加消息、断点跟着尾巴走，
    每圈都能命中上一圈的前缀——这正是 agent loop 最贵的重读部分。
    system（准则 + 记忆 + 摘要）在前缀里，一起被缓存住。
    OpenAI 方向不调这个函数：那边是自动前缀缓存，没有标记可打，
    协议差异收在 adapter 的约定（谁需要打标记谁调这里）。
    """
    messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return messages
