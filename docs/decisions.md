# 技术选型记录

每做一个重要决定记一条：定了什么、为什么、否掉了什么。新条目放最下面，只追加不改写
（发现旧决定错了就记一条新的推翻它，注明被谁取代）。

---

## D1 分层：DDD 四层，src 下平铺三个包（2026-10-30）

**决定**：`domain / application / infrastructure / interfaces` 四层，`server/src/` 下各是一个包，
不套 `agentlearn` 外层包。

**理由**：学习项目的核心是把「谁依赖谁」看清楚。domain 是纯类型谁都不依赖；
infrastructure 翻译协议和存取；application 编排（循环住这里）；interfaces 只做输入输出。
映射到熟悉的三层：interfaces=controller，application=service，infrastructure=dao+第三方封装。

**否掉的备选**：
- 套一层 `src/agentlearn/`——只有一套代码，外层包没有第二个实现对照，白增嵌套（试过又拆掉）。
- 传统三层命名（service/dao）——概念能映射，但 application 这个名字时刻提醒「用例住这里」。

---

## D2 存储：JSONL 存正文，SQLite 存索引和日志（2026-10-30）

**决定**：一次会话一个 `data/sessions/<uuid>.jsonl`，一行一条完整消息只追加；
`data/agent.db` 两张表：sessions（索引）、logs（事件）。

**理由**：消息形状马上要变（第二层加 tool_use/tool_result），JSONL 无表结构，旧文件照样读；
对话只在末尾生长，追加天然契合。索引和日志是要查询的，放 SQLite。

**否掉的备选**：
- 全 SQLite——消息加字段要改表，学习期不想背迁移。
- 全 JSONL——「列出最近会话」「按会话查事件」是查询，顺序扫文件不合适。
- 两个库文件——两张表分两个 db 纯属折腾。

---

## D3 模型接入：双客户端 + adapter.py 翻译（2026-10-30）

**决定**：`AnthropicChat` / `OpenAIChat` 只管运输；字段差异全部收在 `adapter.py`；
domain 类型（四种内容块）的字段名照 Anthropic 线上格式定，OpenAI 方向在适配器里翻。

**理由**：循环层只应看见一套类型。选 Anthropic 词汇做基准，因为它的 `input` 已是字典，
比 OpenAI 的 JSON 字符串少一次解析。stop_reason 同样统一成 Anthropic 词汇
（OpenAI 的 stop/length/tool_calls 在 llm.py 翻成 end_turn/max_tokens/tool_use）。

**否掉的备选**：
- 只接一家——现实里中转/兼容端点两种协议都会遇到。
- LangChain 的统一模型接口——它连循环一起包了，正是要自己写的东西。

---

## D4 配置：config.ini + ${ENV} 引用（2026-11-01）

**决定**：`config.example.ini` 提交、`config.ini` gitignore；provider/base_url/api_key/model_id/max_tokens
五个键；任何值可写 `${VAR}` 表示从环境变量取；缺文件、缺键、环境变量没设、provider 非法，
一律启动即报错退出，不给默认值。

**理由**：config 是「启动前就有的结构化输入」（provider 这种枚举放 ini 顺手）；
密钥不落盘由 `${VAR}` 单独解决。fail-fast 让问题暴露在启动时而不是运行中。

**否掉的备选**：
- 纯 .env——provider/model 这类非环境配置放 env 语义别扭，没有 section 和注释位。
- 配置入 SQLite——密钥落盘；配置是输入不是运行时数据；db 还在 gitignore 里，换机器就丢。
- config 缺键时静默回退 env——出问题要查两个地方才知道值从哪来；`${VAR}` 让来源永远只有一个（文件）。

---

## D5 框架策略：不引入 LangChain / LangGraph（2026-11-01）

**决定**：学习期全部手写。LangChain 跳过；工具循环写完后读 LangGraph 的
StateGraph / Checkpointer 设计对照自写实现，写一篇对比笔记；做 trace 时参考
LangSmith 的 span 模型（父子关系 + 耗时）设计 logs 事件。

**理由**：框架的每个卖点都对应一块自建基建（见 README 的对照表）。没亲手痛过，
就看不懂它在解决什么；先手写再读是十倍效率。引入框架的时点：需要跨会话持久恢复、
人工审批门这类它做成产品的能力时，再按需评估。

---

## D6 桌面端：Tauri，前后端只通过 HTTP（2026-10-30）

**决定**：`app/` 用 Tauri（vanilla-ts 模板起步），与 server 之间只有 HTTP/SSE，不共享代码。

**理由**：注意力预算留给 agent 本身；Tauri 包小，壳薄。前后端进程独立，各自能单独跑。

**否掉的备选**：Electron（同语言排查省事，但包大；当时倾向 Tauri，用户拍板）。

---

## D7 session_id 一律 UUID（2026-10-30）

**决定**：会话 id 用 UUID，文件名即 id；`transcript.path_for` 校验格式并防路径穿越。

**理由**：id 会拼进文件路径，不校验就能被 `../` 写到目录外；UUID 无碰撞、无语义、可校验。

---

## D8 安全基线（2026-11-01）

**决定**：服务只绑 127.0.0.1（uvicorn 默认），CORS 只放行 Tauri 三个来源；
SQL 全参数化；工具执行永远禁止 eval/exec（calculator 用 ast 求值）。

**理由**：当前「无鉴权也安全」完全依赖只听本机回环。绑外网 / 加 --host 之前必须先加鉴权，
这一条记在 TODO「已知欠账」。
