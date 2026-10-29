# AgentLearn

从零写一个 agent。一层一层加，每一层都先自己跑通再往上叠。

1. 一次调用：发一条消息，拿回流式的回答。
2. 一个工具加手写循环：模型要调工具时执行它，把结果送回去，直到它不再要工具。
3. 多个工具、并行调用、工具失败、多轮历史。
4. 上下文变长之后：缓存、截断、压缩。
5. MCP：工具从外部来。
6. Skills：按需加载的指令。

不从 LangChain 起步。循环先自己写一遍，再去看框架包了什么。

## 结构

按依赖方向分层，外层依赖内层：

- `domain`：纯概念。Message、ToolCall。不依赖 SDK。第二层才需要。
- `application`：用例。agent 循环住在这里。第二层才需要。
- `infrastructure`：anthropic 和 openai 的客户端。把 SDK 的事件翻译成自己的类型。
- `interfaces`：入口。只负责输入输出。

仓库分两块，各管各的：

- `server/`：后端。`pyproject.toml`、`main.py`、代码、数据都在这里。
- `app/`：Tauri 桌面端。通过 HTTP 调用 server，不共享代码。

`server/src/` 下三个层各是一个包：

- `domain/message.py`：Message、TextBlock、ThinkingBlock。
- `infrastructure/llm.py`：AnthropicChat 和 OpenAIChat。
- `infrastructure/transcript.py`：会话正文，一个会话一个 JSONL 文件，存在 `server/data/`。
- `infrastructure/store.py`：会话索引和日志，存在 `server/data/agent.db`。
- `interfaces/cli.py`：终端入口。
- `interfaces/web/server.py`：HTTP 入口，给 `app/` 调用。

空的层不提前建。`application` 等写循环时再加。

在 `server/` 目录里启动：

```bash
cd server
uv run python main.py cli                 # 终端里新开一个会话
uv run python main.py cli <session_id>    # 续接已有会话
uv run python main.py web                 # HTTP 服务，默认 8000 端口
```

桌面端在 `app/` 目录里启动：`npm run tauri dev`。它不负责启动后端，后端需要单独跑着。
