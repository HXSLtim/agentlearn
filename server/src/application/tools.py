"""工具注册表和第一个工具：calculator。

「定义」和「执行」都在这里：Tool 是说明书（发给模型看），
函数是本体（我们自己跑）。Conversation 拿 all_tools() 随请求发给模型，
收到 tool_use 后拿名字回 REGISTRY 找函数执行。

安全红线（CLAUDE.md 约定 5）：模型给的参数永远不进 eval / exec。
calculator 用 ast 把表达式解析成语法树，只放行「纯算术」的节点——
数字、四则运算、取负。变量名、函数调用、属性访问一律拒绝，
模型提交 "import os" 时它只是一段解析不通/不合法的文字。
"""
import ast

from domain.tool import Tool


def calculator(expression: str) -> str:
    """算术求值。非法表达式抛 ValueError，由 run_tool 接住转成错误结果交回模型。"""
    # mode="eval" 表示按「一个表达式」解析，"import os" 这类语句直接 SyntaxError。
    tree = ast.parse(expression, mode="eval")
    return str(_eval_node(tree.body))


def _eval_node(node: ast.AST):
    """递归求值一个 AST 节点。只认识数和算术运算，其他节点一律拒绝。

    为什么这么啰嗦地一个节点一个节点判：这就是白名单。
    eval 是黑名单思维（想办法挡住危险的），白名单是只放行安全的——
    没预料到的节点类型走到最后一行直接报错，永远不会被「绕过」。
    """
    # 数字字面量。bool 是 int 的子类，先排掉：True 参与 1+True 太诡异。
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"不支持的常量: {node.value!r}")
    # 二元运算：左 op 右。先算两边再按运算符类型合。
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            # 防巨型幂：2**99999999 能算但会占满内存卡死进程。
            if abs(right) > 10_000:
                raise ValueError("指数太大，拒绝计算")
            return left**right
        raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
    # 一元运算：-3、+3。operand 是被套的那个子表达式。
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
    # 走到这里的是白名单外的节点：变量名（ast.Name）、函数调用（ast.Call）等。
    # 拒绝而不是跳过——静默跳过会让结果错得莫名其妙。
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


CALCULATOR = Tool(
    name="calculator",
    description=(
        "四则运算计算器。输入一个算术表达式字符串（支持 + - * / // % ** 和括号），"
        "返回计算结果。需要精确数值计算时必须用它，不要心算。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "要计算的算术表达式，例如 (123*456)+789",
            }
        },
        "required": ["expression"],
    },
)

# 注册表：工具名 -> (定义, 函数)。以后加工具就是在这里添一行，
# 循环和协议层都不用动——它们只认 Tool 和注册表，不认识具体工具。
REGISTRY: dict[str, tuple[Tool, object]] = {
    CALCULATOR.name: (CALCULATOR, calculator),
}


def all_tools() -> list[Tool]:
    """全部工具的定义。Conversation 每次请求模型时随消息一起发过去。"""
    return [tool for tool, _ in REGISTRY.values()]


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """按名字执行一个工具，返回 (结果文本, 是否出错)。永不抛异常。

    出错也返回字符串（is_error=True）交回模型，而不是把异常抛出去打断
    整轮对话——模型看到错误描述能自己换路子（第三层会专门练这个）。
    span 记录不在这里做：那是 Conversation（知道 turn 上下文）的职责。
    """
    entry = REGISTRY.get(name)
    if entry is None:
        # 模型幻觉出了一个不存在的工具名。如实告诉它有哪些可用，让它调整。
        return f"没有叫 {name} 的工具。可用：{', '.join(REGISTRY)}", True
    _, func = entry
    try:
        # **args 把模型给的字典展开成关键字参数：{"expression": "..."}
        # 正好对上 calculator(expression=...)。给错参数名会 TypeError，落进 except。
        return str(func(**args)), False
    except Exception as e:
        return f"工具执行出错: {e}", True
