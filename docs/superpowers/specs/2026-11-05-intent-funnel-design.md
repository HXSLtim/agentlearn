# 意图识别漏斗设计：规则 → few-shot → 微调兜底

- 日期：2026-11-05　分支：`intent-funnel`　状态：草案，待用户审
- 对应 roadmap「待排期：意图识别模型」；实现时的选型与验收结论记 decisions.md（D32 起）。

## 背景与动机

现在每条用户消息都进完整的 agent loop：全量工具定义、memory、整份历史、
最多 8 圈工具循环。而真实会话里大量输入是闲聊、改口、或一句话就能答的
简单问题。意图漏斗在主模型之前先判「这句话想干什么」，把「要不要带工具、
要不要进循环」从主模型身上拿下来。

约束（roadmap 已写明，这里继承为验收条件）：漏斗自己有延迟和成本，
**省不下主模型的开销就不启用**；分类结果要进 span，错分要能从 trace 看见。

## 三层结构

```
用户消息
  ├─ L1 规则（正则/关键词，数据文件）────── 命中即短路，0 模型调用
  ├─ L2 few-shot prompt（现有通道+便宜小模型）── 置信够 → 采纳
  └─ L3 微调兜底（本地 MLX LoRA 小模型）──── L2 低置信才调
       ↓ 任何一层失败/低置信到底
     按 task 走完整 agent loop（降级不变式，见下）
```

### L1 规则

- 文件 `server/rules/intent.json`（进库）。加规则 = 丢文件，同 skills 的思路。
- 形状：`[{"id": "...", "pattern": "...", "label": "command"|"chat", "action": {...}}]`。
- **command 标签只从 L1 来**：模型能判「这是条命令」却不知道映射哪个动作，
  所以 L2/L3 只分 chat/task，command 的「判据 + 动作绑定」整体由规则携带。
  v1 的 action 只支持 `{"tool": 名字, "args": 固定参数}`。
- 规则文件损坏（JSON 解析失败）：该层跳过进 L2，启动告警但不退出——
  规则文件坏不该拦着人聊天。
- 命中 `chat` pattern（一眼闲聊）同样短路。

### L2 few-shot prompt

- 走现有 `build_chat` 通道，但用 `[intent]` 段单独配的便宜小模型
  （provider/model 独立于主 `[llm]`）。
- prompt = 任务说明 + 标注例句（`server/rules/intent_examples.jsonl`，
  一行一条 `{"text": ..., "label": ...}`，同时也是金标集的底子）+ 输出契约。
- 输出契约：严格 JSON `{"label": "chat"|"task", "confidence": 0..1}`。
  解析失败 / 超时（`timeout_s`）→ 一律当低置信，落 L3。
- `confidence >= confidence_threshold` 采纳；低于则调 L3。

### L3 微调兜底

- 基座：1B 级 instruct 小模型（Qwen2.5-1.5B-Instruct / Llama-3.2-1B 里选，
  训练时按这台机器的显存与效果定，结论记 decisions.md）。
- 训练管线放 `server/training/`：离线工具，不进 src 四层——它不是运行时代码，
  塞进任何一层都违反「层里只放运行时职责」。
- 数据：从 `data/sessions/*.jsonl` 采样真实用户消息 → L2 预标 → 人工复核 →
  金标集。train/eval 分开，eval ≥ 100 条，进库（体积小，是可复现的资产）。
- 推理：mlx-lm 本地加载 LoRA。`application/intent.py` 直连，
  **不经 adapter**——意图分类不是对话流量，没有两家协议翻译的问题，
  走 adapter 反而会把「协议差异」的边界稀释掉。
- 权重目录不存在 / 加载失败 → 该层视为不可用，低置信结果直接归 task。

## 标签集（v1）

| label | 判据 | 动作 |
| --- | --- | --- |
| `chat` | 不需要工具、不需要工作区状态 | 主模型照调，但**不带工具定义**（历史/记忆照带），一轮直答 |
| `task` | 需要工具 / 文件 / 后台任务 / 多步 | 完整 agent loop（现状，不动） |
| `command` | 仅 L1：整句就是一条明确指令 | 短路执行绑定的动作，再一次无工具主模型调用组织成回答 |

