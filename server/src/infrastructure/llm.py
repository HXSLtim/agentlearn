"""对模型的一次流式调用。这一层叫 infrastructure。

两个客户端类只负责运输：把 Message 和工具定义发到模型，把模型的流交回来。
交回来的统一是 (kind, text) 片段，kind 有三种：
    "thinking"：思考的一个片段；
    "text"：回答的一个片段；
    "tool_use"：模型要调工具——text 是工具名，一整个调用只报一次，
                给入口做「正在调用 xx」的提示。
两家协议的形状差别不在这里处理，在 adapter.py 里翻译，
结束原因（stop_reason）也统一成一套词汇，
所以调用方不需要知道背后用的是哪家的模型。

传入的是完整的消息历史，而不只是最新一句话。把历史原样发回去，模型才记得前面说过什么。
tools 是本次申报的工具定义（见 application/tools.py 的说明：注册就是每次请求重申）。

流跑完后从 self.blocks 拿这一轮的完整内容块（落盘用），
从 self.stop_reason 拿结束原因。blocks 为什么不从展示片段拼：
Anthropic 路径的最终消息里，tool_use 的 input 是解析好的字典、
thinking 的 signature 只在最终消息里——片段流里拼不出这两样，
所以落盘块一律以完整消息为准。OpenAI 没有最终消息，在流里自己拼好。

文件里另外两个工具函数：
- build_chat：按配置造客户端，选哪家由 config.ini 决定，不用改代码。
- merge_fragment：把文字片段并成完整的内容块，OpenAI 路径组块用。
"""
import anthropic
from openai import OpenAI

from domain.message import Message, TextBlock, ThinkingBlock
from domain.tool import Tool
from infrastructure.adapter import (
    block_from_anthropic,
    message_to_anthropic,
    message_to_openai,
    merge_tool_call_delta,
    tool_to_anthropic,
    tool_to_openai,
    tool_uses_from_acc,
    usage_from_anthropic,
    usage_from_openai,
)
from infrastructure.config import LLMConfig

# OpenAI 的结束原因 -> 统一词汇（沿用 Anthropic 的叫法）。
# 调用方只应见到右边这套；不翻的话，工具循环判断 stop_reason == "tool_use"
# 时 OpenAI 路径会静默永不触发——那是最难查的一类 bug。
_OPENAI_REASON = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


