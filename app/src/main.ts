// Tauri 的 WebKit 里 -apple-system 就是 SF；浏览器里落到系统 sans。
// 标题栏 overlay 需要给头部留出红绿灯位置。
if ("__TAURI_INTERNALS__" in window) {
  document.documentElement.classList.add("is-tauri");
}

import type {
  ApprovalMode,
  SteeringMode,
  TranscriptMode,
  PlanEvent,
  ImageAttachment,
  ToolEvent,
  ToolPart,
  UiMessage,
  TurnUsage,
  Thread,
  SettingsSnapshot,
  CapabilitiesResponse,
  ChatEvent,
  PlanConfirmResponse,
  ServerSession,
  ServerMessage,
  StopResponse,
  ActivityItem,
  StreamContext,
  TraceResponse,
} from "./types";
import {
  SLASH_COMMANDS_BY_NAME,
  type SlashCommand,
} from "./slashCommands";
import {
  appShell,
  threadList,
  threadCount,
  newThreadButton,
  railCloseButton,
  railOpenButton,
  traceToggle,
  runRailToggle,
  runRail,
  scrim,
  threadKicker,
  threadTitle,
  connectionPill,
  runtimeApiBase,
  conversation,
  conversationScroll,
  composer,
  promptInput,
  composerStatus,
  sendButton,
  sendLabel,
  stopButton,
  runState,
  runStateCopy,
  runSession,
  runTurns,
  runTools,
  runElapsed,
  runInputTokens,
  runOutputTokens,
  runCacheRead,
  runCacheWrite,
  runCacheHit,
  runCost,
  runEffort,
  runApproval,
  runSteering,
  runStop,
  activityList,
  activityCount,
  runtimeLight,
  ledgerLight,
  settingsModal,
  traceModal,
  workspaceDiffToggle,
  workspaceDiffModal,
  threadContextMenu,
  copySessionIdButton,
} from "./dom";

import {
  API_BASE,
  consumeSse,
  ensureApiServer,
  readError,
} from "./api";
import {
  escapeHtml,
  shortSession,
  relativeTime,
  effortLabel,
  threadTitleFor,
  renderMarkdown,
  approvalLabel,
  steeringLabel,
  steeringStateLabel,
  stopReasonLabel,
  formatDuration,
  cacheHitPercent,
  isAutoThreadTitle,
  fallbackSessionTitle,
} from "./format";
import {
  openTrace,
  closeTrace,
  loadTrace,
} from "./trace";
import {
  openWorkspaceDiff,
  closeWorkspaceDiff,
  loadWorkspaceDiff,
} from "./workspaceDiff";
import { showToast } from "./toast";
import {
  hasPendingImages,
  parseImage,
  peekPendingImages,
  takePendingImages,
} from "./composerImages";
import {
  activeThread,
  createId,
  createThread,
  isThreadRunning,
  persistState,
  runningAssistant,
  state,
} from "./state";
import { setShellBridge } from "./shellBridge";
import {
  closeSettings,
  currentEffort,
  currentPricing,
  cycleEffort,
  openSettings,
  quickSetModel,
  refreshEffort,
  selectedModelId,
  setCurrentEffort,
  setEffort,
} from "./settings";
import {
  autoApprovedApprovalIds,
  cycleApprovalMode,
  setApprovalMode,
} from "./approval";
import { renderWorkdir } from "./workdir";
import {
  applyConversationFindHighlight,
  closeConversationFind,
  findConversationMatch,
  isConversationFindOpen,
  openConversationFind,
} from "./conversationFind";
import {
  closeResume,
  findResumeThread,
  handleResumeKeydown,
  isResumeOpen,
  openResume,
} from "./resume";
import {
  closeCommandPalette,
  completeSelectedCommand,
  completeSelectedFile,
  isCommandPaletteOpen,
  isFilePalette,
  moveCommandSelection,
  resetCommandSelection,
  updateCommandPalette,
} from "./commandPalette";
let renderFrame: number | undefined;

let renderTarget: { threadId: string; messageId: string } | null = null;

let transcriptMode: TranscriptMode = "normal";

const expandedReasoning = new Set<string>();

let commandReceipt: {
  threadId: string;
  title: string;
  body: string;
  error: boolean;
} | null = null;

function parseServerTime(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Date.now();
}

function parseServerMessages(
  messages: ServerMessage[],
  sessionStopReason = "",
): UiMessage[] {
  const result: UiMessage[] = [];
  const tools = new Map<string, ToolEvent>();
  const baseTime = Date.now() - messages.length;

  for (const [index, message] of messages.entries()) {
    const createdAt =
      typeof message.ts === "number" && Number.isFinite(message.ts)
        ? message.ts * 1000
        : baseTime + index;
    if (message.role === "assistant") {
      const assistant: UiMessage = {
        ...createAssistantMessage(),
        createdAt,
        streaming: false,
        queued: false,
      };
      for (const block of message.content) {
        if (block.type === "thinking" && block.thinking) {
          appendAssistantThinking(assistant, block.thinking);
        } else if (block.type === "text" && block.text) {
          appendAssistantText(assistant, block.text);
        } else if (block.type === "tool_use" && block.id) {
          const tool: ToolEvent = {
            id: block.id,
            name: block.name ?? "tool",
            input: block.input ?? {},
            state: "running",
          };
          assistant.parts.push({ type: "tool", tool });
          tools.set(block.id, tool);
        }
      }
      if (assistant.parts.length) {
        result.push(assistant);
      }
      continue;
    }

    let userMessage: UiMessage | undefined;
    for (const block of message.content) {
      const image = parseImage(block.image ?? block);
      if (block.type === "text" && block.text) {
        userMessage ??= {
          ...createUserMessage(""),
          createdAt,
        };
        userMessage.text += block.text;
      } else if (block.type === "image" && image) {
        userMessage ??= {
          ...createUserMessage(""),
          createdAt,
        };
        userMessage.images = [...(userMessage.images ?? []), image];
      } else if (block.type === "tool_result" && block.tool_use_id) {
        const tool = tools.get(block.tool_use_id);
        if (tool) {
          tool.state = "done";
          tool.output = block.content ?? "";
          tool.image = image;
          tool.hasImage = Boolean(image);
          tool.isError = Boolean(block.is_error);
          tool.elapsedMs = undefined;
        }
      }
    }
    if (userMessage) {
      result.push(userMessage);
    }
  }

  const lastAssistant = [...result]
    .reverse()
    .find((message) => message.role === "assistant");
  if (lastAssistant && sessionStopReason) {
    lastAssistant.stopReason = sessionStopReason;
  }
  return result;
}

function mergeServerSessions(sessions: ServerSession[]): void {
  for (const session of sessions) {
    let thread = state.threads.find(
      (candidate) => candidate.sessionId === session.id,
    );
    if (!thread) {
      thread = createThread();
      thread.sessionId = session.id;
      thread.messages = [];
      thread.messagesLoaded = false;
      state.threads.push(thread);
    }
    // 标题优先级：服务端正式名 > 本地已起过的名 > 兜底（会话/空会话 + 时间）。
    // 本地还是自动名（未命名运行/新会话）时用兜底，别把一排同名占位顶在列表里。
    const createdAt = parseServerTime(session.created_at);
    thread.title =
      session.title ||
      (isAutoThreadTitle(thread.title)
        ? fallbackSessionTitle(createdAt, session.turns)
        : thread.title) ||
      fallbackSessionTitle(createdAt, session.turns);
    thread.model = session.model || thread.model;
    thread.pinned = session.pinned === true;
    thread.archived = session.archived === true;
    // 服务端是事实来源；只防一种情况：本地刚改过、但会话行还没建
    // （首条消息没发），那个值要随下一次 /chat 落上去，别被冲掉。
    if (typeof session.workdir === "string" && session.workdir !== thread.workdir) {
      if (!thread.workdir || !thread.sessionId || thread.sessionId === session.id) {
        thread.workdir = session.workdir;
      }
    }
    thread.serverTurns = session.turns;
    thread.stopReason = session.stop_reason || thread.stopReason;
    thread.createdAt = parseServerTime(session.created_at);
    thread.updatedAt = parseServerTime(session.updated_at);
  }
}

// span-tick 的数据源：恢复会话时从 trace 拉一次 turn span 时长，
// 配到每轮收尾的助手消息（后面跟着用户消息、或全会话最后一条）上。
// span 按 id 排（store.spans_for），与轮次一一对应；拉不到就静默——
// 账本不造假，tick 只在真有数据时出现。
async function hydrateTurnDurations(thread: Thread): Promise<void> {
  if (!thread.sessionId || durationHydrationTried.has(thread.id)) {
    return;
  }
  durationHydrationTried.add(thread.id);
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}/trace`,
    );
    if (!response.ok) {
      return;
    }
    const { spans } = (await response.json()) as TraceResponse;
    // 后端 JSON 不做类型保证：先 Number() 收敛再过滤非有限值，
    // durationMs 一律是有限数字，后面模板插值（title/width）才可信。
    const turnDurations = spans
      .filter((span) => span.name === "turn" && span.duration_ms !== null)
      .map((span) => Number(span.duration_ms))
      .filter((ms) => Number.isFinite(ms) && ms >= 0);
    if (!turnDurations.length) {
      return;
    }
    // 按用户消息切轮：turns[k] ↔ 第 k 个 turn span，顺序一一对应。
    const turns: { user: UiMessage; assistants: UiMessage[] }[] = [];
    for (const message of thread.messages) {
      if (message.role === "user") {
        turns.push({ user: message, assistants: [] });
      } else {
        turns[turns.length - 1]?.assistants.push(message);
      }
    }
    const turnSpans = spans.filter((span) => span.name === "turn");
    turns.forEach((turn, index) => {
      const span = turnSpans[index];
      if (!span) {
        return;
      }
      // 老 JSONL 没有 ts，恢复后时间是合成序时——用 span 的真实起止
      // 回填：用户消息取 turn 开始，助手消息取 turn 结束。
      const startedAt = Date.parse(span.start_time);
      const endedAt = span.end_time ? Date.parse(span.end_time) : NaN;
      if (Number.isFinite(startedAt)) {
        turn.user.createdAt = startedAt;
      }
      if (Number.isFinite(endedAt)) {
        for (const assistant of turn.assistants) {
          assistant.createdAt = endedAt;
        }
      }
      const duration = Number(span.duration_ms);
      const closer = turn.assistants[turn.assistants.length - 1];
      if (
        closer &&
        closer.durationMs === undefined &&
        Number.isFinite(duration) &&
        duration >= 0 &&
        // 历史 D34 清扫曾把孤儿 span 的 end_time 写成重启时刻，换来几天
        // 的假时长；超过 24h 的轮次时长按脏数据丢弃，宁可不显示。
        duration < 24 * 3_600_000
      ) {
        closer.durationMs = duration;
      }
    });
    if (thread.id === state.activeThreadId) {
      renderConversation(false);
    }
  } catch (error) {
    // 拉不到不造数据；warn 一声方便发现接口断了
    console.warn("span-tick 数据水合失败", error);
  }
}

export async function fetchServerSessions(
  reportError = false,
  archived = false,
): Promise<void> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(
      `${API_BASE}/sessions?archived=${archived ? "true" : "false"}`,
      { signal: controller.signal },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const sessions = (await response.json()) as ServerSession[];
    mergeServerSessions(sessions);
    if (!archived) {
      const activeIds = new Set(sessions.map((session) => session.id));
      for (const thread of state.threads) {
        if (thread.sessionId && !activeIds.has(thread.sessionId)) {
          thread.archived = true;
        }
      }
    }
    persistState();
    renderEverything();
  } catch (error) {
    if (reportError) {
      showToast(
        `读取服务端会话失败：${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  } finally {
    window.clearTimeout(timeout);
  }
}

