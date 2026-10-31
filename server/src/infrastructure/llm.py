"""对模型的一次流式调用。这一层叫 infrastructure。

两个客户端类只负责运输：把 Message 发到模型，把模型的流交回来。
交回来的统一是 (kind, text) 片段：
    kind 为 "thinking" 时，text 是思考的一个片段；
    kind 为 "text" 时，text 是回答的一个片段。
两家协议的形状差别不在这里处理，在 adapter.py 里翻译，
结束原因（stop_reason）也统一成一套词汇，
所以调用方不需要知道背后用的是哪家的模型。

传入的是完整的消息历史，而不只是最新一句话。把历史原样发回去，模型才记得前面说过什么。

文件里另外两个工具函数：
- build_chat：按配置造客户端，选哪家由 config.ini 决定，不用改代码。
- merge_fragment：把流式片段并成完整的内容块，cli 和 web 两个入口共用。
"""
import anthropic
from openai import OpenAI

from domain.message import Message, TextBlock, ThinkingBlock
from infrastructure.adapter import message_to_anthropic, message_to_openai
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

    def stream(self, history: list[Message]):
        """逐个产出 (kind, text)。这是一个生成器，调用方用 for 循环来消费。

        流跑完后，结束原因放在 self.stop_reason 上。
        """
        # messages.stream 返回一个上下文管理器，退出 with 时流会被关闭。
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            # 出站翻译在 adapter 里：Message -> Anthropic 请求格式。
            messages=[message_to_anthropic(message) for message in history],
        ) as msg:
            # 迭代流得到的是事件，不是内容块。
            for event in msg:
                # 事件有很多种：一块开始、一块结束、统计用量。
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
            # 结束原因要等整条流读完才拿得到，所以放在循环之后。
            # "end_turn" 表示模型认为说完了，"max_tokens" 表示被上限掐断。
            # 带上工具之后还会出现 "tool_use"：模型想调工具，循环该继续。
            self.stop_reason = msg.get_final_message().stop_reason


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

    def stream(self, history: list[Message]):
        """逐个产出 (kind, text)。流跑完后从 self.stop_reason 读结束原因。"""
        # stream=True 让返回值变成一个逐块迭代的对象，而不是一条完整消息。
        msg = self.client.chat.completions.create(
            model=self.model,
            # 这个字段在 OpenAI 那边叫 max_completion_tokens，含义和 max_tokens 相同。
            max_completion_tokens=self.max_tokens,
            # 出站翻译在 adapter 里：Message -> OpenAI 请求格式。
            messages=[message_to_openai(message) for message in history],
            stream=True,
        )
        for chunk in msg:
            # 有些兼容中转会发 choices 为空的 chunk（比如带用量的尾包），跳过。
            if not chunk.choices:
                continue
            # 流的单元是 chunk，不是事件。一个 chunk 里思考和回答可能同时出现。
            choice = chunk.choices[0]
            # delta 是这个 chunk 新增的那一小段。
            delta = choice.delta
            # reasoning 这个字段只有推理模型才有。普通模型上它不存在，直接取会报错，
            # 所以用 getattr 取，没有时得到 None。
            reasoning = getattr(delta, "reasoning", None)
            if reasoning:
                yield "thinking", reasoning
            if delta.content:
                yield "text", delta.content
            # 结束原因只挂在最后一个 chunk 上，前面的 chunk 里这个值是空的。
            # OpenAI 的原词在这里翻成统一词汇，见 _OPENAI_REASON 的说明。
            if choice.finish_reason:
                self.stop_reason = _OPENAI_REASON.get(choice.finish_reason, choice.finish_reason)


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

    流交出来的是零碎片段，存盘要的是完整的块。规则一句话：
    和上一个块同类就拼到它上面，不同类就新开一个块。
    cli 和 web 两个入口都用它，规则只在这一处。

    思考块的 signature 在这里拼不出来——它不在流式片段里，
    只在流结束后的最终消息里。所以并出来的思考块 signature 是空字符串。
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
