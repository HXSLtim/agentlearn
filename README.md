# AgentLearn

从零写一个 agent。一层一层加，每一层都先自己跑通再往上叠。

1. 一次调用：发一条消息，拿回流式的回答。
2. 一个工具加手写循环：模型要调工具时执行它，把结果送回去，直到它不再要工具。
3. 多个工具、并行调用、工具失败、多轮历史。
4. 上下文变长之后：缓存、截断、压缩。
5. MCP：工具从外部来。
6. Skills：按需加载的指令。

不从 LangChain 起步。循环先自己写一遍，再去看框架包了什么。

每层的验收标准、基建线进度见 [docs/roadmap.md](docs/roadmap.md)；
技术选型的决定和理由见 [docs/decisions.md](docs/decisions.md)；
当前工作队列见 [TODO.md](TODO.md)。

## 结构

按依赖方向分层，外层依赖内层：

- `domain`：纯概念。Message、四种内容块。不依赖 SDK。
- `application`：用例。会话循环（conversation.py）住在这里，cli 和 web 共用。
- `infrastructure`：anthropic 和 openai 的客户端。把 SDK 的事件翻译成自己的类型。
- `interfaces`：入口。只负责输入输出。

仓库分两块，各管各的：

- `server/`：后端。`pyproject.toml`、`main.py`、代码、数据都在这里。
- `app/`：Tauri 桌面端。通过 HTTP 调用 server，不共享代码。

`server/src/` 下四个层各是一个包：

- `domain/message.py`：Message 和四种内容块（Text、Thinking、ToolUse、ToolResult）。
- `application/conversation.py`：Conversation，唯一的会话循环；入口只剩输入输出。
- `infrastructure/llm.py`：AnthropicChat 和 OpenAIChat，只管运输；build_chat 按配置挑类；merge_fragment 把流式片段并成块。
- `infrastructure/adapter.py`：协议适配器，两家的形状差别全在这里翻译。
- `infrastructure/config.py`：读 config.ini，支持 `${环境变量}` 引用。
- `infrastructure/transcript.py`：会话正文，一个会话一个 JSONL 文件，存在 `server/data/`。
- `infrastructure/store.py`：会话索引和日志，存在 `server/data/agent.db`。
- `interfaces/cli.py`：终端入口。
- `interfaces/web/server.py`：HTTP 入口，给 `app/` 调用。

空的层不提前建；application 在收拢两入口循环时（2026-11-01）成立。

## 配置

第一次使用先复制模板：

```bash
cd server
cp config.example.ini config.ini
```

`config.ini` 不进版本库。任何值都可以写 `${环境变量名}`，
比如密钥写 `api_key = ${LLM_API_KEY}`，启动时从环境变量取，密钥不落盘。

在 `server/` 目录里启动：

```bash
cd server
uv run python main.py cli                 # 终端里新开一个会话
uv run python main.py cli <session_id>    # 续接已有会话
uv run python main.py web                 # HTTP 服务，默认 8000 端口
```

桌面端在 `app/` 目录里启动：`npm run tauri dev`。它不负责启动后端，后端需要单独跑着。
