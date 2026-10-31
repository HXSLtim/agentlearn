"""协议适配器：SDK 的原始形状 <-> domain 里的类型。

两家协议的形状差别全收在这里，函数不发网络请求，只做转换：

出站（发给模型的请求）：
    message_to_anthropic / message_to_openai，由 llm.py 的两个客户端调用。
入站（模型返回的内容块）：
    block_from_anthropic。它还没接进请求路径——现在的流式路径只消费文字片段，
    tool_use 块的翻译等工具循环落地时启用（形状已经用真实返回验证过一轮）。

对照：domain/message.py 的 Message.from_dict 也按 type 分发，但职责不同——
那个把 JSONL 里我们自己存的字典还原成对象（四种块都可能遇到），
这个把 SDK 响应的块翻译成对象（模型永远不会返回 tool_result，所以只有三种）。
"""
from domain.message import ContentBlock, Message, TextBlock, ThinkingBlock, ToolUseBlock


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


def message_to_openai(message: Message) -> dict:
    """一条 Message -> OpenAI 请求要的字典。

    OpenAI 的一条消息内容是单个字符串，不像 Anthropic 那样是内容块的列表。
    思考是 Anthropic 特有的，这里没有对应的位置，所以只取文本块，思考块丢掉。
    工具调用和结果的翻译（tool_calls、role=tool 消息）等工具循环时补。
    """
    # 只拼 type 为 "text" 的块。思考块没有 text 属性，不过滤掉会报错。
    text = "".join(block.text for block in message.content if block.type == "text")
    return {"role": message.role, "content": text}
