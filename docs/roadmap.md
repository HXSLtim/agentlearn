# 路线

两条线并行：**学习线**（六层，自下而上盖楼）和**基建线**（工程能力，随用随建）。
每层有验收标准，过了才进下一层。当前进度同步在根目录 TODO.md。

## 学习线

### 第 1 层 一次调用 ✅（2026-10-30 完成）

发一条消息，拿回流式回答。内容块、事件流、stop_reason 的形状都亲眼看过。

验收：终端逐字输出思考和回答，最后单独一行 `end_turn`。✅

### 第 2 层 一个工具 + 手写循环 ✅（2026-11-01 完成）

给模型一个 calculator，自己写循环：收到 tool_use → 执行 → tool_result 追加进历史 →
再请求，直到 stop_reason 不是 `tool_use`。

验收：

- 一道多步算术题，模型调工具、拿到结果、给出正确答案；✅
  实测 (123*456)+789：模型调了两轮 calculator，答 56877（正确），
  trace 树 turn → llm_request / tool_call / llm_request / tool_call / llm_request。
- JSONL 里四种块齐全，重开会话能续上；✅ 续接会话直接答出 56877。
- 全程禁止 eval（ast 求值）。✅ 攻击样本（import/dunder/函数调用/巨型幂/变量名）
  全部拒绝，错误结果交回模型而不抛异常。

### 第 3 层 多工具、并行、失败

多个工具同时给；一次回复里并行多个 tool_use 靠 id 配对；工具失败回 `is_error` 让模型调整。

验收：并行两问两答互不串线；故意让一个工具抛错，模型能换路子或如实报告。

### 第 4 层 上下文管理

历史变长之后：prompt caching、截断策略、压缩摘要。对应 LangGraph 的 compaction 一类能力。

验收：一个几十轮的会话 token 消耗曲线平下来，回答仍记得开头说过的事。

### 第 5 层 MCP

工具从外部进程来：起一个 MCP server，把它的工具接进注册表。翻译后仍是同一个 ToolUseBlock。

验收：不改正循环代码，只加配置，就能用上一个外部工具。

### 第 6 层 Skills

按需加载的指令包（名字、说明、正文，必要时带脚本），教 agent 某类任务怎么做。

验收：一个没加载 skill 时答不好的任务，加载后明显变好，且不占用常驻上下文。

## 基建线（与学习线并行，按需启动）

| 项 | 内容 | 状态 |
|---|---|---|
| 会话持久化 | JSONL + SQLite（见 docs/decisions.md D2） | ✅ |
| 配置体系 | config.ini + ${ENV}（D4） | ✅ |
| conversation.py | 把 cli/web 两份循环收成 application 层一份（D9） | ✅ |
| trace | spans 表（父子 + 耗时，D10/D11）+ `main.py trace` 回放；turn/llm_request 已埋，tool_call span 随第二层接入 | ✅ 骨架 |
| 评测 | 从 trace 回放打分：工具调用对不对、答案准不准、花了多少 token | 未开始 |
| 桌面端接通 | app 前端接 POST /chat；Tauri 启动时拉起 server | 未开始 |

## 文档维护节奏

- `docs/decisions.md`：每做一个选型决定加一条。
- `docs/roadmap.md`（本文件）：每层完成时勾状态、记验收结果。
- 根目录 `TODO.md`：工作队列，随改随动。
- 根目录 `CHANGELOG.md`：每完成一层或一项基建记一版，不逐 commit。