const hydrationJobs = new Map<string, Promise<void>>();
// span-tick 数据每个会话拉一次就够（页面生命周期内）
const durationHydrationTried = new Set<string>();

export async function hydrateThread(thread: Thread, force = false): Promise<void> {
  if (!thread.sessionId || (thread.messagesLoaded && !force) || isThreadRunning(thread)) {
    return;
  }
  const existing = hydrationJobs.get(thread.id);
  if (existing) {
    return existing;
  }
  const job = (async () => {
    try {
      const response = await fetch(
        `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}/messages`,
      );
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      const messages = (await response.json()) as ServerMessage[];
      thread.messages = parseServerMessages(messages, thread.stopReason);
      thread.serverTurns = messages.filter(
        (message) => message.role === "user",
      ).length;
      thread.messagesLoaded = true;
      thread.updatedAt = Date.now();
      persistState();
      void hydrateTurnDurations(thread);
      if (thread.id === state.activeThreadId) {
        renderEverything(true);
      } else {
        renderThreadList();
      }
    } catch (error) {
      showToast(
        `读取会话正文失败：${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    } finally {
      hydrationJobs.delete(thread.id);
    }
  })();
  hydrationJobs.set(thread.id, job);
  return job;
}


function lastAssistant(thread: Thread): UiMessage | undefined {
  return [...thread.messages].reverse().find((message) => message.role === "assistant");
}

function threadStatus(thread: Thread): string {
  const active = runningAssistant(thread);
  if (active) {
    if (thread.stopping) {
      return "停止中";
    }
    return assistantTools(active).some((tool) => tool.state === "waiting")
      ? "待审批"
      : "运行中";
  }
  const assistant = lastAssistant(thread);
  if (assistant?.stopReason === "cancelled" || thread.stopReason === "cancelled") {
    return "已取消";
  }
  if (assistant?.error || thread.stopReason === "error") {
    return "错误";
  }
  return thread.messages.length ? "完成" : "就绪";
}

function threadStatusClass(status: string): "is-ready" | "is-running" | "is-error" {
  if (status === "运行中" || status === "待审批" || status === "停止中") {
    return "is-running";
  }
  if (status === "错误") {
    return "is-error";
  }
  return "is-ready";
}

function createUserMessage(text: string): UiMessage {
  return {
    id: createId(),
    role: "user",
    text,
    images: [],
    thinking: "",
    parts: [],
    error: "",
    stopReason: "",
    createdAt: Date.now(),
  };
}

function createAssistantMessage(queued = false): UiMessage {
  return {
    id: createId(),
    role: "assistant",
    text: "",
    thinking: "",
    parts: [],
    error: "",
    stopReason: "",
    createdAt: Date.now(),
    streaming: true,
    queued,
  };
}

function assistantTools(message: UiMessage | undefined): ToolEvent[] {
  if (!message) {
    return [];
  }
  return message.parts
    .filter((part): part is ToolPart => part.type === "tool")
    .map((part) => part.tool);
}

function setStatusLight(element: HTMLElement, status: string): void {
  element.classList.remove("is-ready", "is-running", "is-error");
  element.classList.add(status);
}

export function renderThreadList(): void {
  const sorted = state.threads
    .filter((thread) => !thread.archived)
    .sort(
      (a, b) =>
        Number(b.pinned) - Number(a.pinned) || b.updatedAt - a.updatedAt,
    );
  threadCount.textContent = String(sorted.length);
  threadList.innerHTML = sorted
    .map((thread) => {
      const selected = thread.id === state.activeThreadId;
      const messageCount =
        thread.serverTurns ??
        thread.messages.filter((message) => message.role === "user").length;
      const status = threadStatus(thread);
      const statusClass = threadStatusClass(status);
      return `
        <button
          class="thread-item${selected ? " is-active" : ""}"
          type="button"
          data-thread-id="${escapeHtml(thread.id)}"
          aria-current="${selected ? "true" : "false"}"
        >
          <span class="thread-marker ${statusClass}" aria-hidden="true"></span>
          <span class="thread-copy">
            <strong>${escapeHtml(thread.title)}</strong>
            <small><b class="thread-state ${statusClass}">${status}</b> · ${thread.pinned ? "置顶 · " : ""}${relativeTime(thread.updatedAt)} · ${messageCount} 轮</small>
          </span>
        </button>
      `;
    })
    .join("");
}

function emptyStateMarkup(): string {
  return `
    <section class="empty-state">
      <header class="empty-boot">
        <span class="eyebrow">新会话 · 本地运行时</span>
        <span class="empty-signal"><i></i> 就绪</span>
      </header>
      <div class="empty-console" aria-label="Agent 已就绪">
        <span class="empty-prefix">agentlearn@local</span>
        <span class="empty-path">~/sandbox</span>
        <span class="empty-caret">$</span>
        <span class="empty-command">awaiting input</span>
        <span class="empty-cursor" aria-hidden="true">_</span>
      </div>
      <p class="empty-copy">
        输入任务，模型请求、工具执行和轨迹会沿同一条执行线展开。
        所有记录都留在这台机器。
      </p>
      <div class="starter-list" aria-label="示例任务">
        <button type="button" class="starter-card" data-prompt="现在几点了？">
          <span class="starter-index">[01]</span>
          <span class="starter-label">现在几点了？</span>
          <small>now_time</small>
        </button>
        <button type="button" class="starter-card" data-prompt="看一下 sandbox 目录里有哪些文件。">
          <span class="starter-index">[02]</span>
          <span class="starter-label">查看 sandbox</span>
          <small>bash / ls</small>
        </button>
        <button type="button" class="starter-card" data-prompt="先说明你准备怎么做，再执行，最后把结论和依据分开列出来。">
          <span class="starter-index">[03]</span>
          <span class="starter-label">测试执行边界</span>
          <small>reason → act → report</small>
        </button>
      </div>
      <div class="empty-footnote">
        <span>本地 JSONL + SQLite</span>
      </div>
    </section>
  `;
}

function renderTool(tool: ToolEvent, detailed: boolean): string {
  if (!detailed) {
    const preview = compactToolInput(tool);
    const state = compactToolState(tool);
    return `
      <section class="tool-event is-compact ${tool.isError ? "is-error" : ""} ${tool.state === "running" ? "is-running" : ""}" title="${escapeHtml(preview)}">
        <header>
          <span class="tool-label">${tool.isError ? "工具错误" : "工具"}</span>
          <code>${escapeHtml(tool.name)}</code>
          <span>${escapeHtml(state)}</span>
        </header>
        <p class="tool-summary">${escapeHtml(preview)}</p>
      </section>
    `;
  }
  const args = JSON.stringify(tool.input, null, 2);
  const showInput = detailed || tool.state === "waiting";
  const toolImage = tool.image
    ? renderImages([tool.image], "工具结果图片")
    : tool.hasImage
      ? `<div class="tool-image-pending">图片已附 · 从 transcript 重载中</div>`
      : "";
  const output =
    tool.state === "waiting"
      ? `
        <div class="tool-approval-gate">
          <span>宿主机执行需要审批</span>
          <input
            type="text"
            class="approval-reason"
            data-approval-id="${escapeHtml(tool.approvalId ?? "")}"
            placeholder="拒绝理由（可选）"
            aria-label="拒绝理由（可选）"
          />
          <div>
            <button type="button" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="false" data-approval-scope="once">拒绝</button>
            <button type="button" class="approve" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="true" data-approval-scope="once">本次允许</button>
            <button type="button" class="approve" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="true" data-approval-scope="session">本会话允许</button>
          </div>
        </div>
      `
      : tool.state === "running"
        ? `<span class="tool-pending">等待结果…</span>`
        : `<pre>${escapeHtml(tool.output || "（无输出）")}</pre>${toolImage}`;
  const duration =
    tool.elapsedMs === undefined
      ? ""
      : `<span>${formatDuration(tool.elapsedMs)}</span>`;
  const label =
    tool.state === "waiting"
      ? "工具审批"
      : tool.isError
        ? "工具错误"
        : "工具调用";
  const stateClass =
    tool.state === "waiting"
      ? "is-waiting"
      : tool.state === "running"
        ? "is-running"
        : "";
  return `
    <section class="tool-event ${tool.isError ? "is-error" : ""} ${stateClass}">
      <header>
        <span class="tool-label">${label}</span>
        <code>${escapeHtml(tool.name)}</code>
        ${duration}
      </header>
      ${showInput ? `<pre class="tool-input">${escapeHtml(args === "{}" ? "（无参数）" : args)}</pre>` : ""}
      ${output}
    </section>
  `;
}

function compactToolInput(tool: ToolEvent): string {
  const preferred = ["command", "path", "name", "text"].find((key) => {
    const value = tool.input[key];
    return typeof value === "string" && value.trim();
  });
  const raw = preferred
    ? String(tool.input[preferred])
    : Object.keys(tool.input).length
      ? JSON.stringify(tool.input)
      : "无参数";
  return raw.replace(/\s+/g, " ").trim().slice(0, 120) || "无参数";
}

function compactToolState(tool: ToolEvent): string {
  if (tool.state === "waiting") {
    return "等待审批";
  }
  if (tool.state === "running") {
    return "执行中";
  }
  const duration =
    tool.elapsedMs === undefined ? "" : ` · ${formatDuration(tool.elapsedMs)}`;
  return `${tool.isError ? "出错" : "完成"}${duration}`;
}

function renderPlan(plan: PlanEvent): string {
  const actions = plan.discarded
    ? `<span class="plan-state">计划已放弃</span>`
    : plan.executed
      ? `<span class="plan-state">计划已开始执行</span>`
      : plan.confirmed
        ? `<span class="plan-state">计划已确认</span>`
      : `
        <div class="plan-actions">
          <button type="button" data-plan-id="${escapeHtml(plan.id)}" data-plan-action="discard">放弃计划</button>
          <button type="button" class="confirm" data-plan-id="${escapeHtml(plan.id)}" data-plan-action="confirm">确认并执行</button>
        </div>
      `;
  return `
    <section class="plan-card" aria-label="待确认计划">
      <header>
        <span>计划模式 · 人工确认</span>
        <code>${plan.id.slice(0, 8)}</code>
      </header>
      <pre>${escapeHtml(plan.text)}</pre>
      ${actions}
    </section>
  `;
}

function renderImages(images: ImageAttachment[], label: string): string {
  const safeImages = images
    .map(parseImage)
    .filter((image): image is ImageAttachment => Boolean(image));
  if (!safeImages.length) {
    return "";
  }
  return `
    <figure class="message-image-group" aria-label="${escapeHtml(label)}">
      ${safeImages
        .map(
          (image, index) =>
            `<img src="data:${image.media_type};base64,${image.data}" alt="${escapeHtml(label)} ${index + 1}" loading="lazy" />`,
        )
        .join("")}
    </figure>
  `;
}


function assistantMaxDurationMs(): number {
  let max = 0;
  for (const message of activeThread().messages) {
    if (
      message.role === "assistant" &&
      typeof message.durationMs === "number" &&
      Number.isFinite(message.durationMs)
    ) {
      max = Math.max(max, message.durationMs);
    }
  }
  return max || 1;
}

/**
 * 当前转录档位下，这条助手消息是否还有可见内容。
 * 若这轮只有不可渲染的空片段，卡片会变成空白框——与其留白框，
 * 不如让整张卡片不出现（工具活动在右侧账本里仍有记录）。
 * 真正一条内容都没有的回答仍算可见，由 renderAssistant 写「没有返回内容」。
 */
function assistantHasVisibleContent(message: UiMessage): boolean {
  if (message.streaming || message.error) {
    return true;
  }
  if (!message.parts.length) {
    return true;
  }
  return message.parts.some((part) => {
    if (part.type === "tool") {
      return true;
    }
    if (part.type === "plan") {
      return true;
    }
    if (part.type === "thinking") {
      return Boolean(part.text);
    }
    return Boolean(part.text);
  });
}

/** 上一条真正会渲染的消息是不是助手——决定要不要重复「A 智能体」抬头。 */
function showAuthorFor(messages: UiMessage[], index: number): boolean {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const previous = messages[cursor];
    if (previous.role === "assistant" && !assistantHasVisibleContent(previous)) {
      continue;
    }
    return previous.role !== "assistant";
  }
  return true;
}

function renderAssistant(message: UiMessage, showAuthor = true): string {
  const parts = message.parts
    .map((part, index) => {
      if (part.type === "tool") {
        const needsApproval = part.tool.state === "waiting";
        return renderTool(
          part.tool,
          needsApproval || transcriptMode === "verbose",
        );
      }
      if (part.type === "plan") {
        return renderPlan(part.plan);
      }
      if (part.type === "thinking") {
        if (!part.text) {
          return "";
        }
        const live = Boolean(
          message.streaming && index === message.parts.length - 1,
        );
        if (live) {
          const tail = part.text.slice(-280);
          const reasoningKey = `${message.id}:${index}`;
          return `
            <details
              class="reasoning-live reasoning-toggle"
              data-reasoning-key="${escapeHtml(reasoningKey)}"
              ${expandedReasoning.has(reasoningKey) ? "open" : ""}
              aria-label="模型正在思考"
            >
              <summary>
                <span><i aria-hidden="true"></i> 思考 · 进行中</span>
                <span>${part.text.length.toLocaleString()} 字</span>
              </summary>
              <pre class="reasoning-stream">${tail.length < part.text.length ? "…" : ""}${escapeHtml(tail)}<i aria-hidden="true"></i></pre>
            </details>
          `;
        }
        const reasoningKey = `${message.id}:${index}`;
        return `
          <details
            class="reasoning-block reasoning-toggle"
            data-reasoning-key="${escapeHtml(reasoningKey)}"
            ${expandedReasoning.has(reasoningKey) || transcriptMode !== "normal" ? "open" : ""}
          >
            <summary>
              <span>思考</span>
              <span>${part.text.length.toLocaleString()} 字</span>
            </summary>
            <pre>${escapeHtml(part.text)}</pre>
          </details>
        `;
      }
      if (!part.text) {
        return "";
      }
      return `<div class="markdown">${renderMarkdown(part.text)}</div>`;
    })
    .join("");
  const waiting =
    message.streaming &&
    !message.text &&
    !message.parts.some(
      (part) =>
        (part.type === "thinking" && part.text) ||
        (part.type === "tool" && part.tool.state !== "done"),
    )
      ? `
        <div class="thinking-line" aria-label="模型正在生成">
          <span></span><span></span><span></span>
          <em>${
            message.queued
              ? "排队 · 等待轮次槽位"
              : message.thinking
                ? "正在组织回复"
                : "等待首个 token"
          }</em>
        </div>
      `
      : "";
  const error = message.error
    ? `
      <div class="message-error">
        <div>
          <strong>运行错误</strong>
          <p>${escapeHtml(message.error)}</p>
        </div>
        <button class="retry-run" type="button" data-retry-message-id="${escapeHtml(message.id)}">重试</button>
      </div>
    `
    : "";
  const stop =
    message.stopReason && !message.streaming
      ? `<span>结束 · ${escapeHtml(stopReasonLabel(message.stopReason))}</span>`
      : "";
  // 真空回答（不是被隐藏，而是本来就没有内容）：留一句说明，别留空白卡片。
  // 但被中断 / 并入插话的轮不补这句——footer 已经写明结局，再补一句只是噪音。
  const quietStop =
    message.stopReason === "cancelled" ||
    message.stopReason === "steering" ||
    message.stopReason === "injected";
  const emptyNote =
    !parts &&
    !error &&
    !quietStop &&
    !message.streaming &&
    !message.parts.length
      ? `<p class="assistant-empty">本轮没有返回内容。</p>`
      : "";
  return `
    <article class="message assistant-message${message.streaming ? " is-streaming" : ""}" data-message-id="${escapeHtml(message.id)}">
      <header class="message-heading${showAuthor ? "" : " is-continuation"}">
        ${showAuthor ? '<span class="message-avatar">A</span><span>智能体</span>' : ""}
        <span class="message-rule"></span>
        <time>${new Date(message.createdAt).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        })}</time>
      </header>
      <div class="assistant-flow">${waiting}${emptyNote}${parts}</div>
      ${error}
      <footer class="message-footer">
        ${stop}
        ${message.durationMs !== undefined ? `<span>${formatDuration(message.durationMs)}</span>` : ""}
      </footer>
      ${
        message.durationMs !== undefined && !message.streaming
          ? `<span class="span-tick" title="本轮耗时 ${formatDuration(message.durationMs)} · 全会话最长轮为基准"><i style="width: ${Math.max(3, Math.round((message.durationMs / assistantMaxDurationMs()) * 100))}%"></i></span>`
          : ""
      }
    </article>
  `;
}

function renderUser(message: UiMessage): string {
  const steering =
    message.steeringState === "failed"
      ? // 未送达的消息没进模型也没进 JSONL，给一条自愈路径：点一下重发。
        `<button type="button" class="steering-state is-failed" data-resend-message-id="${escapeHtml(message.id)}" title="重新发送这条消息">未送达 · 点此重发</button>`
      : message.steeringState
        ? `<span class="steering-state is-${message.steeringState}">${steeringStateLabel(message.steeringState)}</span>`
        : "";
  return `
    <article class="message user-message message-appear" data-message-id="${escapeHtml(message.id)}">
      <header class="user-heading">
        <span>你${steering ? ` · ${steering}` : ""}</span>
        <time>${new Date(message.createdAt).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        })}</time>
      </header>
      <p>${escapeHtml(message.text)}</p>
      ${renderImages(message.images ?? [], "用户附件")}
    </article>
  `;
}

function renderCommandReceipt(): string {
  const thread = activeThread();
  if (!commandReceipt || commandReceipt.threadId !== thread.id) {
    return "";
  }
  return `
    <section class="command-receipt ${commandReceipt.error ? "is-error" : ""}">
      <header>
        <span>命令 / ${escapeHtml(commandReceipt.title)}</span>
        <span>仅本地</span>
      </header>
      <pre>${escapeHtml(commandReceipt.body)}</pre>
    </section>
  `;
}


export function renderConversation(forceBottom = false): void {
  const thread = activeThread();
  if (thread.sessionId) {
    void hydrateTurnDurations(thread);
  }
  const nearBottom =
    conversationScroll.scrollHeight -
      conversationScroll.scrollTop -
      conversationScroll.clientHeight <
    140;
  if (!thread.messages.length) {
    conversation.innerHTML = emptyStateMarkup() + renderCommandReceipt();
  } else {
    const rendered: string[] = [];
    thread.messages.forEach((message, index) => {
      if (message.role === "assistant" && !assistantHasVisibleContent(message)) {
        return;
      }
      rendered.push(
        message.role === "user"
          ? renderUser(message)
          : renderAssistant(message, showAuthorFor(thread.messages, index)),
      );
    });
    conversation.innerHTML = rendered.join("") + renderCommandReceipt();
  }
  if (forceBottom || nearBottom) {
    requestAnimationFrame(() => {
      conversationScroll.scrollTop = conversationScroll.scrollHeight;
    });
  }
  applyConversationFindHighlight();
}

function renderHeader(): void {
  const thread = activeThread();
  threadTitle.textContent = thread.title;
  threadKicker.textContent = thread.sessionId
    ? `会话 ${shortSession(thread.sessionId)}`
    : "新会话";
  traceToggle.disabled = !thread.sessionId;
  traceToggle.title = thread.sessionId
    ? "打开当前会话执行轨迹（/trace）"
    : "当前会话还没有 trace";
}

export function renderRunLedger(): void {
  const thread = activeThread();
  const active = runningAssistant(thread);
  const assistant = active ?? lastAssistant(thread);
  const tools = assistantTools(assistant);
  const waitingApproval = tools.some((tool) => tool.state === "waiting");
  const status = active
    ? thread.stopping
      ? "停止中"
      : waitingApproval
      ? "待审批"
      : "回复中"
    : threadStatus(thread) === "已取消"
      ? "已取消"
      : assistant?.error
        ? "错误"
        : thread.messages.length
          ? "完成"
          : "就绪";
  const statusClass =
    status === "回复中" || status === "待审批" || status === "停止中"
      ? "is-running"
      : status === "错误"
        ? "is-error"
        : "is-ready";
  const statusCopy =
    status === "停止中"
      ? "停止请求已发送，等待下一个检查点收线"
      : status === "待审批"
      ? "工具等待人工接受或拒绝"
      : status === "回复中"
        ? `${steeringLabel(thread.steeringMode)} steering · 模型请求或工具仍在执行`
      : status === "错误"
        ? "上一轮没有正常结束"
        : status === "已取消"
          ? "上一轮在检查点被取消"
        : status === "完成"
          ? "最近一轮已经结束"
          : "等待第一条消息";
  runState.textContent = status;
  runState.className = statusClass;
  runStateCopy.textContent = statusCopy;
  runSession.textContent = shortSession(thread.sessionId);
  runTurns.textContent = String(
    thread.messages.filter((message) => message.role === "user").length,
  );
  // 账本按会话统计（"工具调用"读起来是整场会话的账），
  // 实时活动那一栏才只看最后一轮。
  runTools.textContent = String(
    thread.messages.reduce(
      (total, message) => total + assistantTools(message).length,
      0,
    ),
  );
  const usage = thread.usage;
  runInputTokens.textContent = String(usage?.input_tokens ?? 0);
  runOutputTokens.textContent = String(usage?.output_tokens ?? 0);
  runCacheRead.textContent = String(usage?.cache_read_tokens ?? 0);
  runCacheWrite.textContent = String(usage?.cache_write_tokens ?? 0);
  runCacheHit.textContent = cacheHitPercent(usage?.cache_hit_ratio);
  runCost.textContent = formatCost(usage);
  runEffort.textContent = effortLabel(currentEffort);
  runApproval.textContent = approvalLabel(thread.approvalMode);
  runSteering.textContent = steeringLabel(thread.steeringMode);
  runStop.textContent = thread.stopReason
    ? stopReasonLabel(thread.stopReason)
    : "—";
  if (active && thread.runStartedAt) {
    runElapsed.textContent = formatDuration(Date.now() - thread.runStartedAt);
  } else {
    runElapsed.textContent =
      assistant?.durationMs !== undefined
        ? formatDuration(assistant.durationMs)
        : "—";
  }
  setStatusLight(runtimeLight, statusClass);
  setStatusLight(ledgerLight, statusClass);
  connectionPill.className = `connection-pill ${statusClass}`;
  connectionPill.innerHTML = `<span class="status-light ${statusClass}"></span>${status}`;

  const activity: ActivityItem[] = [];
  if (thread.contextWarning) {
    activity.push({
      key: "context-warning",
      tag: "警告",
      title: "上下文接近预算",
      detail: `${thread.contextWarning.estimate_tokens.toLocaleString()} / ${thread.contextWarning.budget_tokens.toLocaleString()} tokens`,
      status: "error",
    });
  }
  if (assistant?.thinking) {
    activity.push({
      key: "reasoning",
      tag: "思考",
      title: "已捕获思考",
      detail: `${assistant.thinking.length.toLocaleString()} 字`,
      status: "done",
    });
  }
  for (const tool of [...tools].reverse()) {
    activity.push({
      key: tool.id,
      tag: tool.isError
        ? "错误"
        : tool.state === "waiting"
          ? "等待"
          : tool.state === "running"
            ? "执行"
            : "工具",
      title: tool.name,
      detail:
        tool.state === "waiting"
          ? "等待审批"
          : tool.state === "running"
          ? "执行中"
          : `${tool.elapsedMs === undefined ? "" : `${formatDuration(tool.elapsedMs)} · `}${tool.output?.split("\n")[0]?.slice(0, 48) ?? "完成"}`,
      status: tool.isError ? "error" : tool.state,
    });
  }
  activityCount.textContent = String(activity.length);
  activityList.innerHTML = activity.length
    ? activity
        .slice(0, 6)
        .map(
          (item) => `
            <li class="activity-item ${item.status === "error" ? "is-error" : ""}">
              <span class="activity-tag">${item.tag}</span>
              <span>
                <strong>${escapeHtml(item.title)}</strong>
                <small>${escapeHtml(item.detail)}</small>
              </span>
            </li>
          `,
        )
        .join("")
    : `<li class="activity-empty">工具与思考事件会出现在这里。</li>`;
}

function formatCost(usage: TurnUsage | undefined): string {
  if (!currentPricing) {
    return "暂无价目";
  }
  const amount =
    ((usage?.input_tokens ?? 0) * Number(currentPricing.input_per_million) +
      (usage?.cache_read_tokens ?? 0) *
        Number(currentPricing.cache_read_per_million) +
      (usage?.cache_write_tokens ?? 0) *
        Number(currentPricing.cache_write_per_million) +
      (usage?.output_tokens ?? 0) *
        Number(currentPricing.output_per_million)) /
    1_000_000;
  return `${amount.toFixed(8).replace(/\.?0+$/, "") || "0"} ${
    currentPricing.currency
  }`;
}

export function renderEverything(forceBottom = false): void {
  const thread = activeThread();
  const running = isThreadRunning(thread);
  const stopping = Boolean(thread.stopping && running);
  renderThreadList();
  renderHeader();
  renderConversation(forceBottom);
  renderRunLedger();
  renderWorkdir();
  sendButton.disabled = stopping;
  sendLabel.textContent = running ? "插话" : "发送";
  stopButton.hidden = !running;
  stopButton.disabled = stopping;
  stopButton.textContent = stopping ? "停止中" : "停止";
  composerStatus.textContent = stopping
    ? "停止中 · 等待检查点"
    : running
      ? `${steeringLabel(thread.steeringMode)} · Enter 插话 · Alt+S 切换`
      : "Enter 发送 · Shift+Enter 换行";
  promptInput.placeholder = running
    ? `当前轮运行中 · ${steeringLabel(thread.steeringMode)} steering`
    : "输入任务或命令…";
}

function updateAssistant(thread: Thread, message: UiMessage): void {
  renderTarget = { threadId: thread.id, messageId: message.id };
  if (renderFrame !== undefined) {
    return;
  }
  renderFrame = requestAnimationFrame(() => {
    renderFrame = undefined;
    const target = renderTarget;
    renderTarget = null;
    renderThreadList();
    const active = activeThread();
    if (!target || target.threadId !== active.id) {
      return;
    }
    const current = active.messages.find(
      (candidate) => candidate.id === target.messageId,
    );
    if (!current) {
      return;
    }
    const nearBottom =
      conversationScroll.scrollHeight -
        conversationScroll.scrollTop -
        conversationScroll.clientHeight <
      180;
    const article = conversation.querySelector<HTMLElement>(
      `.assistant-message[data-message-id="${current.id}"]`,
    );
    if (!article) {
      renderConversation(nearBottom);
    } else if (!assistantHasVisibleContent(current)) {
      article.remove();
    } else {
      const wrapper = document.createElement("div");
      const currentIndex = thread.messages.findIndex((m) => m.id === current.id);
      wrapper.innerHTML = renderAssistant(
        current,
        showAuthorFor(thread.messages, currentIndex),
      );
      article.replaceWith(wrapper.firstElementChild as Element);
    }
    renderRunLedger();
    if (nearBottom) {
      conversationScroll.scrollTop = conversationScroll.scrollHeight;
    }
  });
}

function appendAssistantText(message: UiMessage, fragment: string): void {
  const last = message.parts[message.parts.length - 1];
  if (last?.type === "text") {
    last.text += fragment;
  } else {
    message.parts.push({ type: "text", text: fragment });
  }
  message.text += fragment;
}

function appendAssistantThinking(message: UiMessage, fragment: string): void {
  const last = message.parts[message.parts.length - 1];
  if (last?.type === "thinking") {
    last.text += fragment;
  } else {
    message.parts.push({ type: "thinking", text: fragment });
  }
  message.thinking += fragment;
}

function getAssistantTool(message: UiMessage, id: string): ToolEvent | undefined {
  return message.parts
    .filter((part): part is ToolPart => part.type === "tool")
    .map((part) => part.tool)
    .find((tool) => tool.id === id);
}

function markStreamActivity(context: StreamContext): void {
  const { thread, assistant, userMessage } = context;
  assistant.queued = false;
  if (userMessage?.steeringState && userMessage.steeringState !== "delivered") {
    userMessage.steeringState = "delivered";
    schedulePersist();
    if (thread.id === state.activeThreadId) {
      renderConversation(false);
      renderThreadList();
    }
  }
  thread.runStartedAt ??= Date.now();
}

function refreshUserState(thread: Thread): void {
  thread.updatedAt = Date.now();
  schedulePersist();
  if (thread.id === state.activeThreadId) {
    renderConversation(false);
    renderThreadList();
  }
}

function handleChatEvent(event: ChatEvent, context: StreamContext): boolean {
  const { thread } = context;
  let { assistant } = context;
  if (event.kind === "thinking") {
    markStreamActivity(context);
    appendAssistantThinking(assistant, event.text ?? "");
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "text") {
    markStreamActivity(context);
    appendAssistantText(assistant, event.text ?? "");
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "tool_use" && event.id) {
    markStreamActivity(context);
    const tool: ToolEvent = {
      id: event.id,
      name: event.name ?? "tool",
      input: event.input ?? {},
      state: "running",
    };
    assistant.parts.push({ type: "tool", tool });
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "tool_approval" && event.id && event.approval_id) {
    markStreamActivity(context);
    const tool = getAssistantTool(assistant, event.id);
    if (tool) {
      if (autoApprovedApprovalIds.delete(event.approval_id)) {
        tool.state = "running";
        tool.approvalId = undefined;
      } else {
        tool.state = "waiting";
        tool.approvalId = event.approval_id;
      }
      if (event.mode) {
        thread.approvalMode = event.mode;
      }
      updateAssistant(thread, assistant);
    }
    return false;
  }
  if (event.kind === "tool_result" && event.id) {
    markStreamActivity(context);
    let tool = getAssistantTool(assistant, event.id);
    if (!tool) {
      tool = {
        id: event.id,
        name: event.name ?? "tool",
        input: {},
        state: "done",
      };
      assistant.parts.push({ type: "tool", tool });
    }
    tool.state = "done";
    tool.output = event.output ?? "";
    tool.hasImage = Boolean(event.has_image);
    tool.isError = Boolean(event.is_error);
    tool.approvalId = undefined;
    tool.elapsedMs = event.elapsed_ms;
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "steering_ack") {
    if (context.userMessage) {
      context.userMessage.steeringState = "acknowledged";
    }
    refreshUserState(thread);
    return false;
  }
  if (event.kind === "steering_injected") {
    assistant.streaming = false;
    assistant.queued = false;
    assistant.durationMs = Date.now() - assistant.createdAt;
    assistant.stopReason = "steering";

    const text = event.text ?? "";
    let userMessage = [...thread.messages]
      .reverse()
      .find(
        (message) =>
          message.role === "user" &&
          message.text === text &&
          (message.steeringState === "queued" ||
            message.steeringState === "acknowledged"),
      );
    if (!userMessage) {
      userMessage = createUserMessage(text);
      userMessage.steeringState = "delivered";
      thread.messages.push(userMessage);
    } else {
      userMessage.steeringState = "delivered";
    }

    assistant = createAssistantMessage();
    thread.messages.push(assistant);
    context.assistant = assistant;
    context.userMessage = userMessage;
    thread.runStartedAt ??= Date.now();
    thread.updatedAt = Date.now();
    schedulePersist();
    if (thread.id === state.activeThreadId) {
      renderConversation(false);
      renderRunLedger();
    }
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "steering_cancelled") {
    thread.stopping = false;
    assistant.streaming = false;
    assistant.queued = false;
    assistant.stopReason = "cancelled";
    assistant.durationMs = Date.now() - assistant.createdAt;
    thread.stopReason = "cancelled";
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "plan" && event.plan_id) {
    markStreamActivity(context);
    assistant.parts.push({
      type: "plan",
      plan: {
        id: event.plan_id,
        threadId: thread.id,
        sessionId: thread.sessionId,
        text: event.text ?? "",
        confirmed: false,
        discarded: false,
        executed: false,
      },
    });
    updateAssistant(thread, assistant);
    return false;
  }
  if (event.kind === "error") {
    thread.stopping = false;
    assistant.error = event.message ?? "未知错误";
    if (context.userMessage?.steeringState) {
      context.userMessage.steeringState = "failed";
    }
    thread.stopReason = "error";
    updateAssistant(thread, assistant);
    return true;
  }
  if (event.kind === "usage") {
    thread.usage = event.usage;
    schedulePersist();
    if (thread.id === state.activeThreadId) {
      renderRunLedger();
    }
    return false;
  }
  if (event.kind === "context_warning") {
    thread.contextWarning = {
      estimate_tokens: event.estimate_tokens ?? 0,
      budget_tokens: event.budget_tokens ?? 0,
      ratio: event.ratio ?? 0,
    };
    schedulePersist();
    showToast(
      `工作集接近预算：${Math.round(
        (event.ratio ?? 0) * 100,
      )}%（可运行 /compact）`,
    );
    if (thread.id === state.activeThreadId) {
      renderRunLedger();
    }
    return false;
  }
  if (event.kind === "done") {
    thread.stopping = false;
    thread.sessionId = event.session_id ?? thread.sessionId;
    const stopReason = event.stop_reason ?? "—";
    assistant.stopReason = stopReason;
    if (stopReason !== "injected") {
      thread.stopReason = stopReason;
    }
    return true;
  }
  return false;
}


async function submitPrompt(rawPrompt: string): Promise<void> {
  const question = rawPrompt.trim();
  const images = peekPendingImages();
  if (!question && !images.length) {
    return;
  }
  const slashCommand = parseSlashCommand(question);
  if (slashCommand && images.length) {
    showToast("图片附件不能跟斜杠命令一起发送");
    return;
  }
  if (slashCommand) {
    promptInput.value = "";
    resizePrompt();
    closeCommandPalette();
    await runSlashCommand(
      slashCommand.name,
      slashCommand.argument,
      slashCommand.command,
    );
    return;
  }
  const thread = activeThread();
  if (images.length && thread.steeringMode !== "queue") {
    showToast("带图输入只支持 queue 档；按 Alt+S 切回 queue");
    return;
  }
  takePendingImages();
  promptInput.value = "";
  resizePrompt();
  closeCommandPalette();
  if (isThreadRunning(thread)) {
    await submitSteering(thread, question, images);
    return;
  }

  commandReceipt = null;
  if (!thread.sessionId) {
    // 先由客户端定下 UUID，流式期间切换执行等级才能精确发给当前会话。
    thread.sessionId = createId();
  }
  const userMessage = createUserMessage(question);
  userMessage.images = images;
  const assistant = createAssistantMessage();
  thread.messages.push(userMessage);
  thread.messages.push(assistant);
  if (isAutoThreadTitle(thread.title)) {
    thread.title = threadTitleFor(question);
  }
  thread.model = selectedModelId() || thread.model;
  thread.updatedAt = Date.now();
  thread.usage = undefined;
  thread.runStartedAt = Date.now();
  renderEverything(true);
  schedulePersist();
  await runChatRequest(
    { thread, assistant, userMessage },
    question,
    thread.steeringMode,
    images,
  );
}

async function submitPlanningPrompt(task: string): Promise<void> {
  const thread = activeThread();
  if (!task.trim()) {
    showToast("用法：/plan <任务>");
    return;
  }
  if (hasPendingImages()) {
    showToast("plan mode 不能携带图片附件");
    return;
  }
  if (isThreadRunning(thread)) {
    showToast("当前轮结束后再进入 plan mode");
    return;
  }
  if (thread.steeringMode !== "queue") {
    showToast("plan mode 只支持 queue 档；按 Alt+S 切回 queue");
    return;
  }
  promptInput.value = "";
  resizePrompt();
  closeCommandPalette();
  commandReceipt = null;
  thread.sessionId ||= createId();
  const label = `PLAN / ${task.trim()}`;
  const userMessage = createUserMessage(label);
  const assistant = createAssistantMessage();
  thread.messages.push(userMessage);
  thread.messages.push(assistant);
  if (isAutoThreadTitle(thread.title)) {
    thread.title = threadTitleFor(task);
  }
  thread.model = selectedModelId() || thread.model;
  thread.updatedAt = Date.now();
  thread.usage = undefined;
  thread.runStartedAt = Date.now();
  renderEverything(true);
  schedulePersist();
  await runChatRequest(
    { thread, assistant, userMessage },
    task,
    "queue",
    [],
    false,
    true,
  );
}

function findPlan(
  planId: string,
): { thread: Thread; plan: PlanEvent } | null {
  for (const thread of state.threads) {
    for (const message of thread.messages) {
      for (const part of message.parts) {
        if (part.type === "plan" && part.plan.id === planId) {
          return { thread, plan: part.plan };
        }
      }
    }
  }
  return null;
}

async function handlePlanAction(planId: string, action: string): Promise<void> {
  const found = findPlan(planId);
  if (!found) {
    return;
  }
  const { thread, plan } = found;
  if (action === "discard") {
    plan.discarded = true;
    thread.updatedAt = Date.now();
    persistState();
    renderEverything();
    showToast("已丢弃计划；不会执行任何写操作");
    return;
  }
  if (action !== "confirm" || plan.confirmed || plan.executed) {
    return;
  }
  if (isThreadRunning(thread)) {
    showToast("当前轮结束后才能执行计划");
    return;
  }
  try {
    const response = await fetch(
      `${API_BASE}/plans/${encodeURIComponent(plan.id)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: plan.sessionId }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as PlanConfirmResponse;
    if (!result.confirmed) {
      throw new Error("服务端没有记录计划确认");
    }
    plan.confirmed = true;
    plan.executed = true;
    const userMessage = createUserMessage(`CONFIRM PLAN / ${plan.id.slice(0, 8)}`);
    const assistant = createAssistantMessage();
    thread.messages.push(userMessage);
    thread.messages.push(assistant);
    thread.updatedAt = Date.now();
    thread.usage = undefined;
    thread.runStartedAt = Date.now();
    renderEverything(true);
    schedulePersist();
    await runChatRequest(
      { thread, assistant, userMessage },
      "用户已确认上述计划，请执行。",
      "queue",
      [],
      false,
      false,
      plan.id,
    );
  } catch (error) {
    plan.confirmed = false;
    plan.executed = false;
    thread.updatedAt = Date.now();
    persistState();
    renderEverything();
    showToast(error instanceof Error ? error.message : String(error));
  }
}

