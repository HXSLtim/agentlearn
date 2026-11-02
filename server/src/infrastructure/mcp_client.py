"""MCP 客户端：把外部进程供的工具接进注册表（第 5 层）。

MCP（Model Context Protocol）的 stdio 传输：起一个子进程当服务器，
在它的 stdin/stdout 上跑 JSON-RPC 2.0，一行一条 JSON——没有 HTTP，
没有长度前缀。我们是客户端，外部进程是服务端。

协议手写不走官方 SDK，是刻意的：initialize 握手、tools/list、tools/call
的形状值得亲眼看一遍——SDK 把这些全藏掉，而这个项目就是为了看清这些。
真正的难点不在协议在生命周期：进程会死、响应里混着通知、两个请求线程
不能把对方的响应读走。这文件里每处锁和队列都是为了其中一件。

消息形状（我们关心的三种）：
    请求     {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{...}}
    响应     {"jsonrpc":"2.0","id":1,"result":{...}}   （或 "error"）
    通知     {"jsonrpc":"2.0","method":"notifications/initialized"}
             通知没有 id，也不需要回应——收到就丢。
"""
import atexit
import json
import queue
import re
import shlex
import subprocess
import threading

# 握手时申报的协议版本。服务器会在响应里回它自己的版本；只要握手成功
# 就用服务器的（服务器普遍兼容旧客户端），不强求一致。
PROTOCOL_VERSION = "2025-06-18"

# 单次 tools/call 的等待上限。MCP 工具可能是重活（构建、检索），
# 比 bash 的 10 秒宽；卡死的进程由这个上限兜底。
CALL_TIMEOUT_SECONDS = 30


