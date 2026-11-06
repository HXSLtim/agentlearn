"""一次对话的用例编排。这一层叫 application。

TUI 和 desktop/web 两个入口共用的循环收在这里：入口只剩输入输出，
「一轮对话怎么走」只写这一份。这里是 agent loop 本体：

    用户提问 -> 模型回答
       └─ stop_reason == tool_use？-> 执行工具 -> 结果交回 -> 再请求模型
          （循环，直到模型不再要工具，或达到轮数上限）

每一圈是一次独立的厂商请求：POST 全部历史 -> SSE 流回 -> 流关。
「交回结果」不是在原流里续写，是整段历史重发一遍（API 无状态）。
圈与圈之间因此存在真实空隙：工具执行时间 + 新请求的首 token 延迟
（TTFT，记进 llm_request span 的 ttft_ms）。软中断插话插的就是这个空隙。

历史维护方式统一为「每轮从 JSONL 整体重读」：文件是唯一事实来源，
崩溃或重启后天然一致，本地小文件的读取开销可以忽略。

turn() 产出的事件是 dict，两个入口消费同一份：
    {"kind": "thinking" | "text", "text": "..."}        流式片段
    {"kind": "tool_use", "id", "name", "input"}         模型要调工具，执行前就发
    {"kind": "tool_result", "id", "name", "output",
     "is_error", "elapsed_ms"}                          工具跑完，带结果和耗时
    {"kind": "usage", "usage"}                           每次成功模型请求后，发本轮累计 token
    {"kind": "context_warning", "estimate_tokens",
     "budget_tokens", "ratio"}                           工作集接近预算时，每会话发一次
    {"kind": "steering_injected", "text"}               软中断的消息并入当前轮（圈界处）
id 是这一次调用的标识，和落盘的 ToolUseBlock.id 是同一个值：
一次回复里并行多个工具时，前端靠它把 tool_result 填回对应的 tool_use，
不能靠事件顺序猜。TUI 把事件排进时间线，desktop/web 原样塞进 SSE。

trace 的埋点也在这里：turn span 是一棵树的根，树上是循环走过的每一层——
每次模型请求一个 llm_request span，每次工具执行一个 tool_call span
（见 store.py 的 spans 表）。`main.py trace` 在 TUI 里把这棵树展开。

跨会话记忆（2026-11-03）：每轮 turn 拼一次 system 提示词，随每次请求重发。
system 提示词每圈拼一次、不落 JSONL（派生状态不是历史）。四段顺序固定——
准则（server/constitution.md，人维护，模型改不了）、当前模型与思考档位
（运行态）、记忆（data/sandbox/memory.md，模型自己维护）、会话摘要
（第 4 层压缩的派生物，见 compaction.py）。协议差异在 adapter
（Anthropic 顶层字段 / OpenAI 首条消息）。准则在最前，前缀最稳，
缓存断点打在最后一条消息上，命中的前缀整段包含它们。
"""
import json
import time
from pathlib import Path

from domain.message import ImageBlock, Message, TextBlock, ToolResultBlock
from infrastructure.adapter import estimate_tokens
from infrastructure.llm import AnthropicChat, OpenAIChat
from infrastructure.store import Store
from infrastructure.transcript import Transcript

from application.approval import APPROVAL_MODES, ApprovalBroker
from application.plan import PLAN_ALLOWED_TOOLS, PlanBroker
from application.tools import SANDBOX, all_tools, read_memory, run_tool
from application import compaction

# 一轮对话里最多让模型连续调几轮工具。没有这个上限，模型抽风反复调用时
# 会一直烧 token 停不下来——agent 循环永远要有退出条件。
# 这是全局默认；Conversation 可以按会话覆盖（max_tool_rounds 参数，
# TUI 的 /rounds 命令运行中改），见 Conversation.__init__。
MAX_TOOL_ROUNDS = 8
CONTEXT_WARNING_RATIO = 0.8

class TurnCancelled(Exception):
    """这一轮被抢占（steering 的 preemptive 档）打断。

    半截回答不落盘——模型对被打断的回答失忆，这是抢占档的语义。
    落盘侧是自洽的：已写入的都是完整消息（问题、tool_use、tool_result），
    缺的只是那段没生完的 assistant 回答。入口捕获它后向自己的 SSE 推
    steering_cancelled 事件；turn span 记 status=cancelled（区别于 error）。
    """