async function submitSteering(
  thread: Thread,
  question: string,
  images: ImageAttachment[] = [],
): Promise<void> {
  const userMessage = createUserMessage(question);
  userMessage.images = images;
  userMessage.steeringState = "queued";
  const assistant = createAssistantMessage(true);
  thread.messages.push(userMessage);
  thread.messages.push(assistant);
  thread.updatedAt = Date.now();
  thread.runStartedAt ??= Date.now();
  if (thread.id === state.activeThreadId) {
    promptInput.value = "";
    resizePrompt();
    renderEverything(true);
  }
  schedulePersist();
  showToast(
    `${steeringLabel(thread.steeringMode)} steering 已发送 · ${
      thread.steeringMode === "cooperative"
        ? "下一次请求前并入"
        : thread.steeringMode === "preemptive"
          ? "等待当前轮退场"
          : "当前轮结束后执行"
    }`,
  );
  await runChatRequest(
    { thread, assistant, userMessage },
    question,
    thread.steeringMode,
    images,
  );
}

// 每个线程当前那次 SSE 流的控制器。断流收线时（后端说没在跑）用它把
// 还挂着的读掐掉——否则 fetch 永远 pending，前端只能靠刷新恢复。
const streamingControllers = new Map<string, AbortController>();

async function runChatRequest(
  context: StreamContext,
  question: string,
  steering: SteeringMode,
  images: ImageAttachment[] = [],
  retry = false,
  planMode = false,
  planId = "",
): Promise<void> {
  const { thread } = context;
  const controller = new AbortController();
  streamingControllers.set(thread.id, controller);
  let gotDone = false;
  let receivedError = false;
  let acknowledged = false;
  let finalStopReason = "";
  const shouldHydrateImages = () =>
    gotDone &&
    !receivedError &&
    finalStopReason !== "injected" &&
    thread.messages.some((message) =>
      assistantTools(message).some((tool) => tool.hasImage),
    );
  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: retry ? "" : question,
        session_id: thread.sessionId,
        approval_mode: thread.approvalMode,
        steering,
        ...(images.length ? { images } : {}),
        ...(retry ? { retry: true } : {}),
        ...(planMode ? { plan_mode: true } : {}),
        ...(planId ? { plan_id: planId } : {}),
        // 只有用户改过目录的会话才带：不带 = 服务端保持原值，
        // 不会每条消息都把 TUI / curl 改过的目录冲回默认。
        ...(thread.workdir ? { workdir: thread.workdir } : {}),
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    if (!response.body) {
      throw new Error("浏览器没有拿到 SSE body");
    }
    await consumeSse(response.body, (event) => {
      if (event.kind === "steering_ack") {
        acknowledged = true;
      }
      if (event.kind === "error") {
        receivedError = true;
        showToast(event.message ?? "本轮执行出错");
      }
      if (event.kind === "done") {
        finalStopReason = event.stop_reason ?? "";
      }
      gotDone = handleChatEvent(event, context) || gotDone;
    });
    if (!gotDone && !receivedError) {
      throw new Error("SSE 连接结束但没有收到 done 事件");
    }
  } catch (error) {
    // 被 stopActiveTurn 主动掐断的流：本地状态已经按「已中断」收过线，
    // 不要把 abort 的异常再标成错误盖上去。
    if (!controller.signal.aborted) {
      const message =
        error instanceof TypeError
          ? "连不上本地后端。先在 server/ 运行：uv run python main.py desktop"
          : error instanceof Error
            ? error.message
            : String(error);
      context.assistant.error = message;
      if (context.userMessage?.steeringState) {
        context.userMessage.steeringState = "failed";
      }
      thread.stopReason = "error";
      showToast(message);
    }
  } finally {
    if (streamingControllers.get(thread.id) === controller) {
      streamingControllers.delete(thread.id);
    }
    context.assistant.streaming = false;
    context.assistant.queued = false;
    thread.stopping = false;
    if (shouldHydrateImages()) {
      await hydrateThread(thread, true);
    }
    context.assistant.durationMs ??=
      Date.now() - context.assistant.createdAt;
    if (acknowledged && finalStopReason === "injected") {
      const index = thread.messages.findIndex(
        (message) => message.id === context.assistant.id,
      );
      if (index >= 0 && !context.assistant.text && !context.assistant.parts.length) {
        thread.messages.splice(index, 1);
      }
    }
    thread.updatedAt = Date.now();
    if (!isThreadRunning(thread)) {
      thread.runStartedAt = undefined;
    }
    persistState();
    if (thread.id === state.activeThreadId) {
      renderEverything(true);
      if (settingsModal.hidden && !isResumeOpen() && traceModal.hidden) {
        promptInput.focus();
      }
    }
  }
}

