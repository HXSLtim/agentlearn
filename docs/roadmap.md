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

### 第 3 层 多工具、并行、失败 ✅（2026-11-02 完成）

多个工具同时给；一次回复里并行多个 tool_use 靠 id 配对；工具失败回 `is_error` 让模型调整。
工具集换血：calculator 下架，换成 now_time（无参、永不失败）+ bash（有副作用、会失败）。

验收：

- 并行两问两答互不串线；✅ 一条回复里同时要 now_time 和 bash（算 123*456+789），
  两个 tool_call 挂在同一个 llm_request 下，id 各自配对，答案都正确
  （时间 11:23:44 UTC、56877）。
- 故意让一个工具抛错，模型能换路子或如实报告。✅ 实测两档：
  `cat report.txt` exit 1 → 模型 pwd/ls 定位 → 如实报告不存在并主动看 note.txt；
  更强的一档：模型自作主张 `find /` 扫全盘超时（10s 上限触发）→ 逐步降级到
  朴素命令 → 最终正确回答。超时那次的错误消息还暴露并修掉了一个真 bug
  （shell=True 超时路径 TimeoutExpired 给 bytes，text=True 不生效）。
- 执行仍是串行的（一个跑完再跑下一个）；验收要的是配对不串线，同时执行
  （asyncio/线程池）与工具间资源冲突留到以后。

### 第 4 层 上下文管理

历史变长之后：prompt caching、截断策略、压缩摘要。对应 LangGraph 的 compaction 一类能力。

验收：一个几十轮的会话 token 消耗曲线平下来，回答仍记得开头说过的事。

### 第 5 层 MCP

工具从外部进程来：起一个 MCP server，把它的工具接进注册表。翻译后仍是同一个 ToolUseBlock。

验收：不改正循环代码，只加配置，就能用上一个外部工具。

### 第 6 层 Skills

按需加载的指令包（名字、说明、正文，必要时带脚本），教 agent 某类任务怎么做。

验收：一个没加载 skill 时答不好的任务，加载后明显变好，且不占用常驻上下文。

## 待排期：意图识别模型（2026-11-03 记入）

在主模型之前加一个小而快的分类模型，先判用户这句话想干什么，再决定怎么走。
动机：现在每句话都进完整的 agent loop（带工具定义、带记忆、带全部历史），
而大量输入其实是闲聊、改口、或一句话就能答的简单问题——分类先行可以把
「要不要进循环、带哪些工具、用哪一档 steering」这些决定从主模型身上拿下来。

候选用途（做的时候挑，不预设全做）：

- 路由：闲聊直接答 / 任务进 agent loop / 明确的命令走快捷路径；
- 工具裁剪：只把相关工具的定义放进请求，工具多了以后省 token（和第 5 层 MCP 衔接）；
- steering 档位建议：判断新消息是补充（cooperative）还是改口推翻（preemptive），
  现在这个选择完全靠前端传参。

约束：分类器是新增的一次模型调用，本身有延迟和成本，省不下主模型的开销就
不值得加；分类结果记进 span（和 effort 一样留痕），错分要能从 trace 里看见。
选型（用谁、什么粒度、失败时的降级）定了记 decisions.md。

## 基建线（与学习线并行，按需启动）

| 项 | 内容 | 状态 |
| --- | --- | --- |
| 会话持久化 | JSONL + SQLite（见 docs/decisions.md D2） | ✅ |
| 配置体系 | config.ini + ${ENV}（D4） | ✅ |
| conversation.py | TUI/desktop 共用 application 层一份会话循环（D9/D14） | ✅ |
| TUI | 黑绿控制台：流式正文、thinking 滚动预览、按事件顺序挂载工具卡、运行账本、窄终端布局（D14/D16） | ✅ |
| trace | spans 表（D10/D11）+ `main.py trace` 只读 TUI：span 树、耗时、token、工具入参/出参、回答 | ✅ |
| 设置界面 | TUI / desktop 共用 `GET/PUT /settings`；只改 `[llm]`，密钥不回显，下一请求生效（D15） | ✅ |
| 快捷命令 | TUI / desktop 输入 `/` 打开面板；模型切换与预置 prompt 均走界面层白名单（D18） | ✅ |
| 跨会话记忆 | `data/sandbox/memory.md` 每轮注入为 system 提示词；不进 JSONL，协议差异在 adapter；span 记 system_chars | ✅ |
| steering | 同会话连发消息三档（D19）：queue 等整轮 / cooperative 圈界并入 / preemptive 检查点打断；spans 加 cancelled；抢占让位于 tool_use/tool_result 配对不变式 | ✅ |
| 评测 | 从 trace 回放打分：工具调用对不对、答案准不准、花了多少 token | 未开始 |
| 桌面端 | app 已接 POST /chat + SSE、会话侧栏、运行账本和设置抽屉；Tauri 自动拉起 server 未做 | 🟡 |

## 文档维护节奏

- `docs/decisions.md`：每做一个选型决定加一条。
- `docs/roadmap.md`（本文件）：每层完成时勾状态、记验收结果。
- 根目录 `TODO.md`：工作队列，随改随动。
- 根目录 `CHANGELOG.md`：每完成一层或一项基建记一版，不逐 commit。