class Conversation:
    """一个会话的编排器。

    持有四个依赖：模型客户端、正文存储、索引存储、会话 id。
    生命周期内可以连续 turn 多轮，历史由它负责追加和落盘。
    """

    def __init__(
        self,
        chat: AnthropicChat | OpenAIChat,
        transcript: Transcript,
        store: Store,
        session_id: str,
        memory_path: Path | None = None,
        constitution_path: Path | None = None,
        steering=None,
        approval: ApprovalBroker | None = None,
        plan_broker: PlanBroker | None = None,
        approval_mode: str = "approve",
        context_budget_tokens: int | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        # 构造时就校验 session_id（必须是 UUID），非法在这里抛 ValueError。
        # TUI 把它转成一句提示退出，web 端点在推流前拦下返回 422。
        self.session_path = transcript.path_for(session_id)
        self.chat = chat
        self.transcript = transcript
        self.store = store
        self.session_id = session_id
        # 跨会话记忆的来源文件。sandbox 本来就跨会话留存（agent 的便签都住在
        # 那里），memory.md 只是给「醒来要带的行李」一个固定位置——agent 在
        # 自己的工作目录里 echo >> memory.md 就能写下记忆。参数可注入，
        # 测试换临时文件，不碰真 sandbox。
        self.memory_path = memory_path if memory_path is not None else SANDBOX / "memory.md"
        # 行为准则。放在 server/ 下而不是 sandbox 里：sandbox 是模型自己的
        # 便签目录，准则若放进去，就和记忆混成同一个它能维护的文件。
        # 这不是安全边界——bash 用绝对路径照样写得到（D17）。拆开只为了
        # 注入时准则在前、记忆在后，记忆被污染不会把准则整段换掉。
        # parents[2]：本文件在 src/application/ 下，上两级是 server/。
        self.constitution_path = (
            constitution_path
            if constitution_path is not None
            else Path(__file__).resolve().parents[2] / "constitution.md"
        )
        # steering 是同会话并发的钩子（见 sessionManager.py），鸭子类型：
        #   cancelled() -> bool      被抢占了吗（preemptive 档设的标志）
        #   drain() -> list[str]     取走等待插入的新消息（cooperative 档的 inbox）
        # TUI 单连接用不上，None = 没有并发，所有检查点都变空操作。
        self.steering = steering
        self.approval = approval
        self.plan_broker = plan_broker
        self._turn_plan_mode = False
        if approval_mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        self.approval_mode = approval_mode
        # 第 4 层的压缩预算（估算 token）：「摘要 + 尾部」超过它就把前缀
        # 压掉。测试传一个小值逼压缩发生；两个入口不传，用 compaction 的缺省。
        self.context_budget_tokens = (
            context_budget_tokens
            if context_budget_tokens is not None
            else compaction.DEFAULT_BUDGET_TOKENS
        )
        # 工具轮数上限，per-session 可调（构造参数或运行中改属性都行）。
        # 默认 8 是「防模型抽风烧 token」的保险丝，不是什么讲究的数；
        # 调试长循环（比如耐心那层的轮询）时在 TUI 里 /rounds 调高，
        # 不用改代码重启。钳到 >=1：0 会让循环一次都不跑。
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        # 当前轮的 turn span id。抢占检查点要用它把 span 收成 cancelled，
        # 检查点散落在循环各处、拿不到局部变量，挂在自己身上。
        # TUI 串行复用同一个 Conversation，每次 turn() 开头覆盖，安全。
        self._turn_span: str | None = None
        # 本轮开了还没收尾的 span。GeneratorExit 只会落在 yield 上，而 yield
        # 散在循环各处、拿不到局部变量；这里记一份，生成器被消费者关闭时
        # （web 端 SSE 断开）统一收成 cancelled，trace 不再永久挂「运行中」。
        self._open_spans: list[str] = []
        # 最近一轮的结束原因，turn 完整跑完才有值。
        # 带 tools 的一轮可能经历多次请求，这里是最后一次的。
        self.stop_reason: str | None = None
        # 最近一轮所有成功模型请求的 token 总和。chat.usage 每次请求都会
        # 被覆盖，工具循环不能只看最后一次；预算熔断要的是整轮累计。
        self.turn_usage_tokens = 0
        self.turn_usage = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cache_hit_ratio": 0,
        }

    def _record_usage(self, usage: dict) -> dict:
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            self.turn_usage[key] += int(usage.get(key, 0) or 0)
        total_input = (
            self.turn_usage["input_tokens"]
            + self.turn_usage["cache_read_tokens"]
            + self.turn_usage["cache_write_tokens"]
        )
        self.turn_usage["cache_hit_ratio"] = (
            round(self.turn_usage["cache_read_tokens"] / total_input * 1000)
            if total_input
            else 0
        )
        return dict(self.turn_usage)

    @staticmethod
    def _usage_tokens(usage: dict) -> int:
        return sum(
            int(usage.get(key, 0) or 0)
            for key in (
                "input_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "output_tokens",
            )
        )

    def _context_warning(
        self, history: list[Message], summary_text: str, covered: int,
        turn_span: str,
    ) -> dict | None:
        """工作集接近预算时，给两个入口发一次告警。

        这里的估算与 context_report/plan_cut 用同一套账：摘要 + 未覆盖尾部。
        span 是去重事实——web 每轮新建 Conversation，不能把去重放在实例里；
        有过 context_warning 后，同会话不再重复刷。
        """
        _, _, estimate_tokens_total = self._context_estimate(
            history, summary_text, covered
        )
        ratio = estimate_tokens_total / self.context_budget_tokens
        if ratio < CONTEXT_WARNING_RATIO:
            return None
        if any(
            row["name"] == "context_warning"
            for row in self.store.spans_for(self.session_id)
        ):
            return None
        span = self._start_span(
            "context_warning",
            parent_id=turn_span,
            data={
                "estimate_tokens": estimate_tokens_total,
                "budget_tokens": self.context_budget_tokens,
                "ratio": round(ratio, 3),
            },
        )
        self._end_span(span, status="ok")
        return {
            "kind": "context_warning",
            "estimate_tokens": estimate_tokens_total,
            "budget_tokens": self.context_budget_tokens,
            "ratio": round(ratio, 3),
        }

    @staticmethod
    def _context_estimate(
        history: list[Message], summary_text: str, covered: int
    ) -> tuple[int, int, int]:
        summary_tokens = (
            estimate_tokens(summary_text)
            if summary_text
            else compaction.SUMMARY_TOKENS_RESERVE
        )
        tail_tokens = compaction.estimate_history_tokens(history[covered:])
        return summary_tokens, tail_tokens, summary_tokens + tail_tokens

    def _system_prompt(self, chat: AnthropicChat | OpenAIChat, summary_text: str = "") -> str:
        """拼这一圈的 system。四段顺序固定，见本文件顶部的说明。

        准则文件缺了或是空的，运行态照样带上——模型至少知道自己是谁、
        在哪一档。记忆为空就不加第三段，会话没有摘要就不加第四段，
        不留空标题。传入本轮固定的 chat 快照，而不是 self.chat：
        运行中切模型或思考档位只影响下一轮，不能把本轮已经发出的半套配置换掉。
        """
        parts: list[str] = []
        if self.constitution_path.exists():
            constitution = self.constitution_path.read_text(encoding="utf-8").strip()
            if constitution:
                parts.append(constitution)
        # effort_note 是发给模型的那个词：留空和 off 都是 omit（请求不带参数），
        # OpenAI 的 max 被夹成 high 时这里是 max→high，和 trace 里记的是同一个值。
        effort = chat.effort_note or "omit"
        if effort == "omit":
            effort_line = "当前思考档位：未设置（请求不带思考强度参数，用服务端默认）"
        else:
            effort_line = f"当前思考档位：{effort}"
        parts.append(
            f"当前模型：{chat.model}\n"
            f"{effort_line}\n"
            "档位由用户在界面里切换，你改不了。按这个深度分配思考。"
        )
        if self._turn_plan_mode:
            parts.append(
                "当前是 PLAN MODE：只做只读探索，不能改文件、跑 shell、"
                "启动/停止任务或调用外部 MCP 工具。最终回答必须是一份"
                "可执行计划，包含目标、步骤、涉及文件、验证方式和风险；"
                "不要执行计划，等待用户确认。"
            )
        memory = read_memory(self.memory_path).strip()
        if memory:
            parts.append(
                "以下是你的跨会话记忆（data/sandbox/memory.md，主要由你自己维护，"
                "可以用 bash 更新它：追加、改写、删去过期的；带「用户」前缀的行"
                "是用户用 /memory 命令记下的，改写时保留其原意）。"
                "这里只放事实，不放行为准则；准则在上面，改记忆不会改掉它。\n\n"
                + memory
            )
        if summary_text:
            parts.append(
                "以下是这次对话更早内容的摘要（上下文压缩生成的派生物，"
                "原文存档在会话文件里、不再随请求发送）。"
                "把它当作已经发生过的事实。\n\n" + summary_text
            )
        return "\n\n".join(parts)

    def compact_now(self) -> dict:
        """手动压缩（/compact 命令）：不等预算触发，立刻把能压的压掉。

        复用圈界压缩的全部机制（同一次 llm_request、同一个摘要文件、
        同样的失败退路），只把预算当 0：plan_cut 会退到最深的合法切割点。
        没东西可压（历史还没盖过 covered，或只有一个轮次）时如实报告。
        调用方要保证此刻没有轮次在跑（会话锁），否则摘要文件会和
        运行轮的圈界压缩互相踩。
        """
        history = self.transcript.read(self.session_id)
        path = self.transcript.summary_path_for(self.session_id)
        before = compaction.load_summary(path)
        tail, summary_text, covered = self._apply_compaction(
            history, self.chat, None, force=True
        )
        return {
            "messages_total": len(history),
            "covered_before": before.covered if before else 0,
            "covered": covered,
            "compressed": (before.covered if before else 0) < covered,
            "summary_chars": len(summary_text),
            "tail_messages": len(tail),
        }

    def context_report(self) -> dict:
        """/context 的数据：这个会话现在「背着多少上下文」。

        三种数各有各的真值来源：估算是「下一次请求会发多大」的预测
        （摘要 + 尾部历史 + system）；usage 是上一次请求厂商报的实数
        （含缓存命中）；预算是压缩的工作集上限。估算管什么时候压、
        实数管花了多少钱，混在一张表里正好对得上账。
        """
        history = self.transcript.read(self.session_id)
        path = self.transcript.summary_path_for(self.session_id)
        summary = compaction.load_summary(path)
        summary_text = summary.text if summary else ""
        covered = min(summary.covered, len(history)) if summary else 0
        summary_est, tail_est, _ = self._context_estimate(
            history, summary_text, covered
        )
        system_chars = len(
            self._system_prompt(self.chat, summary_text)
        )
        # 上一次请求的实数（usage 记在 llm_request span 的 data 里）。
        last_usage: dict = {}
        for row in reversed(self.store.spans_for(self.session_id)):
            if row["name"] != "llm_request" or not row["data"]:
                continue
            data = json.loads(row["data"])
            if "input_tokens" in data or "cache_read_tokens" in data or "output_tokens" in data:
                last_usage = {
                    key: data[key]
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_tokens",
                        "cache_write_tokens",
                        "cache_hit_ratio",
                    )
                    if key in data
                }
                break
        return {
            "messages_total": len(history),
            "covered": covered,
            "summary_chars": len(summary_text),
            "estimate": {
                "summary_tokens": summary_est,
                "tail_tokens": tail_est,
                "system_chars": system_chars,
                "budget_tokens": self.context_budget_tokens,
            },
            "last_usage": last_usage,
        }

    def _apply_compaction(
        self, history: list, chat: AnthropicChat | OpenAIChat, turn_span: str,
        force: bool = False,
        count_turn_tokens: bool = False,
    ) -> tuple[list, str, int]:
        """圈界处的上下文压缩：返回 (发给模型的尾部历史, 摘要文本, covered)。

        不超预算时原样返回（摘要文件若已有covered，仍要切掉已覆盖的前缀——
        那段只存在于摘要里）。超了就把 covered 到切割点之间的原文交给模型
        并进旧摘要，写回摘要文件。压缩本身是一次 llm_request，挂 span
        （purpose=compaction），trace 里看得见它花了多少 token。
        压缩请求失败不挡对话：沿用旧摘要和原来的未覆盖尾部，账记在 span 上。
        """
        path = self.transcript.summary_path_for(self.session_id)
        summary = compaction.load_summary(path)
        # covered 理论上不会超过历史长度（JSONL 只追加）；摘要文件被手改过
        # 就钳住，宁可重发全文也不发一段悬空的尾部。
        covered = min(summary.covered, len(history)) if summary else 0
        summary_text = summary.text if summary else ""
        # force（/compact 手动压缩）：预算当 0 用——plan_cut 里什么都不塞得进，
        # 退到最深的合法切割点（最后一个轮次边界），把能压的全压掉。
        budget = 0 if force else self.context_budget_tokens
        cut = compaction.plan_cut(history, covered, summary_text, budget)
        if cut is not None and cut > covered:
            prompt = compaction.build_summary_prompt(
                summary_text, compaction.render_for_summary(history[covered:cut])
            )
            span = self._start_span(
                "llm_request",
                parent_id=turn_span,
                data={
                    "purpose": "compaction",
                    "dropped": cut - covered,
                    "budget": self.context_budget_tokens,
                    # 单次覆盖档（llm.stream 的 effort 参数）：摘要不需要
                    # 深度思考，固定 low，不跟用户档位走。span 里记一声，
                    # trace 才分得清它和主请求的档位为什么不一样。
                    "effort": "low",
                },
            )
            pieces: list[str] = []
            try:
                for kind, text in chat.stream(
                    [Message("user", [TextBlock(prompt)])], None,
                    system="", effort="low",
                ):
                    if kind == "text":
                        pieces.append(text)
                    # 压缩流也让出给消费者：抢占在压缩中途照样生效，
                    # 已花的压缩 token 记在 cancelled 的 span 上。
                    self._checkpoint()
            except TurnCancelled:
                self._end_span(span, status="cancelled")
                raise
            except Exception as e:
                # 摘要是派生物，压不成不该拖垮对话。沿用旧摘要对应的
                # covered 边界：把已覆盖原文再发一遍会让模型看到两份事实。
                self._end_span(span, status="error", data={"message": str(e)})
                return history[covered:], summary_text, covered
            new_text = "".join(pieces).strip() or summary_text
            self._end_span(
                span,
                status="ok",
                data={**chat.usage, "summary_chars": len(new_text)},
            )
            if count_turn_tokens:
                self.turn_usage_tokens += self._usage_tokens(chat.usage)
                self._record_usage(chat.usage)
            compaction.save_summary(path, cut, new_text)
            covered, summary_text = cut, new_text
        tail = history[covered:] if covered else history
        return tail, summary_text, covered

    def _approval_mode_now(self) -> str:
        """当前轮的执行等级；入口能在同一个 turn 里实时改它。"""
        if self.steering is not None:
            getter = getattr(self.steering, "approval_mode", None)
            if callable(getter):
                return getter()
        return self.approval_mode

    def _start_span(
        self,
        name: str,
        parent_id: str | None = None,
        data: dict | None = None,
    ) -> str:
        """开 span 并登记进本轮未收尾清单——断连收尾要靠这份清单找回 id。"""
        span_id = self.store.start_span(
            self.session_id, name, parent_id=parent_id, data=data
        )
        self._open_spans.append(span_id)
        return span_id

    def _end_span(
        self, span_id: str, status: str = "ok", data: dict | None = None
    ) -> None:
        """收 span：先摘出清单再回填。正常收尾的不再出现在断连收尾里。"""
        if span_id in self._open_spans:
            self._open_spans.remove(span_id)
        self.store.end_span(span_id, status=status, data=data)

    def _abort_open_spans(self) -> None:
        """生成器被提前关闭（客户端断开）时，把本轮还开着的 span 收成 cancelled。

        GeneratorExit 不是 Exception 的子类，循环里的 except 都接不住；
        不收尾就会永久停在 running——D11 原策略说这叫「如实反映没跑完」，
        但 trace 上它和真在跑的轮次长得一模一样（都是「运行中」+ 无限时长），
        实际观感是假运行、还越积越多。cancelled 才是如实：没跑完，但结束了。
        """
        while self._open_spans:
            span_id = self._open_spans.pop()
            self._end_span(
                span_id, status="cancelled", data={"note": "客户端断开，本轮中止"}
            )

    def _checkpoint(self) -> None:
        """抢占检查点：在把事件交给消费者之后、开新的厂商请求之前调用。

        生成器在消费者要下一个事件时才恢复执行，恢复的这一刻是我们能感知
        取消的最快时机。真·零等待抢不动一个阻塞中的网络读，抢占档的等待
        上限是「下一个事件到达或下一圈开始」，这点如实记在 D18。
        被抢占时把 turn span 收成 cancelled（半截回答不落盘），再抛
        TurnCancelled 给入口——怎么向用户表现打断是入口的事。
        """
        if self.steering is not None and self.steering.cancelled():
            if self._turn_span:
                self._end_span(
                    self._turn_span, status="cancelled",
                    data={"note": "被抢占打断，半截回答未落盘"},
                )
            raise TurnCancelled

    def _append_disconnect_tool_results(
        self, assistant_blocks: list, results: list
    ) -> None:
        """客户端断开时补齐 tool_use/tool_result 配对。

        GeneratorExit 可能落在 tool_use、审批或 tool_result 事件上；这里
        不猜断点，只对照 assistant 块补缺失结果，并把已有结果一起落盘。
        span 不在这里收——turn 外层统一按 cancelled 收尾（见 _abort_open_spans）。
        """
        completed_ids = {result.tool_use_id for result in results}
        missing = [
            block for block in assistant_blocks
            if block.type == "tool_use" and block.id not in completed_ids
        ]
        results.extend(
            ToolResultBlock(
                tool_use_id=block.id,
                content="Client disconnected before this tool call completed.",
                is_error=True,
            )
            for block in missing
        )
        if results:
            self.transcript.append(self.session_id, Message("user", results))

    def turn(
        self,
        question: str,
        origin: str = "",
        append_user: bool = True,
        images: list[ImageBlock] | None = None,
        plan_mode: bool = False,
    ):
        """跑一轮对话。公共入口只做一件循环体里做不到的事，循环在 _turn_impl。

        消费者提前关闭生成器（web 端 SSE 断开 → close()）时，GeneratorExit
        会从内层冒出来。它不是 Exception 的子类，循环里的 except 接不住；
        在这里统一把本轮还开着的 span 收成 cancelled 再放行，trace 才不会
        永久停在「运行中」。
        """
        try:
            yield from self._turn_impl(
                question,
                origin=origin,
                append_user=append_user,
                images=images,
                plan_mode=plan_mode,
            )
        except GeneratorExit:
            self._abort_open_spans()
            raise

    def _turn_impl(
        self,
        question: str,
        origin: str = "",
        append_user: bool = True,
        images: list[ImageBlock] | None = None,
        plan_mode: bool = False,
    ):
        """跑一轮对话，逐个产出事件（dict），直到给出最终回答。

        事件的形状见本文件顶部。两个入口拿到的是同一份：
        TUI 排进时间线，web 原样推进 SSE，谁都不需要再拼装工具的入参出参。
        一轮的轨迹是 turn span 一棵树：循环里每次模型请求挂一个
        llm_request，每次工具执行挂一个 tool_call（`main.py trace` 回放）。
        流中途出错时：给出错的 span 标上 error（共同动作，记在这里），
        然后把异常抛回给入口（怎么表现错误是入口的事）。
        半截回复不落盘——存一半的对话和用户看到的不一致，宁可不要；
        已落盘的 user 消息留着，下一轮模型会看到这次没答上的问题。

        append_user=False 是重试模式（P0「重试去重」契约）：失败的轮次
        已经把 user 消息落了盘，重试是对既有历史重新发起请求，不再追加
        一条同文 user（否则模型看到同一句问题问了两遍）。此时 question
        传空串，历史尾部必须是 user 消息——可能是纯文本（请求前就失败），
        也可能是 tool_result（工具循环中途失败，重试从工具结果接着跑，
        这正是想要的续跑语义）；不是 user 说明没有可重试的半截轮。
        """
        # 一轮开始后固定同一个客户端：TUI 里可以随时改模型或思考档位，
        # 但当前轮的工具循环不能中途换客户端，否则同一条 trace 会混两套配置。
        chat = self.chat
        self._turn_plan_mode = plan_mode
        self.turn_usage_tokens = 0
        self.turn_usage = self._empty_usage()
        if not append_user and images:
            raise ValueError("重试不落新消息，不能带图。")
        if append_user:
            # 第一次真正有消息时才登记会话：启动后立刻退出的会话
            # 不再在 sessions 表里留下指向不存在文件的行。续接时表里已有，跳过。
            if not self.store.has_session(self.session_id):
                self.store.create_session(
                    self.session_id, chat.model, str(self.session_path)
                )
            # 标题 = 第一条用户消息截断（会话索引 GET /sessions 和 TUI /resume
            # 都靠它认会话）。set_title_if_empty 只写空标题，桌面端以后起的
            # 正式名字不会被覆盖。
            clean = " ".join(question.split())
            self.store.set_title_if_empty(
                self.session_id, clean[:64] + ("…" if len(clean) > 64 else "")
            )
        else:
            # 重试模式的前置校验放在 turn span 之前：校验失败不该在 trace
            # 里留一个 running 的孤儿 span。
            existing = self.transcript.read(self.session_id)
            if not existing or existing[-1].role != "user":
                raise ValueError(
                    "没有可重试的轮次：历史尾部不是用户消息"
                    "（可能已重试过、轮次已成功、或还没问过）。"
                )

        # 本轮新的未收尾清单：上一轮若被异常打断，残留的旧清单不归这轮管，
        # 不能拿它去收尾（那会把别的轮次的 span 标成这次的断连）。
        self._open_spans = []
        # turn span：整轮的根，不挂父。它下面的所有 span 都算这轮的开销。
        # origin：这轮是谁发起的——空 = 界面里的人敲的；"trigger:<名>"
        # = 无人值守轮（D27）。trace 里区分「人的轮次」和「它自己的轮次」
        # 全靠这一个字段。
        turn_span = self._start_span(
            "turn", data={"origin": origin} if origin else None
        )
        self._turn_span = turn_span

        if append_user:
            # 附图（@file / 粘贴图片）：文字块在前图块在后，存储和线上
            # 形状都在 adapter / domain 已定（D25 的管道复用）。
            blocks: list = [TextBlock(question)]
            if images:
                blocks.extend(images)
            user = Message("user", blocks)
            self.transcript.append(self.session_id, user)
        # 整体重读而不是内存累积：见本文件顶部的说明。重试模式上面校验时
        # 读过一次，这里再读：校验和此刻之间没有本会话的写入（会话锁在手），
        # 两次是同一份，重读换的是代码路径统一。
        history = self.transcript.read(self.session_id)

        # 工具定义在这里取一次。它随下面的每次请求重申（API 无状态，
        # 「注册」不是一次性的，是每回都带）。
        tools = all_tools()
        if plan_mode:
            tools = [tool for tool in tools if tool.name in PLAN_ALLOWED_TOOLS]

        # agent loop：最多 max_tool_rounds 次「请求 -> (要工具就执行再请求)」。
        for _ in range(self.max_tool_rounds):
            # 圈界 = 请求间隙 = 软中断（cooperative steering）的插入点。
            # 排干 inbox：等在这里的新消息先落盘、发一条事件，再整体重读
            # 历史——下一圈请求里模型就「看得见」它们。此时上一个流和上一个
            # 工具都已经跑完，这正是 cooperative 的语义：等待单位是一次请求。
            # 落盘后历史尾部会出现 user(tool_result) + user(新消息) 的连续
            # user，Anthropic 方向由 adapter.history_to_anthropic 合并。
            if self.steering is not None:
                self._checkpoint()
                injected = self.steering.drain()
                for text in injected:
                    self.transcript.append(
                        self.session_id, Message("user", [TextBlock(text)])
                    )
                    # 事件发在当前流上：这轮的 SSE 消费者看得见插入了什么。
                    yield {"kind": "steering_injected", "text": text}
                    self._checkpoint()
                if injected:
                    history = self.transcript.read(self.session_id)

            # 第 4 层的圈界动作：超预算就把前缀压成摘要，请求只发尾部。
            # 放在圈界而不是 turn 开头——工具循环里历史也会长，每圈都要看。
            history, summary_text, covered = self._apply_compaction(
                history, chat, turn_span, count_turn_tokens=True
            )
            # system 每圈拼一次：四段固定（准则、运行态、记忆、会话摘要）。
            # 字符串不变时字节就不变，前缀稳定，缓存断点（打在最后一条消息上，
            # 见 llm.py）命中的整段前缀里包含它；变的只有压缩刚发生的那一圈。
            # 不落 JSONL——它是随请求重发的派生状态，不是历史。
            system = self._system_prompt(chat, summary_text)
            # llm_request span：挂在 turn 下。开始时就知道的（模型名、消息条数）
            # 现在记，结束才知道的（stop_reason）由 end_span 补。
            span_data = {
                "model": chat.model,
                # 这里记的是发给模型的尾部条数，covered 之前的历史在摘要里。
                "messages": len(history),
                # 记忆有没有带上、带了多少，记长度不记内容——spans 只记
                # 控制流（D11），内容以文件为准，trace 里不复制一遍。
                "system_chars": len(system),
            }
            if covered:
                span_data["compaction_covered"] = covered
            llm_span = self._start_span(
                "llm_request", parent_id=turn_span, data=span_data
            )

            # TTFT（首 token 延迟）：从发起请求到第一个片段回来。
            # stream 是生成器，第一次迭代才真正发出网络请求，所以 t0 放 for 前面。
            # 这段等待 = 网络往返 + 厂商排队 + prefill（重读全部历史）+ 出第一个字；
            # 工具循环每圈都重付一次，历史越长 prefill 越久。
            t0 = time.monotonic()
            ttft: float | None = None

            try:
                for kind, text in chat.stream(history, tools, system=system):
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    # 模型的 tool_use 片段只有名字，入参还在流里分片、这里拿不到，
                    # 跳过它：完整的 tool_use 事件等这轮流结束、从 blocks 里发。
                    if kind == "tool_use":
                        continue
                    yield {"kind": kind, "text": text}
                    # 让出后恢复的第一件事：看有没有被抢占。
                    self._checkpoint()
            except TurnCancelled:
                # 被抢占不是出错：llm span 收成 cancelled（这次请求是被掐的），
                # turn span 已由 _checkpoint 收尾，这里只结束自己这一层再往外抛。
                self._end_span(llm_span, status="cancelled")
                raise
            except Exception as e:
                # 出错：两个 span 都标 error，错误消息记在子 span 上（离错误最近）。
                self._end_span(llm_span, status="error", data={"message": str(e)})
                self._end_span(turn_span, status="error")
                raise
            # 注意：客户端中途断开（web 端 SSE 被掐掉）时，生成器收到的是
            # GeneratorExit，它不是 Exception 的子类，不走上面的 except——
            # 未收尾的 llm/turn span 由 turn 外层的 _abort_open_spans 统一
            # 记成 cancelled（D33 修订了 D11「留 running」的旧策略）。

            # 完整内容块从客户端拿（Anthropic：最终消息，含 signature 和解析好的
            # 入参；OpenAI：流里拼好）。落盘用完整块，不用展示片段。
            blocks = chat.blocks
            self.transcript.append(self.session_id, Message("assistant", blocks))
            stop = chat.stop_reason
            # ttft 和用量都拆开记：总耗时里藏着 ttft，token 和缓存比例藏在 usage 里。
            # effort 只在配置了才记。被夹档时 note 是 max→high，否则就是档位本身。
            data = {"stop_reason": stop, "ttft_ms": round(ttft * 1000) if ttft is not None else None,
                    **chat.usage}
            if chat.effort:
                data["effort"] = chat.effort_note
            self.turn_usage_tokens += self._usage_tokens(chat.usage)
            usage_snapshot = self._record_usage(chat.usage)
            self._end_span(llm_span, status="ok", data=data)
            yield {"kind": "usage", "usage": usage_snapshot}
            warning = self._context_warning(
                self.transcript.read(self.session_id),
                summary_text,
                covered,
                turn_span,
            )
            if warning is not None:
                yield warning

            if stop != "tool_use":
                # 模型不再要工具，这一轮到此为止。
                self.stop_reason = stop
                plan_id = ""
                if plan_mode:
                    plan_text = "\n".join(
                        block.text
                        for block in blocks
                        if block.type == "text" and block.text.strip()
                    )
                    if self.plan_broker is not None and plan_text.strip():
                        plan_id = self.plan_broker.create(
                            self.session_id, plan_text
                        )
                end_data = {"plan_id": plan_id} if plan_id else None
                self._end_span(turn_span, status="ok", data=end_data)
                if plan_id:
                    yield {
                        "kind": "plan",
                        "plan_id": plan_id,
                        "text": self.plan_broker.get(plan_id).text
                        if self.plan_broker is not None
                        else "",
                    }
                return

            # stop_reason == "tool_use"：模型发起了工具调用（一条回复里可能有多个）。
            # 逐个执行，配对靠 id 不靠顺序（并行调多个时顺序不可靠）。
            # 这一整段没有抢占检查点，是刻意的：assistant(tool_use) 已经落盘，
            # 对应的 user(tool_result) 不跟上，这个会话就再也续不上
            # （Anthropic 要求配对）。「配对不变式」优先于抢占的及时性——
            # 工具批次一旦开跑就完整落盘，取消在下一圈圈界生效（D18）。
            results = []
            for block in blocks:
                if block.type != "tool_use":
                    continue
                # 执行前先报一次：前端可以立刻渲染「正在调用 calculator(12*11)」，
                # 不用干等工具跑完。id 带上，并行多个调用时结果靠它配对。
                try:
                    yield {"kind": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                except GeneratorExit:
                    self._append_disconnect_tool_results(blocks, results)
                    raise
                approval_mode = self._approval_mode_now()
                tool_span = self._start_span(
                    "tool_call",
                    parent_id=turn_span,
                    data={
                        "tool": block.name,
                        "input": block.input,
                        "approval_mode": approval_mode,
                    },
                )
                decision = None
                approved: bool | None = True
                approval_id: str | None = None
                plan_blocked = plan_mode and block.name not in PLAN_ALLOWED_TOOLS
                if plan_blocked:
                    approved = False
                    content = (
                        "Plan mode blocked this tool call. Only read-only tools "
                        "are available before the user confirms the plan."
                    )
                    status = "plan_blocked"
                    is_error = True
                    image = None
                elif (
                    self.approval is not None
                    and self.approval.requires_approval(approval_mode, block.name)
                    and not self.approval.is_session_approved(
                        self.session_id, block.name
                    )
                ):
                    approval_id = self.approval.open(
                        block.name, session_id=self.session_id
                    )
                    register = (
                        getattr(self.steering, "register_approval", None)
                        if self.steering is not None
                        else None
                    )
                    if callable(register):
                        # 登记与读档位在同一把锁里，关掉「刚切到 yolo、
                        # 审批却已按旧档位挂起」的竞态。
                        approval_mode = register(approval_id, block.name)
                    try:
                        if self.approval.requires_approval(approval_mode, block.name):
                            yield {
                                "kind": "tool_approval",
                                "id": block.id,
                                "approval_id": approval_id,
                                "name": block.name,
                                "input": block.input,
                                "mode": approval_mode,
                            }
                        else:
                            self.approval.decide(approval_id, True)
                        decision = self.approval.wait_with_reason(approval_id)
                        approved = decision.approved if decision is not None else None
                    except GeneratorExit:
                        self.approval.cancel(approval_id)
                        self._append_disconnect_tool_results(blocks, results)
                        raise
                    finally:
                        forget = (
                            getattr(self.steering, "forget_approval", None)
                            if self.steering is not None
                            else None
                        )
                        if callable(forget):
                            forget(approval_id)
                if plan_blocked:
                    pass
                elif approved is not True:
                    if approved is None:
                        content = "Timed out waiting for user approval."
                        status = "timeout"
                    else:
                        content = "Tool execution was denied by the user."
                        if decision is not None and decision.reason:
                            content += f" Reason: {decision.reason}"
                        status = "denied"
                    is_error = True
                else:
                    # run_tool 永不抛异常：出错也变成文本结果交回，模型自己调整。
                    # 第三个返回值是附带的图（眼睛）：纯文本工具恒为 None。
                    content, is_error, image = run_tool(block.name, block.input)
                    status = "error" if is_error else "ok"
                span_data = {
                    "output": content,
                    "approval_id": approval_id,
                    "approval_mode": approval_mode,
                }
                if plan_mode:
                    span_data["plan_mode"] = True
                if status == "denied" and decision is not None and decision.reason:
                    span_data["denial_reason"] = decision.reason
                if image is not None:
                    # 图绝不进 span（D11：spans 只记控制流，base64 一张几十 K），
                    # 记个大小留痕：这张图进过模型上下文。
                    span_data["image_bytes"] = len(image.data) * 3 // 4
                self._end_span(tool_span, status=status, data=span_data)
                # 耗时用 span 的起止时间，和 trace 回放是同一个算法（store.span_duration_ms）：
                # 前端看到的 elapsed_ms 跟 `main.py trace` 打出来的毫秒数一致。
                elapsed_ms = self.store.span_duration_ms(tool_span)
                # 跑完再报一次：出参、是否出错、耗时。id 和上面的 tool_use 相同。
                # has_image 只报有无：SSE 事件不搬 base64（前端渲染图片是
                # 另一件事，见 TODO），正文以 JSONL 为准。
                results.append(ToolResultBlock(
                    tool_use_id=block.id, content=content, is_error=is_error, image=image
                ))
                try:
                    yield {
                        "kind": "tool_result",
                        "id": block.id,
                        "name": block.name,
                        "output": content,
                        "is_error": is_error,
                        "denied": approved is not True,
                        "has_image": image is not None,
                        "elapsed_ms": elapsed_ms,
                    }
                except GeneratorExit:
                    self._append_disconnect_tool_results(blocks, results)
                    raise

            # 工具结果作为下一条 user 消息交回（Anthropic 协议如此；
            # OpenAI 方向由 adapter 拆成 role="tool" 的独立消息）。
            self.transcript.append(self.session_id, Message("user", results))
            # 带着工具结果整体重读历史，进下一轮请求——模型这时「看得见」结果了。
            history = self.transcript.read(self.session_id)
        else:
            # for 循环自然走完（没从 return 出去）= 轮数用尽模型还在要工具。
            # 强行收住并在轨迹里留痕；stop_reason 保持最后一次的 "tool_use"，
            # 如实反映「是我不想再跑了」，不是模型说完了。
            self.stop_reason = chat.stop_reason
            self._end_span(turn_span, status="ok", data={"note": "达到工具轮数上限"})
