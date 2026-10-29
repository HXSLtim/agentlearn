"""对模型的一次流式调用。这一层叫 infrastructure。

职责只有一个：把 domain 里的 Message 发到模型，再把模型的流翻译成统一的片段交回来。
两个类交出来的东西一样，都是 (kind, text)：
    kind 为 "thinking" 时，text 是思考的一个片段；
    kind 为 "text" 时，text 是回答的一个片段。
差别只在各自怎么读 SDK 的流。调用方不用知道背后是哪家的模型。

传入的是完整的消息历史，不只是最新一句话。把历史原样发回去，模型才记得前面说过什么。
"""
import anthropic
from openai import OpenAI

from domain.message import Message


class AnthropicChat:
    """走 Anthropic 协议的客户端。连官方接口或兼容这个协议的中转都能用。"""

    def __init__(self, url: str, key: str, model: str, max_tokens: int = 64000) -> None:
        # 模型名。不同的中转有不同的命名，这里不做校验，由调用方传入。
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
            # Message 的字典形状刚好就是这个接口要的格式，可以直接发。
            messages=[message.to_dict() for message in history],
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
            # OpenAI 的消息是单个字符串，先用 _to_openai 转换再发。
            messages=[_to_openai(message) for message in history],
            stream=True,
        )
        for chunk in msg:
            # 一个 chunk 里思考和回答可能同时出现，所以两个都要检查。
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
            # "stop" 相当于 Anthropic 的 "end_turn"。
            if choice.finish_reason:
                self.stop_reason = choice.finish_reason


def _to_openai(message: Message) -> dict:
    """把一条消息收成 OpenAI 要的形状。

    OpenAI 的一条消息内容是单个字符串，不像 Anthropic 那样是内容块的列表。
    思考是 Anthropic 特有的，这里没有对应的位置，所以只取文本块，思考块丢掉。
    """
    # 只拼 type 为 "text" 的块。思考块没有 text 属性，不过滤掉会报错。
    text = "".join(block.text for block in message.content if block.type == "text")
    return {"role": message.role, "content": text}