class AnthropicChat:
    """走 Anthropic 协议的客户端。连官方接口或兼容这个协议的中转都能用。"""

    def __init__(self, url: str, key: str, model: str, max_tokens: int = 64000) -> None:
        # 模型 ID。不同的中转有不同的命名，这里不做校验，由配置传入。
        self.model = model
        # 这一次回复的输出上限，不限制输入。设得太小，回答会被掐断，
        # stop_reason 变成 "max_tokens" 而不是 "end_turn"。
        self.max_tokens = max_tokens
        # base_url 指向中转或官方地址，api_key 是对应的密钥。
        self.client = anthropic.Anthropic(base_url=url, api_key=key)
        # 流跑完之前没有结束原因，所以先是 None。调用方读完 stream 之后再看它。
        self.stop_reason: str | None = None
        # 流跑完之前这一轮的内容块也是空的，同样是读完 stream 再看。
        self.blocks: list = []
        # 这一轮的用量，流跑完才有。中转不给时保持空字典，调用方要容忍缺字段。
        self.usage: dict = {}

    def stream(self, history: list[Message], tools: list[Tool] | None = None):
        """逐个产出 (kind, text)。这是一个生成器，调用方用 for 循环来消费。

        流跑完后：结束原因在 self.stop_reason，完整内容块在 self.blocks。
        """
        # 请求参数先攒成字典：tools 是可选的，没有工具就不带这个键。
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # 出站翻译在 adapter 里：Message -> Anthropic 请求格式。
            "messages": [message_to_anthropic(message) for message in history],
        }
        if tools:
            # 「注册工具」就是这一行：定义随请求重申。API 无状态，
            # 服务器不会替你记住上次发过什么工具。
            params["tools"] = [tool_to_anthropic(tool) for tool in tools]

        # messages.stream 返回一个上下文管理器，退出 with 时流会被关闭。
        with self.client.messages.stream(**params) as msg:
            # 迭代流得到的是事件，不是内容块。
            for event in msg:
                # 事件有很多种。先看块开始事件：tool_use 块的 id 和名字在这里，
                # 入参在后面的 input_json_delta 里逐片到达——展示只报一次名字，
                # 完整块（含解析好的入参）等流结束从最终消息拿。
                if event.type == "content_block_start" and event.content_block.type == "tool_use":
                    yield "tool_use", event.content_block.name
                # 只有 content_block_delta 表示「某一块又来了一个片段」，其余跳过。
                if event.type != "content_block_delta":
                    continue
                # 片段再分种类，文字放在不同的字段里。
                if event.delta.type == "thinking_delta":
                    # 思考的片段，文字在 delta.thinking。
                    yield "thinking", event.delta.thinking
                elif event.delta.type == "text_delta":
                    # 回答的片段，文字在 delta.text。
                    yield "text", event.delta.text
            # 结束原因和完整内容都要等整条流读完才拿得到，所以放在循环之后。
            # "end_turn" 表示模型认为说完了，"max_tokens" 表示被上限掐断，
            # "tool_use" 表示模型想调工具——调用方的循环该执行工具再来一轮。
            final = msg.get_final_message()
            self.stop_reason = final.stop_reason
            # 用量和结束原因一样，要等整条流读完。缓存比例也在这里面，
            # 字段翻译在 adapter.usage_from_anthropic。
            self.usage = usage_from_anthropic(final.usage)
            # SDK 的块是对象不是字典，model_dump() 转成字典再进 adapter。
            # thinking 的 signature 只在最终消息里，从这里落盘它才不会丢。
            self.blocks = [
                block_from_anthropic(block.model_dump() if hasattr(block, "model_dump") else block)
                for block in final.content
            ]