function finalizeInterruptedTurn(thread: Thread): void {
  // 只收「本地以为在跑」的那条助手消息：done/cancelled 事件既然到不了，
  // 它没有别的机会收线。卡在假 running/waiting 的工具卡一并收成错误结果，
  // 不然「等待结果…」会永远转下去。
  const assistant = runningAssistant(thread);
  if (assistant) {
    for (const tool of assistantTools(assistant)) {
      if (tool.state !== "done") {
        tool.state = "done";
        tool.isError = true;
        tool.output = tool.output || "连接中断，没有拿到结果。";
        tool.elapsedMs = undefined;
        tool.approvalId = undefined;
      }
    }
    assistant.streaming = false;
    assistant.queued = false;
    assistant.stopReason = "cancelled";
    assistant.durationMs ??= Date.now() - assistant.createdAt;
  }
  thread.stopReason = "cancelled";
  thread.stopping = false;
  thread.runStartedAt = undefined;
  // 掐掉还挂着的读——正是它让这轮看起来永远在跑。
  streamingControllers.get(thread.id)?.abort();
  streamingControllers.delete(thread.id);
  schedulePersist();
}

async function stopActiveTurn(thread = activeThread()): Promise<void> {
  if (!thread.sessionId || !isThreadRunning(thread) || thread.stopping) {
    if (!isThreadRunning(thread)) {
      showToast("当前没有运行中的轮次");
    }
    return;
  }
  thread.stopping = true;
  if (thread.id === state.activeThreadId) {
    renderEverything();
  }
  renderThreadList();
  try {
    const response = await fetch(`${API_BASE}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: thread.sessionId }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as StopResponse;
    if (!result.active) {
      // 后端如实说「没在跑」：本地的 streaming 是断流残留——SSE 客户端
      // 先断了，done/error 永远到不了。就地收线，让 Stop 真能解锁。
      finalizeInterruptedTurn(thread);
      showToast("后端没有正在运行的轮次，本地这轮已收为中断");
      if (thread.id === state.activeThreadId) {
        renderEverything();
      }
      renderThreadList();
      return;
    }
    showToast("停止请求已发送 · 将在下一个检查点收线");
  } catch (error) {
    thread.stopping = false;
    showToast(
      `停止失败：${error instanceof Error ? error.message : String(error)}`,
    );
    if (thread.id === state.activeThreadId) {
      renderEverything();
    }
    renderThreadList();
  }
}

function newThread(): boolean {
  const current = activeThread();
  if (!current.messages.length) {
    commandReceipt = null;
    renderConversation();
    promptInput.focus();
    closeThreadRail();
    return false;
  }
  const thread = createThread();
  state.threads.push(thread);
  state.activeThreadId = thread.id;
  commandReceipt = null;
  persistState();
  renderEverything();
  promptInput.focus();
  closeThreadRail();
  return true;
}

export function switchThread(threadId: string): void {
  if (!state.threads.some((thread) => thread.id === threadId)) {
    return;
  }
  commandReceipt = null;
  state.activeThreadId = threadId;
  persistState();
  renderEverything();
  closeThreadRail();
  closeResume();
  promptInput.focus();
  const thread = activeThread();
  if (!thread.messagesLoaded) {
    showToast(`正在读取会话 ${shortSession(thread.sessionId)}…`);
    void hydrateThread(thread);
  }
}

export function resizePrompt(): void {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 160)}px`;
}

function parseSlashCommand(raw: string): {
  name: string;
  argument: string;
  command?: SlashCommand;
} | null {
  if (!raw.startsWith("/")) {
    return null;
  }
  const [head = "", ...rest] = raw.slice(1).split(/\s+/);
  const name = head.toLowerCase();
  return {
    name,
    argument: rest.join(" ").trim(),
    command: SLASH_COMMANDS_BY_NAME.get(name),
  };
}





async function showCapabilities(): Promise<void> {
  const thread = activeThread();
  try {
    const response = await fetch(`${API_BASE}/capabilities`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const capabilities = (await response.json()) as CapabilitiesResponse;
    const sources = new Map<string, string[]>();
    for (const tool of capabilities.tools) {
      sources.set(tool.source, [...(sources.get(tool.source) ?? []), tool.name]);
    }
    const sourceLines = [...sources.entries()].sort().map(
      ([source, names]) =>
        `${source.toUpperCase()} ${names.length}: ${names.join(", ")}`,
    );
    const body = [
      `PYTHON     ${capabilities.runtime.python}`,
      `PROVIDER   ${capabilities.runtime.provider}`,
      `MODEL      ${capabilities.runtime.model}`,
      `EFFORT     ${capabilities.runtime.effort || "omit"}`,
      `TRIGGERS   ${capabilities.runtime.triggers}`,
      "",
      "工具",
      ...(sourceLines.length ? sourceLines : ["none"]),
      "",
      "MCP",
      ...(capabilities.mcp.length
        ? capabilities.mcp.map(
            (entry) =>
              `${entry.alias}=${entry.state}${entry.command ? ` (${entry.command})` : ""}`,
          )
        : ["none configured/not started"]),
      "",
      `SANDBOX    ${capabilities.environment.sandbox}`,
      `BOUNDARY   ${capabilities.environment.sandbox_boundary}`,
    ].join("\n");
    commandReceipt = {
      threadId: thread.id,
      title: "能力",
      body,
      error: false,
    };
  } catch (error) {
    commandReceipt = {
      threadId: thread.id,
      title: "能力",
      body: error instanceof Error ? error.message : String(error),
      error: true,
    };
  }
  thread.updatedAt = Date.now();
  persistState();
  renderEverything(true);
}

const STEERING_MODES: SteeringMode[] = [
  "queue",
  "cooperative",
  "preemptive",
];

const TRANSCRIPT_MODES: TranscriptMode[] = [
  "normal",
  "thinking",
  "verbose",
];

function setTranscriptMode(mode: TranscriptMode): void {
  transcriptMode = mode;
  const descriptions: Record<TranscriptMode, string> = {
    normal: "看回答、简略工具卡与折叠思考；审批仍显示完整入参。",
    thinking: "在 normal 基础上自动展开思考；工具仍保持简略。",
    verbose: "显示思考块、工具入参和工具出参。",
  };
  showCommandResult("转录", `${mode}\n${descriptions[mode]}`);
}

function setSteeringMode(mode: SteeringMode): void {
  const thread = activeThread();
  thread.steeringMode = mode;
  thread.updatedAt = Date.now();
  persistState();
  renderThreadList();
  renderRunLedger();
  const descriptions: Record<SteeringMode, string> = {
    queue: "新消息等当前轮结束后再跑",
    cooperative: "下一次模型请求前并入当前轮",
    preemptive: "下一个检查点打断当前轮",
  };
  showToast(
    `Steering：${mode.toUpperCase()} · ${descriptions[mode]}${
      isThreadRunning(thread) ? " · 下一条消息生效" : ""
    }`,
  );
}

function cycleSteeringMode(): void {
  const thread = activeThread();
  const index = STEERING_MODES.indexOf(thread.steeringMode);
  setSteeringMode(STEERING_MODES[(index + 1) % STEERING_MODES.length]);
}

function showCommandResult(title: string, body: string, error = false): void {
  commandReceipt = {
    threadId: state.activeThreadId,
    title,
    body,
    error,
  };
  renderConversation(true);
}

async function runMemoryCommand(argument: string): Promise<void> {
  // 不等当前轮：记忆是每圈圈界重读的，追加只影响之后的请求。
  try {
    if (!argument) {
      const response = await fetch(`${API_BASE}/memory`);
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      const memory = (await response.json()) as { text: string };
      showCommandResult(
        "记忆",
        memory.text.trim() || "（记忆还是空的。/memory <内容> 记第一条。）",
      );
      return;
    }
    const response = await fetch(`${API_BASE}/memory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: argument }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as { line: string };
    showToast("已记下，下一圈请求生效");
    showCommandResult("记忆", `已记下（下一圈请求生效）：\n${result.line}`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error));
  }
}

async function showStatus(): Promise<void> {
  const thread = activeThread();
  let model = "unavailable";
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(`${API_BASE}/settings`, {
      signal: controller.signal,
    });
    if (response.ok) {
      const snapshot = (await response.json()) as SettingsSnapshot;
      model = snapshot.model_id;
      setCurrentEffort(snapshot.effort);
      runEffort.textContent = effortLabel(currentEffort);
    }
  } catch {
    model = "unavailable · backend offline";
  } finally {
    window.clearTimeout(timeout);
  }
  const assistant = lastAssistant(thread);
  const tools = assistantTools(assistant);
  const stateLabel = threadStatus(thread);
  showCommandResult(
    "状态",
    [
      `session   ${thread.sessionId || "pending"}`,
      `model     ${model}`,
      `thinking  ${effortLabel(currentEffort)}`,
      `approval  ${thread.approvalMode.toUpperCase()}`,
      `steering  ${thread.steeringMode.toUpperCase()}`,
      `state     ${stateLabel}`,
      `turns     ${thread.messages.filter((message) => message.role === "user").length}`,
      `tools     ${tools.length}`,
      `stop      ${thread.stopReason || "—"}`,
      "storage   server /sessions + JSONL + SQLite · localStorage is UI cache",
    ].join("\n"),
  );
}

async function copyLatestAnswer(): Promise<void> {
  const thread = activeThread();
  const answer = [...thread.messages]
    .reverse()
    .find(
      (message) =>
        message.role === "assistant" && !message.streaming && message.text.trim(),
    );
  if (!answer) {
    showToast("还没有可复制的完整回答");
    return;
  }
  try {
    await navigator.clipboard.writeText(answer.text);
  } catch {
    const helper = document.createElement("textarea");
    helper.value = answer.text;
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    const copied = document.execCommand("copy");
    helper.remove();
    if (!copied) {
      showToast("系统剪贴板不可用");
      return;
    }
  }
  showToast(`已复制最近回答 · ${answer.text.length.toLocaleString()} 字`);
}

async function exportCurrentSession(): Promise<void> {
  const thread = activeThread();
  if (!thread.sessionId) {
    showCommandResult("导出", "当前会话还没有落盘，不能导出。", true);
    return;
  }
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${thread.sessionId}/markdown`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename =
      disposition.match(/filename="([^"]+)"/)?.[1] ??
      `agentlearn-${thread.sessionId}.md`;
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showCommandResult(
      "导出",
      `会话 ${shortSession(thread.sessionId)}\nMarkdown 下载已开始：${filename}`,
    );
  } catch (error) {
    showCommandResult(
      "导出",
      error instanceof Error ? error.message : String(error),
      true,
    );
  }
}

async function resendFailedSteering(
  thread: Thread,
  messageId: string,
): Promise<void> {
  const index = thread.messages.findIndex((message) => message.id === messageId);
  const message = thread.messages[index];
  if (!message || message.role !== "user") {
    return;
  }
  if (message.images?.length) {
    // 带图消息没法从历史里还原成待发送附件；保留卡片，提示手动重发，
    // 免得图片悄悄丢了还以为发过。
    showToast("这条插话带图片，请重新输入并附加原图");
    return;
  }
  // 未送达的消息既没进模型也没进 JSONL：从时间线上摘掉，再走正常提交，
  // 新一轮会重新落盘，不留一条永远失败的残影。
  thread.messages.splice(index, 1);
  renderConversation(false);
  renderThreadList();
  await submitPrompt(message.text);
}

async function retryFailedTurn(
  thread: Thread,
  assistantId?: string,
): Promise<void> {
  if (isThreadRunning(thread)) {
    showToast("当前轮还在运行；先停止或等待它收线");
    return;
  }
  const failedIndex = assistantId
    ? thread.messages.findIndex(
        (message) => message.id === assistantId && message.role === "assistant",
      )
    : [...thread.messages]
        .map((message, index) => ({ message, index }))
        .reverse()
        .find(
          ({ message }) =>
            message.role === "assistant" && Boolean(message.error),
        )?.index ?? -1;
  const failed = thread.messages[failedIndex];
  if (!failed || failed.role !== "assistant" || !failed.error) {
    showToast("没有可重试的失败请求");
    return;
  }
  const previousUser = [...thread.messages.slice(0, failedIndex)]
    .reverse()
    .find((message) => message.role === "user");
  if (
    !previousUser ||
    (!previousUser.text.trim() && !(previousUser.images ?? []).length)
  ) {
    showToast("找不到这条失败请求对应的用户消息");
    return;
  }
  const assistant = createAssistantMessage();
  thread.messages.splice(failedIndex + 1, 0, assistant);
  thread.stopReason = "";
  thread.usage = undefined;
  thread.stopping = false;
  thread.runStartedAt = Date.now();
  thread.updatedAt = Date.now();
  if (thread.id === state.activeThreadId) {
    renderEverything(true);
  }
  schedulePersist();
  await runChatRequest(
    { thread, assistant },
    previousUser.text,
    "queue",
    [],
    true,
  );
}

async function runSlashCommand(
  name: string,
  argument: string,
  command?: SlashCommand,
): Promise<void> {
  if (!command) {
    showToast(`没有 /${name || "?"} 这条命令。输入 / 查看全部命令。`);
    return;
  }
  if (command.name === "help") {
    promptInput.value = "/";
    resizePrompt();
    resetCommandSelection();
    updateCommandPalette();
    promptInput.focus();
    promptInput.setSelectionRange(1, 1);
    return;
  }
  if (command.name === "config") {
    openSettings();
    return;
  }
  if (command.name === "model") {
    if (!argument) {
      openSettings();
      return;
    }
    await quickSetModel(argument);
    return;
  }
  if (command.name === "find") {
    if (argument) {
      findConversationMatch(argument);
    } else {
      openConversationFind();
    }
    return;
  }
  if (command.name === "status") {
    await showStatus();
    return;
  }
  if (command.name === "trace") {
    openTrace();
    return;
  }
  if (command.name === "stop") {
    await stopActiveTurn();
    return;
  }
  if (command.name === "retry") {
    await retryFailedTurn(activeThread());
    return;
  }
  if (command.name === "memory") {
    await runMemoryCommand(argument);
    return;
  }
  if (command.name === "copy") {
    await copyLatestAnswer();
    return;
  }
  if (command.name === "export") {
    await exportCurrentSession();
    return;
  }
  if (command.name === "steering") {
    if (!argument) {
      cycleSteeringMode();
      return;
    }
    const mode = argument.toLowerCase() as SteeringMode;
    if (!STEERING_MODES.includes(mode)) {
      showToast(`未知 steering 档位：${argument}`);
      return;
    }
    setSteeringMode(mode);
    return;
  }
  if (command.name === "transcript") {
    if (!argument) {
      const index = TRANSCRIPT_MODES.indexOf(transcriptMode);
      setTranscriptMode(
        TRANSCRIPT_MODES[(index + 1) % TRANSCRIPT_MODES.length],
      );
      return;
    }
    const mode = argument.toLowerCase() as TranscriptMode;
    if (!TRANSCRIPT_MODES.includes(mode)) {
      showCommandResult(
        "转录",
        `未知档位：${argument}。可用：normal / thinking / verbose。`,
        true,
      );
      return;
    }
    setTranscriptMode(mode);
    return;
  }
  if (command.name === "effort") {
    if (!argument) {
      await cycleEffort();
      return;
    }
    const normalized = argument.toLowerCase();
    const aliases: Record<string, SettingsSnapshot["effort"]> = {
      default: "",
      omit: "",
      none: "",
      off: "off",
      low: "low",
      medium: "medium",
      high: "high",
      max: "max",
    };
    const effort = aliases[normalized];
    if (effort === undefined) {
      showToast(`未知思考等级：${argument}`);
      return;
    }
    await setEffort(effort);
    return;
  }
  if (command.name === "approval") {
    if (!argument) {
      cycleApprovalMode();
      return;
    }
    const mode = argument.toLowerCase() as ApprovalMode;
    if (mode !== "approve" && mode !== "auto" && mode !== "yolo") {
      showToast(`未知执行等级：${argument}`);
      return;
    }
    setApprovalMode(mode);
    return;
  }
  if (command.name === "plan") {
    await submitPlanningPrompt(argument);
    return;
  }
  if (command.name === "capabilities") {
    await showCapabilities();
    return;
  }
  if (command.name === "resume") {
    if (argument) {
      const thread = findResumeThread(argument);
      if (thread) {
        await hydrateThread(thread, true);
        switchThread(thread.id);
        showToast(`已续接：${thread.title}`);
      }
      return;
    }
    await openResume();
    return;
  }
  if (command.name === "clear" || command.name === "new") {
    const created = newThread();
    showToast(
      created
        ? command.name === "clear"
          ? "上下文已清空，新会话已建立"
          : "新会话已建立"
        : "当前已经是空会话",
    );
    return;
  }
  const prompts: Record<string, string> = {
    now: "现在几点了？",
    files: "看一下 sandbox 目录里有哪些文件。",
    boundary: "先说明你准备怎么做，再执行，最后把结论和依据分开列出来。",
  };
  await submitPrompt(prompts[command.name]);
}

function openThreadRail(): void {
  appShell.classList.add("rail-open");
  scrim.classList.add("is-visible");
}

function closeThreadRail(): void {
  appShell.classList.remove("rail-open");
  scrim.classList.remove("is-visible");
}

function schedulePersist(): void {
  window.setTimeout(() => persistState(), 250);
}

traceToggle.addEventListener("click", openTrace);
workspaceDiffToggle.addEventListener("click", openWorkspaceDiff);
newThreadButton.addEventListener("click", newThread);
railCloseButton.addEventListener("click", closeThreadRail);
railOpenButton.addEventListener("click", openThreadRail);
scrim.addEventListener("click", closeThreadRail);
stopButton.addEventListener("click", () => {
  void stopActiveTurn();
});
runRailToggle.addEventListener("click", () => {
  if (window.matchMedia("(max-width: 1180px)").matches) {
    const open = runRail.classList.toggle("run-rail-open");
    runRailToggle.setAttribute("aria-pressed", String(open));
    return;
  }
  const hidden = appShell.classList.toggle("run-rail-hidden");
  runRailToggle.setAttribute("aria-pressed", String(!hidden));
});
threadList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-thread-id]",
  );
  if (button?.dataset.threadId) {
    switchThread(button.dataset.threadId);
  }
});

