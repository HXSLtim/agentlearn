"""工具的定义。这一层叫 domain。

只放「数据长什么样」：名字、说明、参数的 JSON Schema。
执行不在 domain——函数本体在 application/tools.py 的注册表里。

放在 domain 而不是 application，是因为 adapter.py（infrastructure）要把它
翻译成各家协议的形状，而依赖方向规定 infrastructure 只能依赖 domain
（见 CLAUDE.md 约定 1）。
"""


from dataclasses import dataclass


@dataclass
class Tool:
    """一个工具的说明书。模型只看得到这三样，据此决定要不要调、怎么调。"""

    # 工具名。模型在 tool_use 块里靠它指名，注册表也用它做键。
    name: str
    # 给模型看的说明：干什么、什么时候该用。写得越准，模型选得越对。
    description: str
    # 参数的 JSON Schema，模型按它生成入参字典。形状：
    # {"type": "object", "properties": {...}, "required": [...]}
    input_schema: dict
