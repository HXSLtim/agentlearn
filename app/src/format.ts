// 纯格式化助手：转义、缩写、时间与标签。无状态，随便引用。
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { SettingsSnapshot } from "./types";

marked.setOptions({ gfm: true, breaks: true });

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function shortSession(sessionId: string): string {
  return sessionId ? sessionId.slice(0, 8) : "待定";
}

// 枚举值落屏前的中文名；没命中的原样大写兜底（新枚举值不致渲染成小写英文）。
const APPROVAL_LABELS: Record<string, string> = {
  approve: "需审批",
  auto: "自动",
  yolo: "免审批",
};
const STEERING_LABELS: Record<string, string> = {
  queue: "队列",
  cooperative: "协作",
  preemptive: "抢占",
};
const STEERING_STATE_LABELS: Record<string, string> = {
  queued: "已排队",
  acknowledged: "已确认",
  delivered: "已送达",
  failed: "未送达",
};
// stop_reason 有模型侧（end_turn / max_tokens …）和本地侧（cancelled /
// steering / error）两种来源，都转中文；没命中的原样兜底。
const STOP_REASON_LABELS: Record<string, string> = {
  end_turn: "自然结束",
  max_tokens: "达到输出上限",
  tool_use: "调用工具",
  refusal: "模型拒绝",
  pause_turn: "暂停",
  cancelled: "已中断",
  steering: "已并入插话",
  injected: "已并入插话",
  error: "出错",
};

export function approvalLabel(mode: string): string {
  return APPROVAL_LABELS[mode] ?? mode.toUpperCase();
}

export function steeringLabel(mode: string): string {
  return STEERING_LABELS[mode] ?? mode.toUpperCase();
}

export function steeringStateLabel(state: string): string {
  return STEERING_STATE_LABELS[state] ?? state.toUpperCase();
}

export function stopReasonLabel(reason: string): string {
  return STOP_REASON_LABELS[reason] ?? reason;
}

export function effortLabel(effort: SettingsSnapshot["effort"]): string {
  return effort || "default (omit)";
}

// 毫秒是内部单位，落屏要人话：<1 秒按毫秒，<1 分钟按秒，再往上给分/时。
// 0ms 显示「<1ms」——被打断的轮至少能看出发生过，而不是像没跑。
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return "—";
  }
  if (ms < 1) {
    return "<1ms";
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)} 秒`;
  }
  if (ms < 3_600_000) {
    const minutes = Math.floor(ms / 60_000);
    const seconds = Math.round((ms % 60_000) / 1000);
    return `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒`;
  }
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.round((ms % 3_600_000) / 60_000);
  return `${hours} 时 ${String(minutes).padStart(2, "0")} 分`;
}

// 服务端 cache_hit_ratio 是千分比（457 = 45.7%）。落屏统一百分数，
// 千分号在中文界面里没人读得懂。value 直接收 unknown——trace 的
// data 是外部 JSON，类型不可信，先窄化再算。
export function cacheHitPercent(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${(value / 10).toFixed(1)}%`;
}

export function threadTitleFor(prompt: string): string {
  const singleLine = prompt.replace(/\s+/g, " ").trim();
  // 按码点截断而不是 UTF-16 码元：直接 slice 会把 emoji 的代理对切成
  // 半个字符（渲染成 �），Array.from 至少保证每个字符完整。
  const chars = Array.from(singleLine);
  return chars.length > 42 ? `${chars.slice(0, 42).join("")}…` : singleLine;
}

export function isAutoThreadTitle(title: string): boolean {
  return !title || title === "未命名运行" || title === "新会话";
}

// 服务端还没给出标题、本地也没起过名时的列表兜底：有内容的说「会话」，
// 真空会话说「空会话」，都带日期——比一排「未命名运行」可辨认。
export function fallbackSessionTitle(createdAt: number, turns: number): string {
  const date = new Date(createdAt);
  const stamp = `${
    date.getMonth() + 1
  }-${String(date.getDate()).padStart(2, "0")} ${String(
    date.getHours(),
  ).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `${turns > 0 ? "会话" : "空会话"} · ${stamp}`;
}

// 相对时间的统一口径：1 分钟内给秒，1 小时内给分钟，同一天给小时；
// 更早的不再说「N 天前」——5 天前是哪天没人算得出来，直接给绝对时间。
// 昨天 →「昨天 HH:mm」，今年 →「MM-DD HH:mm」，跨年 →「YYYY-MM-DD HH:mm」。
export function relativeTime(timestamp: number): string {
  const now = new Date();
  const then = new Date(timestamp);
  const diffMs = now.getTime() - then.getTime();
  const clock = (date: Date): string =>
    `${String(date.getHours()).padStart(2, "0")}:${String(
      date.getMinutes(),
    ).padStart(2, "0")}`;
  if (diffMs < 60_000) {
    return diffMs < 1000 ? "刚刚" : `${Math.floor(diffMs / 1000)} 秒前`;
  }
  if (diffMs < 3_600_000) {
    return `${Math.floor(diffMs / 60_000)} 分钟前`;
  }
  const sameDay =
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate();
  if (sameDay) {
    return `${Math.floor(diffMs / 3_600_000)} 小时前`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (
    then.getFullYear() === yesterday.getFullYear() &&
    then.getMonth() === yesterday.getMonth() &&
    then.getDate() === yesterday.getDate()
  ) {
    return `昨天 ${clock(then)}`;
  }
  const monthDay = `${then.getMonth() + 1}-${String(then.getDate()).padStart(
    2,
    "0",
  )}`;
  if (then.getFullYear() === now.getFullYear()) {
    return `${monthDay} ${clock(then)}`;
  }
  return `${then.getFullYear()}-${monthDay} ${clock(then)}`;
}




export function renderMarkdown(value: string): string {
  const parsed = marked.parse(value) as string;
  return DOMPurify.sanitize(parsed, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target"],
  });
}