// —— 会话行右键：复制完整会话 ID ——
// UUID 是给工具和日志用的，不常驻行内（缩略 ID 对人是噪音），
// 需要时右键取全量。菜单定位到鼠标位置，贴边时向内收。
let contextMenuThreadId = "";

function hideThreadContextMenu(): void {
  threadContextMenu.hidden = true;
  contextMenuThreadId = "";
}

function showThreadContextMenu(x: number, y: number, threadId: string): void {
  contextMenuThreadId = threadId;
  threadContextMenu.hidden = false;
  const rect = threadContextMenu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - rect.width - 8);
  const top = Math.min(y, window.innerHeight - rect.height - 8);
  threadContextMenu.style.left = `${Math.max(8, left)}px`;
  threadContextMenu.style.top = `${Math.max(8, top)}px`;
}

async function copyTextToClipboard(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    // WKWebView 或权限受限时的兜底：临时 textarea + execCommand
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

threadList.addEventListener("contextmenu", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-thread-id]",
  );
  if (!button?.dataset.threadId) {
    return;
  }
  event.preventDefault();
  const thread = state.threads.find((item) => item.id === button.dataset.threadId);
  if (!thread?.sessionId) {
    showToast("会话还没有落盘，暂时没有 ID");
    return;
  }
  showThreadContextMenu(event.clientX, event.clientY, thread.id);
});

