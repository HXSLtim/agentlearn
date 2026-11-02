# AgentLearn

从零写一个 agent。一层一层加，每一层都先自己跑通再往上叠。

1. 一次调用：发一条消息，拿回流式的回答。
2. 一个工具加手写循环：模型要调工具时执行它，把结果送回去，直到它不再要工具。
3. 多个工具、并行调用、工具失败、多轮历史。
4. 上下文变长之后：缓存、截断、压缩。
5. MCP：工具从外部来。
6. Skills：按需加载的指令。

不从 LangChain 起步。循环先自己写一遍，再去看框架包了什么。

每层在搞懂什么、为什么按这个顺序，见 [docs/learning-path.md](docs/learning-path.md)；
每层的验收标准、基建线进度见 [docs/roadmap.md](docs/roadmap.md)；
技术选型的决定和理由见 [docs/decisions.md](docs/decisions.md)；
当前工作队列见 [TODO.md](TODO.md)。

## 结构

按依赖方向分层，外层依赖内层：

- `domain`：纯概念。Message、四种内容块。不依赖 SDK。
- `application`：用例。会话循环（conversation.py）住在这里，TUI 和 desktop 共用。
- `infrastructure`：anthropic 和 openai 的客户端。把 SDK 的事件翻译成自己的类型。
- `interfaces`：入口。只负责输入输出。

仓库分两块，各管各的：

- `server/`：后端。`pyproject.toml`、`main.py`、代码、数据都在这里。
- `app/`：Tauri 桌面端。通过 HTTP 调用 server，不共享代码。

`server/src/` 下四个层各是一个包：

- `domain/message.py`：Message 和四种内容块（Text、Thinking、ToolUse、ToolResult）。
- `domain/tool.py`：Tool 定义（名字、说明、参数 schema）。执行不在这里。
- `application/conversation.py`：Conversation，唯一的会话循环（含工具循环）；入口只剩输入输出。
- `application/tools.py`：工具注册表、now_time 和 bash。
- `infrastructure/llm.py`：AnthropicChat 和 OpenAIChat，只管运输；build_chat 按配置挑类；merge_fragment 把流式片段并成块。
- `infrastructure/adapter.py`：协议适配器，两家的形状差别全在这里翻译。
- `infrastructure/config.py`：读写 config.ini，支持 `${环境变量}` 引用；对外只返回密钥来源，不回传密钥。
- `infrastructure/transcript.py`：会话正文，一个会话一个 JSONL 文件，存在 `server/data/`。
- `infrastructure/store.py`：会话索引和执行轨迹（spans，父子关系 + 耗时），存在 `server/data/agent.db`。
- `interfaces/tui/`：唯一的终端入口包；`__init__.py` 只导出 `run_chat` 与 `run_trace`，
  对话工作台和 trace 检查器分文件实现。
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

挂着系统代理的机器注意一个坑：`all_proxy=socks5://…` 会让模型流量尝试走
SOCKS，依赖里已带 `socksio`，装上即正常走代理；不想让模型流量走代理，
在 `[llm]` 里写 `no_proxy = *`（或逗号分隔的主机列表，留空 = 完全尊重
环境里的代理变量）。真踩到这一步，报错信息里会列出这几条解法。

运行中也能改模型配置：TUI / desktop 输入 `/config` 或按 `Ctrl+,`。可改 provider、
model ID、base URL、max tokens、effort 和 API key；
密钥留空表示保留原值，保存后下一轮请求生效。两个界面共用 `GET /settings` 和
`PUT /settings`，写入时只改 `[llm]` 的已知键并保留文件里的注释。

TUI 和 desktop 输入 `/` 都会打开命令面板：方向键选择、Tab 补全、Enter 执行。
可直接用 `/model <id>` 切模型、`/config` 打开设置，或用 `/now`、`/files`、
`/boundary` 触发预置任务；未知斜杠命令只在本地报错，不会发给模型。

在 `server/` 目录里启动：

```bash
cd server
uv run python main.py tui                 # 终端工作台，新开会话
uv run python main.py tui <session_id>    # 续接已有会话
uv run python main.py trace <session_id>  # 在 TUI 里检查执行轨迹
uv run python main.py desktop             # 桌面端 HTTP/SSE 服务，默认 8000
uv run python main.py web                 # desktop 的兼容别名
```

`main.py` 是唯一的进程启动器，只装配依赖并分发模式。桌面端在 `app/` 目录里启动：
`npm run tauri dev`，界面是三栏工作台——会话列表、对话流（思考折叠、工具卡、
Markdown）、执行账本。它目前不负责拉起后端，`main.py desktop` 需要单独跑着。