边界例句（进 examples 文件，兼作 L2 few-shot 素材）：

- 「今天状态不错」→ chat
- 「帮我把 data 里的会话按工具使用次数排个序」→ task
- 「现在几点」→ command（L1 命中 `now_time`）

已知局限（记录，不解决）：分类只看当前消息文本，不带对话上文。
「那再来一张截图」这种依赖上文的意图，孤立看更像闲聊——这类样本进
金标集时**默认标 task**（安全方向：宁可多走完整循环）。

## 调用点与数据流

- 新增 `application/intent.py`：
  `classify(question) -> IntentResult(layer, label, confidence, rule_id, model, latency_ms)`。
- 挂点：`Conversation.turn()` 里 `tools = all_tools()` 之前
  （conversation.py:578 附近）。chat → `tools = []`；command → 短路分支；
  task / 漏斗失败 → 现状不动。
- **跳过漏斗的轮次**：`append_user=False`（重试）、`plan_mode`、
  `origin` 非空（触发器注入，定义上就是任务）。
- 分类结果**不落 JSONL**：路由决策是派生状态不是历史（同 system/摘要的
  原则），记 span——`intent_classify` 挂 turn 下，
  `data = {layer, label, confidence, rule_id?, model?, latency_ms}`；
  `layer=fallback` 表示漏斗整体失败归 task。
- v1 不发 UI 事件，两端不感知（trace 里可见）；界面展示路由留作后续条目。

## 降级不变式

漏斗任何一层失败，最终一律归 `task` 走完整循环。
错分的代价 = 多花一次完整循环（性能损失），**绝不 = 任务失败（功能损失）**。
方向不能反：宁可漏斗白跑，不能漏斗杀任务。

## config

```ini
[intent]
enabled = false              # 实测省得回主模型开销才开（验收第 4 条）
provider = openai            # L2 走哪个通道（复用 build_chat，不填 = 跟 [llm] 同源）
model =                      # L2 用的小模型 id
max_tokens = 200
timeout_s = 5                # L2 超时按低置信处理
confidence_threshold = 0.7   # L2 低于此值落 L3
rules = rules/intent.json
examples = rules/intent_examples.jsonl
lora_model = data/intent/lora # L3 权重目录；不存在 = L3 不可用（合法状态）
```

`config.example.ini` 同步补段；`${ENV}` 展开规则照旧；设置界面
（GET/PUT /settings）v1 不动 `[intent]`——它是实验旋钮，不是运行配置。

## 评估与验收

1. 金标集：真实 transcript 采样 ≥ 200 条用户消息，AI 预标 + 人工复核；
   含依赖上文类、歧义类样本。
2. 分层报表：从 spans 聚合——每层命中率、准确率、P50/P95 延迟、
   L2 的 token 成本。
3. 降级演练：规则文件损坏 / L2 超时 / L3 缺权重，三案都归 task
   且 `intent_classify` span 的 layer 可查。
4. 算账：开启后 chat 轮省下的工具定义 token vs 漏斗自身开销
   （L2+L3 的调用与延迟）。结论写 decisions.md；不划算就 `enabled=false`
   收档，漏斗代码留着当教材。
5. 错分可查：trace TUI 与 desktop trace 抽屉里，`intent_classify`
   span 显示 layer/label/confidence。

## 非目标（v1 明确不做）

- 工具裁剪（task 内部挑工具）：v2。等工具多了、trace 攒了真实调用分布再做。
- steering 档位建议：触发时机不同（只在有运行轮时），单独一刀。
- 分类结果落 JSONL / UI 实时展示路由。
- 规则自动生成、在线学习。

## 与后续工作的关系

ReAct / agent loop 对照 / multi-agent 是**第二个子项目**，不受本 spec 约束。
目录调整只挪位置不改行为；`intent.py` 落 application 根，`rules/`、`training/`
为 server 顶层新目录，与目录调整方案兼容。
