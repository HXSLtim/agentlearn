# TODO

学习路线总览见 docs/roadmap.md，选型理由见 docs/decisions.md。
这里的条目完成后移进 CHANGELOG，不删历史。

## 下一步（已排期）

第三层（多工具、并行、失败），清单在「以后」段。第二层已完成：
calculator + agent loop + tool_call span。

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