copySessionIdButton.addEventListener("click", async () => {
  const thread = state.threads.find((item) => item.id === contextMenuThreadId);
  hideThreadContextMenu();
  if (!thread?.sessionId) {
    return;
  }
  const ok = await copyTextToClipboard(thread.sessionId);
  showToast(ok ? `已复制会话 ID · ${thread.sessionId.slice(0, 8)}…` : "复制失败");
});

document.addEventListener("pointerdown", (event) => {
  if (threadContextMenu.hidden) {
    return;
  }
  if (!(event.target as Element).closest("#thread-context-menu")) {
    hideThreadContextMenu();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !threadContextMenu.hidden) {
    hideThreadContextMenu();
  }
});

window.addEventListener("resize", hideThreadContextMenu);
threadList.addEventListener("scroll", hideThreadContextMenu);
conversation.addEventListener("click", (event) => {
  const retryButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-retry-message-id]",
  );
  if (retryButton?.dataset.retryMessageId) {
    void retryFailedTurn(activeThread(), retryButton.dataset.retryMessageId);
    return;
  }
  const resendButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-resend-message-id]",
  );
  if (resendButton?.dataset.resendMessageId) {
    void resendFailedSteering(
      activeThread(),
      resendButton.dataset.resendMessageId,
    );
    return;
  }
  const planButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-plan-id]",
  );
  if (planButton?.dataset.planId) {
    void handlePlanAction(planButton.dataset.planId, planButton.dataset.planAction ?? "");
    return;
  }
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-prompt]",
  );
  if (button?.dataset.prompt) {
    void submitPrompt(button.dataset.prompt);
  }
});

