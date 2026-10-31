# Changelog

每完成一层或一项基建记一版，不逐 commit。新条目放最上面。

## 2026-11-01（三）：trace 骨架

- store.py 的 logs 表退役，spans 表接棒（D11）：一行一个 span（turn / llm_request，
  第二层加 tool_call），span_id / parent_id / start_time / end_time / status / data；
  开始 insert 结束 update，参考 LangSmith 的 span 模型（父子 + 耗时）。
  start_span / end_span / spans_for 三个方法，log / logs_for 删除。
- conversation.py 埋点：每轮一个 turn 根 span，每次模型请求一个 llm_request 子 span
  （开始记模型名和消息条数，结束补 stop_reason）；出错两个 span 都标 error。
  客户端断开时生成器收到 GeneratorExit（不是 Exception），span 停在 running——
  如实反映「这轮没跑完」。
- 新增 `main.py trace <session_id>` 子命令：把 spans 画成缩进树打印（名称/状态/耗时），
  cli.py 的 show_trace。实测正常轮显示耗时、被掐断的轮显示「未结束」。
- 记录部署形态选型 D10：外部系统只持一个会话（POST /chat 换 session_id），
  工具执行、正文、trace 全在本地；HTTP 面保持极小。
- 评测欠账显式化：token 用量尚未记进 span（两条路径取法不同，第四层前补上）。

## 2026-11-01（二）：application 层成立

- `application/conversation.py`：Conversation 成为两入口唯一的会话循环——
  登记、user 落盘、整体重读历史、流转发、assistant 落盘、request/error 日志都在 turn() 里；
  cli.py / web/server.py 缩成纯输入输出。选型与三个随路决定见 docs/decisions.md D9。
- 路径锚定：main.py 启动时 `os.chdir` 到 server/，从仓库根启动 data/ 与 config.ini
  也落在 server/ 下（实测仓库根启动一轮对话，正文进 server/data/sessions/）。
- 配置惰性加载：import server 模块不再读 config.ini（实测 `_cached_config is None`）；
  web 启动时 fail-fast 读一次。
- 会话登记改惰性：第一次真有消息才进 sessions 表，启动即退出的会话不再留空行
  （实测空会话不登记、已用会话登记且 logs 有 request 记录）。
- 验证：cli 续接记忆正常；web 非法 session_id 仍 422、SSE 出流、done 事件回传
  session_id、正文落盘。

## 2026-11-01

- 协议适配器 `infrastructure/adapter.py` 成立：出站两个方向（message_to_anthropic /
  message_to_openai）已接进 llm.py 的请求路径；入站 block_from_anthropic 用真实返回
  验证过一轮 calculator tool_use，等工具循环接线。
- domain 新增 ToolUseBlock / ToolResultBlock，内容块从两种扩到四种。
- 配置体系：config.example.ini 模板 + `infrastructure/config.py`（支持 `${环境变量}` 引用，
  四类错误启动即报）+ `build_chat` 按 provider 挑客户端。url/key/model 不再硬编码。
- 会话 id 统一为 UUID，`transcript.path_for` 校验并防路径穿越。
- review 一轮可读性：两份入口重复的「片段并块」收成 `merge_fragment`；
  `_to_openai` 迁入 adapter；README 结构清单、CLAUDE.md、TODO.md 建立。
- 安全自查：无鉴权仅靠绑定 127.0.0.1；CORS 收紧到 Tauri 三来源；SQL 全参数化。
  结论与遗留见 TODO「已知欠账」。
- sub agent 可维护性 review 落地：stop_reason 归一化（OpenAI 的 stop/length/tool_calls
  翻成统一词汇，防工具循环在 OpenAI 路径静默失效）；SSE 增加 error 事件、非法
  session_id 在推流前返回 422（此前 200 已发出只能断流）；cli 出错不再裸退 traceback，
  一轮作废继续下一轮；SQLite 关闭同线程检查（SSE 同步生成器跑在线程池）；
  空 choices chunk 防护；三处说错的注释纠正；list_sessions/logs_for 标注预留用途。
  review 其余结构性发现（路径锚定、配置惰性加载、循环收拢）并入 conversation.py 一并做。
- 建立文档体系：docs/decisions.md（D1-D8 选型记录）、docs/roadmap.md（六层学习线 +
  基建线与验收标准）、CLAUDE.md（项目认知）、TODO.md、CHANGELOG.md。

## 2026-10-30

- 初始骨架（commit 39fcaa2）：server 按 domain / infrastructure / interfaces 分层，
  AnthropicChat + OpenAIChat 流式调用，多轮历史，JSONL 会话正文 + SQLite 索引与日志，
  main.py 子命令入口；app/ 为 Tauri 模板（vanilla-ts）。
