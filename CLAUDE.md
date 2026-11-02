# CLAUDE.md

给在这个仓库工作的 AI 助手（和半年后的作者）的项目认知。人读 README，机器读这份。

## 这是什么项目

从零手写一个 agent 的学习项目。逐层自建，每一层先自己跑通再往上叠。
不引入 LangChain / LangGraph——它们是参考读物，不是依赖。
学习路线和当前进度见 README.md 和 TODO.md。

## 布局

- `server/`：Python 后端。入口 `main.py`（子命令 tui / trace / desktop，web 为兼容别名）。
  - `src/domain/`：纯数据类型，不依赖任何层。
  - `src/infrastructure/`：llm.py（运输）、adapter.py（协议翻译）、config.py、transcript.py（JSONL 正文）、store.py（SQLite 索引 + trace/spans）。
  - `src/interfaces/tui/`：唯一终端入口包；`__init__.py` 是门面，chat.py / trace.py 是两个内部模式。
  - `src/interfaces/web/server.py`：desktop 的 HTTP/SSE 适配层。
  - `src/application/`：conversation.py，TUI 与 desktop 共用的会话循环（turn 落盘、日志都在这）。
- `app/`：Tauri 桌面端，通过 HTTP 调 server，不共享代码。
- `docs/`：`decisions.md`（选型记录，只追加）+ `roadmap.md`（路线与验收标准）。
- `data/`（运行时产生，gitignore）、`config.ini`（gitignore，模板 config.example.ini）。

## 运行

```bash
cd server
uv run python main.py tui [session_id]              # 终端工作台，可续接
uv run python main.py trace <session_id>            # 只读 TUI，检查 spans 树
uv run python main.py desktop [--port 8000]         # 桌面端 HTTP 服务
uv run python main.py web [--port 8000]             # desktop 兼容别名
```

- 配置：复制 `config.example.ini` 为 `config.ini`。任何值可写 `${环境变量名}`。
- 模型设置可在 TUI / desktop 的运行界面里改（`Ctrl+,`）：两边共用
  `GET /settings` / `PUT /settings`，只改 `[llm]` 的已知键；API key 留空保留原值，
  响应只给来源不回显密钥，保存后下一次模型请求生效。
- 本机代理坑：`all_proxy=socks5://...` 会让 anthropic SDK 因缺 socksio 在建客户端时报错。
  跑之前 `env -u all_proxy -u ALL_PROXY -u http_proxy -u https_proxy ...`，或设大写 `NO_PROXY`。

## 代码约定（必须遵守）

1. 分层依赖方向：interfaces → application → domain ← infrastructure。
   domain 永远不 import infrastructure / interfaces。
2. Anthropic 和 OpenAI 的字段差异只许出现在 `infrastructure/adapter.py`。
   llm.py 只管收发，出现字段名判断就是放错了地方。
3. 中文教学式密集注释是刻意的（作者在学），不要删减；过期、说错的注释要修。
4. `session_id` 一律 UUID，进文件路径前必须过 `transcript.path_for` 的校验。
5. 执行模型给的参数时禁止 `eval` / `exec`；工具失败必须变成带 `is_error` 的结果交回模型。
6. 层和文件只在真正需要时建，不提前铺空目录。
7. 消息存盘必须存完整内容块（含 thinking 的 signature），只存可见文字会导致会话续不上。
8. `main.py` 只做组合根；终端只保留 `interfaces/tui` 一个公开门面，不要再恢复 cli.py 或并列的 trace_tui.py。
9. 设置写入必须保留 `config.ini` 注释和其他段落；先写临时文件并完整校验，失败不能覆盖原配置。

## 已知坑与欠账

见 TODO.md 的「已知欠账」段，动相关代码前先读它。

## 版本库纪律

- `data/`、`server/config.ini`、虚拟环境、node_modules 不进库（已在 .gitignore）。
- 文档四件套的更新时机：做了选型决定 → docs/decisions.md 加一条；
  完成一层/一项基建 → docs/roadmap.md 勾状态、CHANGELOG.md 记一版（不逐 commit）；
  TODO.md 随改随动。
