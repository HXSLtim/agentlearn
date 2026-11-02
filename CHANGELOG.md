# Changelog

每完成一层或一项基建记一版，不逐 commit。新条目放最上面。

## 2026-11-02（二）：第 3 层——多工具、并行、失败

- 工具集换血：calculator 下架（ast 白名单的写法留在 git 46ef2e1 和
  learning-path 第 2 层），换成 now_time（无参、永不失败）+ bash
  （shell=True，管道/重定向/$() 全真实语义，10 秒超时，cwd 在 data/sandbox）。
  先试过 shlex.split 直 exec，等于向模型承诺残缺 shell（$((...))、> 全是
  字面量），改回 shell=True——安全面无实质差别（python -c 本就是任意代码执行）。
- 并行验收 ✅：一条回复里同时要 now_time 和 bash，两个 tool_call 挂同一
  llm_request，id 配对不串线（时间 + 56877 都正确）。
- 失败验收 ✅ 两档：exit 1 → 模型 pwd/ls 定位后如实报告；模型自发 find /
  全盘扫描超时 → 逐步降级到朴素命令最终答对。
- 修一个真 bug：shell=True 超时路径 TimeoutExpired.stdout/stderr 是 bytes
  （text=True 不生效），原代码 str+bytes 直接 TypeError，靠 run_tool 兜底
  才没断链；现在统一 `_as_text` 转换，超时报错带部分输出、说人话。
- 已知边界如实记录（TODO）：bash 的 cwd 不是安全边界，绝对路径可绕过，
  学习期接受，对外前必须容器化；超时只 kill shell，后台子进程可能残留。

## 2026-11-02：用量与缓存比例进 span

- llm_request span 增加五个字段：input_tokens / output_tokens /
  cache_read_tokens / cache_write_tokens / cache_hit_ratio（千分比）。
  翻译在 adapter.py：usage_from_anthropic / usage_from_openai 翻成同一套键。
  两家口径不同——Anthropic 的 input_tokens 不含缓存，OpenAI 的 prompt_tokens
  是总量，要减掉 cached_tokens 才对齐，这个减法收在 adapter 里。
- OpenAI 路径加 stream_options.include_usage：用量在 [DONE] 前最后一个 chunk，
  且该 chunk 的 choices 为空，原来「choices 为空就跳过」会把它丢掉。
- 实测中转透传这些字段。同一轮工具循环：第一圈 cache 0/0，第二圈
  cache_read=192、hit=747‰——没写 cache_control 也有命中，第四层是稳住它
  而不是从零打开。

## 2026-11-01（四）：第 2 层——工具循环

- agent loop 本体落在 conversation.py：stop_reason == "tool_use" 时执行工具、
  tool_result 作为 user 消息交回、整体重读历史再请求，直到模型不再要工具；
  MAX_TOOL_ROUNDS=8 兜底（用尽时 turn span 记 note，stop_reason 如实留 tool_use）。
- 第一个工具 calculator（application/tools.py）：ast 白名单求值，禁 eval。
  实测攻击样本全部拒绝：函数调用、属性探路、语句、lambda、变量名、巨型幂、bool。
  run_tool 永不抛异常——出错/未知工具/参数错都变成 is_error 结果交回模型。
- 工具注册 = 每次请求重申定义：domain/tool.py 的 Tool，all_tools() 随请求带上。
  出站翻译 tool_to_anthropic / tool_to_openai 收在 adapter.py。
- llm.py：stream 增加 tools 参数；落盘块改从完整消息取（Anthropic：最终消息，
  顺带把 thinking 的 signature 回填，消掉该欠账；OpenAI：流里自拼）。
  新增 "tool_use" 片段（只报工具名，给入口做提示）。
- OpenAI 方向适配器补齐（离线验证，未接真实端点）：出站 message_to_openai 改为
  返回列表——工具结果拆成独立 role="tool" 消息、助手调用变 tool_calls 数组
  （arguments 为 JSON 字符串）；入站 merge_tool_call_delta + tool_uses_from_acc
  按 index 拼分片，arguments 解析回字典，与 Anthropic 的 input 对齐。
- tool_call span 接入 trace：挂在 turn 下，data 记工具名/入参/出参/是否出错。
- 验收：真实模型算 (123*456)+789，调两轮 calculator，答 56877（正确）；
  JSONL 四种块齐全；重开会话续接直接答出 56877。
- 记录选型 D12：自建 runtime 而不是站在 OpenCode 上做 MCP+Skills——
  交付生意与产品生意的区别，站在别人 runtime 上最难的 30% 够不着。

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
