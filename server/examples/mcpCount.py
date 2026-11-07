"""一个最小的 MCP 服务器（stdio 传输，无 SDK）。

第 5 层的验收对象：`config.ini` 里加一段——

    [mcp:cnt]
    command = python3 examples/mcpCount.py

就能让 agent 多出 `cnt__count_words` 这个工具，循环代码一行不改。

它的形状就是 MCP stdio 协议本体：stdin 逐行读 JSON-RPC，stdout 逐行回。
真实服务器会实现更多能力（资源、采样、进度通知）；这个例子只做验收
需要的最小集：initialize / tools/list / tools/call。服务器端手写和
客户端（src/infrastructure/mcpClient.py）手写是同一个理由：协议要看
过原始形状才算学过。
"""
import json
import sys

# 工具说明书。inputSchema 就是 JSON Schema，和我们 domain.Tool 的
# input_schema 同一个形状——注册表那边零翻译直接用。
TOOLS = [
    {
        "name": "count_words",
        "description": "统计一段文本的字符数（不含空格）和词数（按空白切分）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要统计的文本"}
            },
            "required": ["text"],
        },
    }
]


def count_words(text: str) -> str:
    """工具本体。确定性输出，验收好断言。"""
    return f"字符数（不含空格）: {len(''.join(text.split()))}；词数: {len(text.split())}"


def main() -> None:
    # stdio 服务器的主循环：一行一条请求，处理完立刻回一行。
    # 没有并发、没有状态，卡死一个请求就卡死整个服务器——例子接受这一点。
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")

        # initialize：握手。回对方请求的协议版本（兼容旧客户端的惯例）、
        # 自己的能力（只有 tools）、身份。
        if method == "initialize":
            reply = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": message.get("params", {}).get(
                        "protocolVersion", "2025-06-18"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-count", "version": "0.1"},
                },
            }
        # initialized 是通知：没有 id，不需要回。
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            params = message.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                if name != "count_words":
                    raise ValueError(f"没有叫 {name} 的工具")
                # 工具失败不抛 JSON-RPC error：按协议回 isError=true 的
                # 正常响应，让客户端把失败当工具结果交给模型。
                reply = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": count_words(**arguments)}],
                        "isError": False,
                    },
                }
            except Exception as e:
                reply = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": f"工具执行出错: {e}"}],
                        "isError": True,
                    },
                }
        elif request_id is not None:
            # 带 id 的未知请求：按 JSON-RPC 惯例回 method not found。
            reply = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        else:
            # 不认识的通知：忽略。
            continue

        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
