# TODO

学习路线总览见 docs/roadmap.md，选型理由见 docs/decisions.md。
这里的条目完成后移进 CHANGELOG，不删历史。

## 下一步（已排期）

第三层（多工具、并行、失败），清单在「以后」段。第二层已完成：
calculator + agent loop + tool_call span。

## 排队中：同会话消息的 steering（三档）

用户在同一个会话里连发消息时不能交错写 JSONL。前端做防抖（一次只飞一条），
服务端按三档处理（设计定稿，实现在 SessionManager，application 层）。
三者的唯一差别是等待颗粒度：一整轮 / 一次请求 / 不等。

术语对照（agent 圈把「执行中途改方向」统称 steering；抢占/协作这对区分
来自操作系统调度，1960 年代的词，比 agent 圈的叫法更精确）：

- [ ] queuing（顺延）：本轮 end_turn 后按队列顺序处理新消息。等待单位：一整轮
- [ ] cooperative steering（软中断）：在 yield point 插入——agent loop 每圈是一次
      完整的厂商请求（POST 全历史 → SSE 流回 → 流关），请求间隙即让出点；
      当前流与当前工具让它们跑完。已确认可行：落盘规则是每步只追加完整消息，
      任何间隙打断 JSONL 都自洽。等待单位：一次请求
- [ ] preemptive steering（强制插入）：立即关掉当轮生成器（GeneratorExit 在 yield
      点触发，SDK 的 with 块自动关 HTTP 流）；半截回答不落盘，模型对被打断的
      回答失忆。等待单位：零
- [ ] SessionManager：session_id → 队列 + 正在跑的轮次 + steering 标志
      （现在每请求独立 new Conversation，没有跨请求状态可挂）。
      Store 也收归它持有：现在每个请求各自 sqlite3.connect 一个新连接，
      复用成进程级一个连接。注意复用不消除写锁——SQLite 锁的是整个库文件，
      多连接抢锁和单连接排队是同一份串行，省掉的只是建连接开销
- [ ] 随路三件事：spans 加 cancelled 状态（区分打断与崩溃）；adapter 在请求构建时
      合并连续 user 消息（yield point 后必然出现 user(tool_result) + user(新消息)）；
      工具是否可取消算工具自己的属性（MCP 层的事）

## 第二层：一个工具 + 手写循环 ✅（2026-11-01）

- [x] `llm.py` 的 stream 增加 tools 参数
- [x] tool_use 块接进请求路径：block_from_anthropic 接线，落盘块改从最终消息取
- [x] calculator 工具：**禁止 eval**，用 ast 解析表达式树求值（白名单）
- [x] 循环本体：收到 tool_use → 执行 → tool_result 追加进历史 → 再请求，
      直到 stop_reason 不是 "tool_use"（MAX_TOOL_ROUNDS=8 兜底）
- [x] 工具执行处挂 tool_call span（parent 是当轮 turn span），
      data 记工具名/入参/出参/是否出错
- [x] OpenAI 方向适配器补齐：入站 tool_calls（arguments 逐片拼接后解析回字典）；
      出站工具结果拆成单独的 role="tool" 消息（离线验证，未接真实 OpenAI 端点）

## 已知欠账

- [ ] 思考块 signature 现在存空字符串：流式片段里没有它，只在最终消息里。
      **Anthropic 路径已从最终消息回填（第二层顺带解决）**；OpenAI 路径
      的 reasoning 不回传历史，signature 仍是空串，换会校验的接口前确认它不校验
- [ ] token 用量没记进 span：Anthropic 路径 get_final_message().usage 现成可取；
      OpenAI 路径要加 stream_options={"include_usage": true} 才有。
      第四层（上下文管理）和评测都需要，届时补在 llm_request span 的 data 里
- [ ] SQLite 并发写：现在靠默认 busy_timeout（5 秒）扛——库级写锁，第二个写者
      等锁释放，超时抛 database is locked。四五个会话的写入量碰不到；
      并发真上来时连接加 `PRAGMA journal_mode=WAL`（读写互不挡），
      同时做上面 SessionManager 那条的连接复用。两件事独立，都不急。
      不上连接池：SQLite 连接是打开本地文件，开销微秒级且写锁是库级的，
      池化不增加写并发；也不上 ORM：全部 SQL 就七条，裸 SQL 是故意的
      （参数化、锁、WAL 都要看得见），等表多到需要迁移再重新评估
- [ ] 跨会话的工具冲突不归循环管：calculator 是纯函数所以安全；工具一旦碰
      文件/进程/外部系统（第五层 MCP），冲突发生在工具碰的资源上。
      解法是工具侧的：按 session_id 隔离工作目录，或工具声明自己能否并行。
      与第三层「一个会话内并行多个 tool_use」是两个方向，别混
- [ ] web 无鉴权：现在安全仅因为 uvicorn 默认只绑 127.0.0.1。
      绑 0.0.0.0 / 加 --host 之前必须先加鉴权
- [ ] Tauri 一键启动：app 启动时拉起 server 进程（现在是两边各开各的）
- [ ] app 前端还是模板欢迎页，没接 POST /chat
- [ ] pyproject 把 domain/infrastructure/interfaces 装成顶层包名，与第三方包撞名风险低，
      出现痛点再收窄成 `agentlearn.*` 命名空间

## 以后（路线图后段，暂不展开）

- 第三层：多工具、并行调用、失败重试、多轮
- 第四层：上下文变长后的缓存、截断、压缩
- 第五层：MCP；第六层：Skills
- 读完 LangGraph 的 StateGraph / Checkpointer 设计，对照自写的 Conversation 写对比笔记
