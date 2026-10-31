# TODO

学习路线总览见 docs/roadmap.md，选型理由见 docs/decisions.md。
这里的条目完成后移进 CHANGELOG，不删历史。

## 下一步（已排期）

第二层工具循环（清单在下面那节）。trace 骨架已就位：spans 表 + `main.py trace`
回放命令，工具执行处只要 `start_span(session_id, "tool_call", parent_id=turn_span)`
再 `end_span`，轨迹里就自动多一层。

## 第二层：一个工具 + 手写循环

- [ ] `llm.py` 的 stream 增加 tools 参数
- [ ] tool_use 块接进请求路径：block_from_anthropic 已写好并验证过，尚未接线
- [ ] calculator 工具：**禁止 eval**，用 ast 解析表达式树求值
- [ ] 循环本体：收到 tool_use → 执行 → tool_result 追加进历史 → 再请求，
      直到 stop_reason 不是 "tool_use"
- [ ] 工具执行处挂 tool_call span（parent 是当轮 turn span），
      data 记工具名/入参/出参/是否出错
- [ ] OpenAI 方向适配器补齐：入站 tool_calls（arguments 是 JSON 字符串，流式时逐片到达要先拼）；
      出站工具结果要变成单独一条 role="tool" 消息

## 已知欠账

- [ ] 思考块 signature 现在存空字符串：流式片段里没有它，只在最终消息里。
      当前中转不校验所以没炸；换成会校验的接口前必须从最终消息回填，否则续会话 400
- [ ] token 用量没记进 span：Anthropic 路径 get_final_message().usage 现成可取；
      OpenAI 路径要加 stream_options={"include_usage": true} 才有。
      第四层（上下文管理）和评测都需要，届时补在 llm_request span 的 data 里
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
