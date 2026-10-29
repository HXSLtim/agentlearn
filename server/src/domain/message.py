"""对话的数据类型。这一层叫 domain。

这里只放「数据长什么样」，不放任何行为：
不调用模型（那是 infrastructure/llm.py 的事），
也不读写文件（那是 infrastructure/transcript.py 和 store.py 的事）。
这么分是为了让别的层都依赖这里，而这里谁都不依赖。
换 SDK、换存储方式，这个文件都不用改。

工具相关的类型先没有。等模型真的返回一次 tool_use，
对着真实返回的块再加，避免凭空猜一个对不上的形状。
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


# 一条消息的内容由这两种块组成。用联合类型表示「二者之一」，
# 这样别的地方拿到一块时能明确只有这两种可能。
ContentBlock = TextBlock | ThinkingBlock


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
        """变成普通字典，好让 json.dumps 能写成一行 JSON。"""
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
            else:
                # 出现第三种块说明模型返回了还没定义的类型（比如将来的工具调用）。
                # 直接报错，而不是静默丢掉，免得数据缺了一块还不知道。
                raise ValueError(f"未知的内容块: {block['type']}")
        return Message(role=data["role"], content=blocks)
