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
import os

import anthropic
from openai import OpenAI

from domain.message import Message, TextBlock, ThinkingBlock
from domain.tool import Tool
from infrastructure.adapter import (
    block_from_anthropic,
    history_to_anthropic,
    message_to_anthropic,
    message_to_openai,
    merge_tool_call_delta,
    prepend_system,
    tool_to_anthropic,
    tool_to_openai,
    tool_uses_from_acc,
    usage_from_anthropic,
    usage_from_openai,
    effort_to_anthropic,
    effort_to_openai,
)
from infrastructure.config import LLMConfig

# OpenAI 的结束原因 -> 统一词汇（沿用 Anthropic 的叫法）。
# 调用方只应见到右边这套；不翻的话，工具循环判断 stop_reason == "tool_use"
# 时 OpenAI 路径会静默永不触发——那是最难查的一类 bug。
_OPENAI_REASON = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


class AnthropicChat:
    """走 Anthropic 协议的客户端。连官方接口或兼容这个协议的中转都能用。"""

    def __init__(self, url: str, key: str, model: str, max_tokens: int = 64000, effort: str = "") -> None:
        # 模型 ID。不同的中转有不同的命名，这里不做校验，由配置传入。
        self.model = model
        # 这一次回复的输出上限，不限制输入。设得太小，回答会被掐断，
        # stop_reason 变成 "max_tokens" 而不是 "end_turn"。
        self.max_tokens = max_tokens
        # 思考强度是我们的词。翻译成这家协议的字段，没有对应档位就夹紧。
        # effort_note 给 trace：夹过的写成 max→high，没发的是 omit。
        self.effort = effort
        self.effort_wire, self.effort_note = effort_to_anthropic(effort)
        # base_url 指向中转或官方地址，api_key 是对应的密钥。
        self.client = anthropic.Anthropic(base_url=url, api_key=key)
        # 流跑完之前没有结束原因，所以先是 None。调用方读完 stream 之后再看它。
        self.stop_reason: str | None = None
        # 流跑完之前这一轮的内容块也是空的，同样是读完 stream 再看。
        self.blocks: list = []
        # 这一轮的用量，流跑完才有。中转不给时保持空字典，调用方要容忍缺字段。
        self.usage: dict = {}

    def stream(self, history: list[Message], tools: list[Tool] | None = None, system: str = ""):
        """逐个产出 (kind, text)。这是一个生成器，调用方用 for 循环来消费。

        system 是随请求重发的提示词（跨会话记忆住在这里），不进对话历史。
        流跑完后：结束原因在 self.stop_reason，完整内容块在 self.blocks。
        """
        # 请求参数先攒成字典：tools 是可选的，没有工具就不带这个键。
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # 出站翻译在 adapter 里：整段历史 -> Anthropic 请求格式。
            # 走 history_to_anthropic 而不是逐条翻译：它顺带把连续 user
            # 消息合并（软中断会造出这种序列，Anthropic 要求角色交替）。
            "messages": history_to_anthropic(history),
        }
        if tools:
            # 「注册工具」就是这一行：定义随请求重申。API 无状态，
            # 服务器不会替你记住上次发过什么工具。
            params["tools"] = [tool_to_anthropic(tool) for tool in tools]
        if self.effort_wire:
            # 思考强度。None（off 或没配）就不带这个键：发一个模型不认识的
            # 参数是 400，省略则用服务端默认。
            params["output_config"] = {"effort": self.effort_wire}
        if system:
            # 系统提示词在这家协议里是顶层字符串字段（OpenAI 那边是首条
            # 消息，见 prepend_system）——这是原生形状，直接放，不用过 adapter。
            params["system"] = system

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

    def __init__(self, url: str, key: str, model: str, max_tokens: int = 64000, effort: str = "") -> None:
        # 含义和 AnthropicChat 相同，只是底层 SDK 不同。
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.effort_wire, self.effort_note = effort_to_openai(effort)
        self.client = OpenAI(base_url=url, api_key=key)
        self.stop_reason: str | None = None
        self.blocks: list = []
        self.usage: dict = {}

    def stream(self, history: list[Message], tools: list[Tool] | None = None, system: str = ""):
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
        if system:
            # 系统提示词：这家的挂法是消息列表首条 role="system"（Anthropic 是
            # 顶层字段）。怎么挂是协议差异，翻译在 adapter.prepend_system。
            params["messages"] = prepend_system(params["messages"], system)
        if tools:
            params["tools"] = [tool_to_openai(tool) for tool in tools]
        if self.effort_wire:
            # OpenAI 这边的字段名是 reasoning_effort，不在 output_config 里。
            params["reasoning_effort"] = self.effort_wire
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


def _apply_no_proxy(value: str) -> None:
    """把配置里的 no_proxy 合并进进程环境，只影响本进程的出站连接。

    标准 NO_PROXY 语义：逗号分隔的主机列表，* 表示全部绕过。
    合并而不是覆盖：环境里已有的条目保留。大小写两个名字都设，
    httpx 两套都会读，只设一套可能被另一套盖住。
    只在 build_chat 建客户端前调用——httpx 是在构造时读环境变量的。
    """
    entries = [item.strip() for item in value.split(",") if item.strip()]
    if not entries:
        return
    for name in ("NO_PROXY", "no_proxy"):
        current = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
        os.environ[name] = ",".join(current + [e for e in entries if e not in current])


def build_chat(config: LLMConfig) -> AnthropicChat | OpenAIChat:
    """按配置里的 provider 造对应的客户端。

    放在两个类的后面：先读类，再看挑类的工厂。
    provider 的合法性在读配置时已经校验过，这里再兜一次底。
    """
    # 配置里写了 no_proxy 才绕行；没写就完全尊重环境里的代理变量。
    _apply_no_proxy(config.no_proxy)
    try:
        if config.provider == "anthropic":
            return AnthropicChat(url=config.base_url, key=config.api_key,
                                 model=config.model_id, max_tokens=config.max_tokens,
                                 effort=config.effort)
        if config.provider == "openai":
            return OpenAIChat(url=config.base_url, key=config.api_key,
                              model=config.model_id, max_tokens=config.max_tokens,
                              effort=config.effort)
    except ImportError as e:
        # 本机代理坑：shell 里挂着 all_proxy=socks5://… 时，httpx 建客户端
        # 就要 SOCKS 支持，环境里没有 socksio 直接 ImportError。这里翻译成
        # 带解法的人话，而不是让它以原始 traceback 糊脸。
        # 抛 ValueError 而不是 SystemExit：build_chat 在运行中也会被调
        # （设置保存、desktop 每个请求），SystemExit 会把常驻服务带走；
        # TUI 启动路径由 main.py 把 ValueError 转成一句提示退出。
        if "socksio" in str(e) or "SOCKS" in str(e):
            raise ValueError(
                "环境里挂着 SOCKS 代理（all_proxy 等），但缺 socksio 包，"
                "模型客户端建不起来。三种解法任选：\n"
                "  1) config.ini 的 [llm] 里加 no_proxy = *（或具体主机列表），"
                "让模型流量绕过代理；\n"
                "  2) 模型接口不用走代理，剥掉代理变量再启动：\n"
                "     env -u all_proxy -u ALL_PROXY -u http_proxy -u https_proxy "
                "-u HTTP_PROXY -u HTTPS_PROXY uv run python main.py tui\n"
                "  3) 模型流量就是要走代理，装上 SOCKS 支持：uv add socksio"
            ) from e
        raise
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
