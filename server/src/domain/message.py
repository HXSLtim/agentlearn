"""对话的数据类型。这一层叫 domain。

这里只放「数据长什么样」，不放任何行为：
不调用模型（那是 infrastructure/llm.py 的事），
也不读写文件（那是 infrastructure/transcript.py 和 store.py 的事）。
这么分是为了让别的层都依赖这里，而这里谁都不依赖。
换 SDK、换存储方式，这个文件都不用改。

工具相关的类型按 Anthropic 的真实返回定：一个 tool_use 块带 id、name、input。
OpenAI 的形状不同，由 infrastructure 里的适配器翻译成这里的类型。
"""
# asdict 把 dataclass 实例递归转成普通字典，json.dumps 只能处理字典和列表。
from dataclasses import asdict, dataclass

# Literal 把取值限制成列出的那几个字符串，写错别的值时类型检查会报错。
from typing import Literal


@dataclass
class TextBlock:
    """一段普通文本。用户的提问和模型的回答都是这种块。"""

    # 这段文本的正文。
    text: str
    # 类型标记，固定是 "text"。存进 JSON 再读出来时，靠它分辨该还原成哪个类。
    type: Literal["text"] = "text"


@dataclass
class ThinkingBlock:
    """模型的一段思考。它先思考再回答，所以一条回复里通常思考块在前、文本块在后。"""

    # 思考的正文。
    thinking: str
    # signature 是服务端给这块思考打的校验值，绑着这一次对话。
    # 多轮时要把上一轮的助手消息原样发回去，这个值必须还在，
    # 丢了或者改过，服务端会认为历史被篡改，直接返回 400。
    # 它不显示给用户，但必须保存。当前用的中转没返回这个值，所以默认是空字符串。
    signature: str = ""
    # 类型标记，固定是 "thinking"。作用和 TextBlock.type 一样。
    type: Literal["thinking"] = "thinking"


@dataclass
class ToolUseBlock:
    """模型发出的一次工具调用。

    字段照着真实返回定：一次带工具的请求，模型返回的块就是这个形状，
    同时 stop_reason 变成 "tool_use"。
    """

    # 这一次调用的标识。交回结果时要带上同一个值，模型才知道结果对应哪一次调用。
    # 并行调两个工具时全靠它区分，不能靠顺序猜。把它当不透明字符串，别假设格式。
    id: str
    # 模型选中的工具名。
    name: str
    # 模型填好的参数，已经是字典。
    input: dict
    # 类型标记，固定是 "tool_use"。
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ImageBlock:
    """一张图（眼睛，2026-11-03）。目前只作为工具结果的一部分存在：
    工具截了图/读了图，模型在下一圈请求里「看见」它。

    存的是 base64（两家协议的图像运输都用它）。media_type 如 "image/png"。
    注意这个 dataclass 的形状不是任何一家的线上格式——出站翻译（图像
    挂在 tool_result 里还是补一条 user 消息）全在 adapter，这是两家
    差异最大的一块，见 adapter.message_to_anthropic / message_to_openai。
    """

    # 图的 MIME 类型，如 "image/png"。
    media_type: str
    # 图的二进制内容的 base64 文本。
    data: str
    # 类型标记，固定是 "image"。
    type: Literal["image"] = "image"


@dataclass
class ToolResultBlock:
    """一次工具调用的执行结果。

    它不是模型返回的，是你执行完工具后自己造的，放进下一条 user 消息里交回去。
    """

    # 对应哪一次调用，值取自 ToolUseBlock.id。
    tool_use_id: str
    # 执行结果的文本。
    content: str
    # 执行是否失败。失败也要交回结果，而不是抛掉，模型才能据此调整。
    is_error: bool = False
    # 工具附带的一张图（可选）。None = 纯文本结果，绝大多数工具如此。
    # 有图时 content 仍要写文字说明（「这是一张截图，内容是…」的占位
    # 由工具自己写）——协议两家都要求/建议图文并存，纯图结果在
    # OpenAI 方向连消息都发不出去。
    image: ImageBlock | None = None
    # 类型标记，固定是 "tool_result"。
    type: Literal["tool_result"] = "tool_result"


# 一条消息的内容由这五种块组成。用联合类型表示「其中之一」，
# 这样别的地方拿到一块时能明确只有这几种可能。
ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock | ImageBlock


@dataclass
class Message:
    """一轮对话里的一条消息。

    role 是谁说的：user 是用户，assistant 是模型。
    content 是内容块的列表。一条助手消息里可以同时有思考块和文本块。
    """

    # 发言者。只能是 "user" 或 "assistant"。
    role: Literal["user", "assistant"]
    # 内容块列表，顺序就是模型生成的顺序。
    content: list[ContentBlock]

    def to_dict(self) -> dict:
        """变成普通字典，好让 json.dumps 能写成一行 JSON。

        字段名当初照着 Anthropic 的线上格式定，所以这个字典同时就是
        Anthropic 请求要的形状——adapter.message_to_anthropic 直接返回它。
        """
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Message":
        """从一行 JSON 还原回来。和 to_dict 正好相反。

        字典本身看不出该变成哪个类，所以按 type 字段一个块一个块地判断。
        """
        blocks: list[ContentBlock] = []
        # data["content"] 是块的字典列表，逐个还原成对应的类。
        for block in data["content"]:
            if block["type"] == "thinking":
                # get 而不是直接取：旧数据里可能没有 signature 这个键。
                blocks.append(ThinkingBlock(thinking=block["thinking"], signature=block.get("signature", "")))
            elif block["type"] == "text":
                blocks.append(TextBlock(text=block["text"]))
            elif block["type"] == "tool_use":
                blocks.append(ToolUseBlock(id=block["id"], name=block["name"], input=block["input"]))
            elif block["type"] == "tool_result":
                # image 可选：只有带图的工具结果才有这个键（老数据没有，get 兜住）。
                raw_image = block.get("image")
                image = ImageBlock(**raw_image) if raw_image else None
                blocks.append(ToolResultBlock(
                    tool_use_id=block["tool_use_id"],
                    content=block["content"],
                    is_error=block.get("is_error", False),
                    image=image,
                ))
            elif block["type"] == "image":
                # 独立的 image 块：当前我们的出站不会生成（图都挂在 tool_result
                # 里），但读回来不认识就报错会连旧文件都读不了，宽容处理。
                blocks.append(ImageBlock(
                    media_type=block["media_type"], data=block["data"]
                ))
            else:
                # 走到这里说明出现了还没定义的块类型。
                # 直接报错，而不是静默丢掉，免得数据缺了一块还不知道。
                raise ValueError(f"未知的内容块: {block['type']}")
        return Message(role=data["role"], content=blocks)
