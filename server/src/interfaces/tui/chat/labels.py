"""界面枚举值/收条标题的中文显示名。

与 WebUI 侧 format.ts 的词汇表保持同一套词（D30 只把 WebUI 中文化了，
TUI 的界面文案在 D36 这次补齐）。内部状态仍用英文枚举当标识，
只在渲染前过一层映射——新枚举值没配中文名时原样大写兜底。
"""

# 运行状态：顶栏圆点 + 台账 STATE 行共用。
RUN_STATE_LABELS = {
    "READY": "就绪",
    "STREAMING": "回复中",
    "APPROVAL": "待审批",
    "COMPLETE": "完成",
    "ERROR": "出错",
    "STOPPING": "停止中",
    "STOPPED": "已停止",
    "PREEMPTING": "打断中",
}

# 执行审批档位（approve/auto/yolo）。
APPROVAL_LABELS = {
    "approve": "需审批",
    "auto": "自动",
    "yolo": "免审批",
}

# 流式插话档位（queue/cooperative/preemptive）。
STEERING_LABELS = {
    "queue": "队列",
    "cooperative": "协作",
    "preemptive": "抢占",
}

# 命令收条的标题（_show_command_result 的第一参数）。
RECEIPT_LABELS = {
    "APPROVAL": "审批",
    "ATTACH": "图片",
    "COMPACT": "压缩",
    "CONTEXT": "上下文",
    "COOPERATIVE": "插话 · 协作",
    "EXPORT": "导出",
    "MEMORY": "记忆",
    "MODEL": "模型",
    "PLAN": "计划",
    "PREEMPTIVE": "插话 · 抢占",
    "QUEUE": "插话 · 排队",
    "RESUME": "会话",
    "RETRY": "重试",
    "ROUNDS": "轮次",
    "STATUS": "状态",
    "STEERING": "插话",
    "STOPPED": "已停止",
    "THINKING": "思考",
    "TRACE": "轨迹",
    "TRANSCRIPT": "正文",
    "UNKNOWN": "未知",
    "WORKDIR": "目录",
}

# stop_reason 的两类来源（模型侧 end_turn/max_tokens…、本地侧
# cancelled/steering/error）统一给中文名；与 WebUI format.ts 同词。
STOP_REASON_LABELS = {
    "end_turn": "自然结束",
    "max_tokens": "达到输出上限",
    "tool_use": "调用工具",
    "refusal": "模型拒绝",
    "pause_turn": "暂停",
    "cancelled": "已中断",
    "steering": "已并入插话",
    "injected": "已并入插话",
    "error": "出错",
}


def run_state_label(state: str) -> str:
    return RUN_STATE_LABELS.get(state, state)


def approval_label(mode: str) -> str:
    return APPROVAL_LABELS.get(mode, mode.upper())


def steering_label(mode: str) -> str:
    return STEERING_LABELS.get(mode, mode.upper())


def receipt_label(title: str) -> str:
    return RECEIPT_LABELS.get(title, title)


def stop_reason_label(reason: str) -> str:
    return STOP_REASON_LABELS.get(reason, reason)