conversation.addEventListener(
  "toggle",
  (event) => {
    const details = event.target as HTMLDetailsElement | null;
    const key = details?.dataset.reasoningKey;
    if (!key) {
      return;
    }
    if (details.open) {
      expandedReasoning.add(key);
    } else {
      expandedReasoning.delete(key);
    }
  },
  true,
);

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitPrompt(promptInput.value);
});
promptInput.addEventListener("keydown", (event) => {
  const paletteVisible = isCommandPaletteOpen();
  if (paletteVisible && event.key === "ArrowDown") {
    event.preventDefault();
    moveCommandSelection(1);
    return;
  }
  if (paletteVisible && event.key === "ArrowUp") {
    event.preventDefault();
    moveCommandSelection(-1);
    return;
  }
  if (paletteVisible && event.key === "Tab") {
    event.preventDefault();
    if (isFilePalette()) {
      completeSelectedFile();
    } else {
      completeSelectedCommand();
    }
    return;
  }
  if (paletteVisible && event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeCommandPalette();
    return;
  }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    event.stopPropagation();
    if (paletteVisible) {
      if (isFilePalette()) {
        completeSelectedFile();
        return;
      } else {
        const parsed = parseSlashCommand(promptInput.value.trim());
        if (!parsed?.command) {
          completeSelectedCommand();
          return;
        }
      }
    }
    void submitPrompt(promptInput.value);
  }
});
promptInput.addEventListener("input", () => {
  resizePrompt();
  updateCommandPalette();
});
document.addEventListener("keydown", (event) => {
  const modifier = event.metaKey || event.ctrlKey;
  const conversationFindVisible = isConversationFindOpen();
  if (conversationFindVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeConversationFind();
    }
    return;
  }
  const workspaceDiffVisible = !workspaceDiffModal.hidden;
  if (workspaceDiffVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeWorkspaceDiff();
      return;
    }
    if (event.key.toLowerCase() === "r" && !modifier) {
      event.preventDefault();
      void loadWorkspaceDiff();
      return;
    }
    return;
  }
  const traceVisible = !traceModal.hidden;
  if (traceVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeTrace();
      return;
    }
    if (event.key.toLowerCase() === "r" && !modifier) {
      event.preventDefault();
      void loadTrace();
      return;
    }
    return;
  }
  const resumeVisible = handleResumeKeydown(event);
  if (resumeVisible) {
    return;
  }
  const settingsVisible = !settingsModal.hidden;
  if (settingsVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSettings();
    }
    return;
  }
  if (event.altKey && event.code === "KeyT" && !modifier) {
    event.preventDefault();
    void cycleEffort();
    return;
  }
  if (event.key === "Tab" && event.shiftKey) {
    event.preventDefault();
    cycleApprovalMode();
    return;
  }
  if (event.altKey && event.code === "KeyS" && !modifier) {
    event.preventDefault();
    cycleSteeringMode();
    return;
  }
  if (modifier && event.key === ",") {
    event.preventDefault();
    openSettings();
  }
  if (modifier && event.key.toLowerCase() === "n") {
    event.preventDefault();
    newThread();
  }
  if (modifier && event.key.toLowerCase() === "k") {
    event.preventDefault();
    promptInput.focus();
  }
  if (event.key === "Escape") {
    if (isThreadRunning(activeThread())) {
      event.preventDefault();
      void stopActiveTurn();
      return;
    }
    closeThreadRail();
    promptInput.blur();
  }
});
window.setInterval(() => {
  const thread = activeThread();
  if (isThreadRunning(thread) && thread.runStartedAt) {
    runElapsed.textContent = formatDuration(Date.now() - thread.runStartedAt);
  }
}, 100);