class OpenAIChat:
    """走 OpenAI Chat Completions 协议的客户端。

    base_url 要带 /v1，SDK 会在后面自动拼 /chat/completions。
    """

    def __init__(self, url: str, key: str, model: str, max_tokens: int = 64000) -> None:
        # 含义和 AnthropicChat 相同，只是底层 SDK 不同。
        self.model = model
        self.max_tokens = max_tokens
        self.client = OpenAI(base_url=url, api_key=key)
        self.stop_reason: str | None = None
        self.blocks: list = []
        self.usage: dict = {}

    def stream(self, history: list[Message], tools: list[Tool] | None = None):
        """逐个产出 (kind, text)。流跑完后看 self.stop_reason 和 self.blocks。"""
        params: dict = {
            "model": self.model,
            # 这个字段在 OpenAI 那边叫 max_completion_tokens，含义和 max_tokens 相同。
            "max_completion_tokens": self.max_tokens,
            # 出站翻译在 adapter 里。一条我们的消息在 OpenAI 那边可能拆成多条
            # （工具结果各自一条 role="tool"），所以这里要摊平。
            "messages": [m for message in history for m in message_to_openai(message)],
            "stream": True,
            # 不带这个，流里根本没有 usage。带了之后它出现在 [DONE] 前的最后一个
            # chunk 上，而且那个 chunk 的 choices 是空的——下面不能把它当尾包丢掉。
            "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = [tool_to_openai(tool) for tool in tools]
        # stream=True 让返回值变成一个逐块迭代的对象，而不是一条完整消息。
        msg = self.client.chat.completions.create(**params)

        # OpenAI 没有最终消息，块只能在流里自己拼：
        # 文字/思考用 merge_fragment，工具调用用 adapter 的累积器。
        blocks: list[TextBlock | ThinkingBlock] = []
        tool_acc: dict[int, dict] = {}
        announced: set[int] = set()
        for chunk in msg:
            # choices 为空有两种：中转的尾包，和带用量的最后一个 chunk。
            # 后者是我们要的，先把用量拿走再跳过。中转不支持 include_usage 时
            # 这个字段一直是 None，self.usage 就保持空字典。
            if getattr(chunk, "usage", None):
                self.usage = usage_from_openai(chunk.usage)
            if not chunk.choices:
                continue
            # 流的单元是 chunk。一个 chunk 里思考和回答可能同时出现。
            choice = chunk.choices[0]
            # delta 是这个 chunk 新增的那一小段。
            delta = choice.delta
            # reasoning 这个字段只有推理模型才有。普通模型上它不存在，直接取会报错，
            # 所以用 getattr 取，没有时得到 None。
            reasoning = getattr(delta, "reasoning", None)
            if reasoning:
                yield "thinking", reasoning
                merge_fragment(blocks, "thinking", reasoning)
            if delta.content:
                yield "text", delta.content
                merge_fragment(blocks, "text", delta.content)
            if delta.tool_calls:
                # 工具调用不走 content，走 tool_calls，且逐片到达：拼进累积器。
                merge_tool_call_delta(tool_acc, delta.tool_calls)
                for d in delta.tool_calls:
                    # 名字只在第一个片段出现，每个工具报一次「正在调用」，
                    # 不然后续每个参数分片都刷一行。
                    if d.index not in announced:
                        announced.add(d.index)
                        yield "tool_use", tool_acc[d.index]["name"]
            # 结束原因只挂在最后一个 chunk 上，前面的 chunk 里这个值是空的。
            # OpenAI 的原词在这里翻成统一词汇，见 _OPENAI_REASON 的说明。
            if choice.finish_reason:
                self.stop_reason = _OPENAI_REASON.get(choice.finish_reason, choice.finish_reason)
        # 流结束：工具调用块接在文字块后面，凑成这一轮完整的 blocks。
        # arguments 在累积器里是拼好的 JSON 字符串，这里解析回字典，
        # 和 Anthropic 路径的 input 对齐——循环层拿到的永远是同一种形状。
        self.blocks = blocks + tool_uses_from_acc(tool_acc)


def build_chat(config: LLMConfig) -> AnthropicChat | OpenAIChat:
    """按配置里的 provider 造对应的客户端。

    放在两个类的后面：先读类，再看挑类的工厂。
    provider 的合法性在读配置时已经校验过，这里再兜一次底。
    """
    if config.provider == "anthropic":
        return AnthropicChat(url=config.base_url, key=config.api_key,
                             model=config.model_id, max_tokens=config.max_tokens)
    if config.provider == "openai":
        return OpenAIChat(url=config.base_url, key=config.api_key,
                          model=config.model_id, max_tokens=config.max_tokens)
    raise ValueError(f"不认识的 provider: {config.provider}")


def merge_fragment(blocks: list[TextBlock | ThinkingBlock], kind: str, text: str) -> None:
    """把一个流式片段并进块列表，就地修改，不返回新列表。

    OpenAI 路径没有最终消息，落盘的块只能在流里边收边拼。规则一句话：
    和上一个块同类就拼到它上面，不同类就新开一个块。

    思考块的 signature 在这里拼不出来——OpenAI 的流里根本不给回传历史用的
    signature，这也是两家「上下文保留规则」差异的一部分（见 adapter 的说明）。
    """
    if blocks and blocks[-1].type == kind:
        # 同类：拼到最后一个块上。两个类的文字字段名不同，所以先判类型。
        if isinstance(blocks[-1], ThinkingBlock):
            blocks[-1].thinking += text
        else:
            blocks[-1].text += text
    elif kind == "thinking":
        blocks.append(ThinkingBlock(text))
    else:
        blocks.append(TextBlock(text))