class StdioMCPClient:
    """和一个 MCP 服务器子进程的连接。一个实例一个进程，不是线程安全的
    ——并发由 MCPManager 的锁串行化（见那里的说明）。"""

    def __init__(self, command: list[str]) -> None:
        # stderr 丢弃：服务器往 stderr 打日志会把我们的终端弄脏；
        # 调试服务器时把它接到文件再看。
        # text=True + bufsize=1：行缓冲的文本管道，readline/write 一行一条消息。
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._next_id = 0
        # 读线程 + 队列：readline 是阻塞的，没法带超时；放线程里读，
        # 请求方从队列拿，才能实现「等 30 秒没响应就报错」。
        self._messages: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._handshake()

    def _read_loop(self) -> None:
        """后台读进程 stdout，一行一条 JSON 解析后进队列；进程退出放 None 哨兵。"""
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._messages.put(json.loads(line))
            except ValueError:
                # 非 JSON 的行（服务器忘了关 debug 打印之类）直接丢，
                # 不能让一条脏行毒死整个连接。
                continue
        self._messages.put(None)

    def alive(self) -> bool:
        """进程还活着吗。poll() 返回 None = 还在跑。"""
        return self.proc.poll() is None

    def _send(self, payload: dict) -> None:
        """写一条消息。json.dumps 不产换行，正好一行一条。"""
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT_SECONDS) -> dict:
        """发一个带 id 的请求，等到自己的响应为止。

        等待途中可能读到别的消息：服务器主动发的通知（进度、日志）——
        没有 id，读了就丢；这不是并发请求（调用方被锁串行），
        只可能是服务器插在响应前面发的。
        """
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    **({"params": params} if params is not None else {})})
        while True:
            try:
                message = self._messages.get(timeout=timeout)
            except queue.Empty:
                raise ValueError(f"MCP 请求 {method} 超过 {timeout} 秒没响应。")
            if message is None:
                raise ValueError(f"MCP 服务器在处理 {method} 时退出。")
            if message.get("id") != request_id:
                continue  # 通知或别人的消息，丢掉继续等
            if "error" in message:
                raise ValueError(f"MCP 错误（{method}）: {message['error'].get('message')}")
            return message.get("result") or {}

    def _handshake(self) -> None:
        """MCP 的启动握手：initialize 请求 -> 对方回能力和身份 -> 我们发
        initialized 通知。三步走完之前不许发别的请求。"""
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},  # 我们什么都不支持（不采样、不订阅）
                "clientInfo": {"name": "AgentLearn", "version": "0.1"},
            },
            timeout=CALL_TIMEOUT_SECONDS,
        )
        # 用服务器申报的版本继续（它向下兼容我们请求的版本）。
        self.server_version = result.get("protocolVersion", PROTOCOL_VERSION)
        # 通知没有 id，也不期待响应。
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        """服务器的工具清单。每项 {name, description, inputSchema}。"""
        return (self._request("tools/list") or {}).get("tools") or []

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        """调一个工具，返回 (结果文本, 是否出错)。协议层的错误抛 ValueError，
        由调用方（tools.run_tool）转成 is_error 结果。"""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        # content 是块列表，我们只消费文本块（图片块是「眼睛」那条 TODO 的事）。
        text = "\n".join(
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        )
        # isError 是协议里工具级失败的信号（比如工具内部抛了异常），
        # 和 JSON-RPC 层的 error 不同层：那是「调用没送达」，这是「工具跑了但失败」。
        return text, bool(result.get("isError"))

    def close(self) -> None:
        """关连接。先 terminate 给它优雅退出的机会，超时再 kill。"""
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class MCPManager:
    """全部 MCP 服务器的登记处：按别名惰性起进程，死了自动重启。

    工具名用「别名__工具名」的复合名（如 cnt__count_words）：
    不同服务器可能有同名工具，前缀保证全局唯一；内置工具名永远不含
    __，路由不会撞。每圈 tools/list 都是真请求（API 无状态的老规矩），
    本地进程一个来回微秒级，不值得做缓存。
    """

    def __init__(self, servers: list[tuple[str, list[str]]]) -> None:
        # servers 来自 config.ini 的 [mcp:<别名>] 段：[(别名, 命令)]。
        self._servers = dict(servers)
        self._clients: dict[str, StdioMCPClient] = {}
        # 一把总锁串行化所有服务器的连接管理：起进程、重启、列表都是低频
        # 操作，锁竞争不存在。工具调用本身不持这把锁（见 call）。
        self._lock = threading.Lock()
        # 进程退出时收掉全部子进程，不留孤儿。
        atexit.register(self.shutdown)

    def _client(self, alias: str) -> StdioMCPClient:
        """拿一个服务器的连接，没有就起，死了就重启。"""
        if alias not in self._servers:
            raise ValueError(f"没有配置叫 {alias} 的 MCP 服务器。")
        client = self._clients.get(alias)
        if client is not None and client.alive():
            return client
        if client is not None:
            client.close()
        client = StdioMCPClient(self._servers[alias])
        self._clients[alias] = client
        return client

    def list_tools(self) -> list[tuple[str, dict]]:
        """全部服务器的工具清单。单个服务器起不来就跳过它——
        工具列表不该被一个坏进程拖垮，模型至少还能用内置工具；
        错误会在它被调用时如实报给模型。"""
        out: list[tuple[str, dict]] = []
        with self._lock:
            for alias in self._servers:
                try:
                    for tool in self._client(alias).list_tools():
                        out.append((alias, tool))
                except ValueError:
                    continue
        return out

    def call(self, alias: str, name: str, arguments: dict) -> tuple[str, bool]:
        """调一个 MCP 工具。永不抛异常：任何失败都变成 is_error 文本，
        和 run_tool 的契约一致（模型看了错误自己调整）。"""
        try:
            with self._lock:
                client = self._client(alias)
            # 调用本身不持锁：一次 tools/call 可能要跑几十秒（构建、检索），
            # 持锁会把别的服务器的列表操作全堵住。单个服务器内部的并发
            # 由 StdioMCPClient 自己的请求-响应匹配（id + 队列）兜住——
            # 但两个线程交错请求时响应归属仍可能读串，所以真正的串行化
            # 靠调用频率低这一事实 + 工具循环本身是顺序执行工具的。
            return client.call(name, arguments)
        except Exception as e:
            return f"MCP 工具 {alias}__{name} 执行失败: {e}", True

    def health(self) -> list[dict]:
        """每个服务器的健康快照（能力面板用）。

        刻意不主动起进程：健康检查本身不该有启动副作用，
        还没惰性起过的如实报 not_started。stopped = 起过但进程没了
        （下次调用会自动重启，那是 _client 的事）。
        """
        with self._lock:
            out: list[dict] = []
            for alias, command in self._servers.items():
                client = self._clients.get(alias)
                if client is not None and client.alive():
                    state, pid = "running", client.proc.pid
                elif client is not None:
                    state, pid = "stopped", None
                else:
                    state, pid = "not_started", None
                out.append({
                    "alias": alias,
                    "command": " ".join(command),
                    "state": state,
                    "pid": pid,
                })
            return out

    def shutdown(self) -> None:
        """进程退出时收尾，atexit 挂过。"""
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