setShellBridge({
  fetchServerSessions,
  hydrateThread,
  renderConversation,
  renderEverything,
  renderRunLedger,
  renderThreadList,
  resizePrompt,
  switchThread,
});

function showServerBootstrapError(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  runtimeApiBase.textContent = "启动失败";
  setStatusLight(runtimeLight, "is-error");
  connectionPill.className = "connection-pill is-error";
  connectionPill.innerHTML = '<span class="status-light is-error"></span>离线';
  showToast(`本地运行时启动失败：${message}`);
}

async function bootstrapServer(): Promise<void> {
  runtimeApiBase.textContent = "检测中…";
  renderEverything();
  resizePrompt();
  try {
    await ensureApiServer();
    runtimeApiBase.textContent = new URL(API_BASE).host;
  } catch (error) {
    showServerBootstrapError(error);
    return;
  }
  await Promise.all([
    refreshEffort(),
    // 启动时跟服务端对一次账：标题、轮数、归档状态都以后端为准。
    // 以前只有打开「恢复会话」才合并，侧栏一直顶着 localStorage 里的旧标题
    // （一排「未命名运行」就是这么来的）。失败静默——本地缓存照常能用。
    fetchServerSessions(false),
  ]);
}

void bootstrapServer();
if (window.matchMedia("(max-width: 1180px)").matches) {
  runRailToggle.setAttribute("aria-pressed", "false");
}
