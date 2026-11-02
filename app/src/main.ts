import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import DOMPurify from "dompurify";
import { marked } from "marked";

const API_BASE = "http://127.0.0.1:8000";
const STORAGE_KEY = "agentlearn.desktop.v1";

marked.setOptions({ gfm: true, breaks: true });

type ToolState = "running" | "done";

interface ToolEvent {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  isError?: boolean;
  elapsedMs?: number;
  state: ToolState;
}

interface TextPart {
  type: "text";
  text: string;
}

interface ToolPart {
  type: "tool";
  tool: ToolEvent;
}

type AssistantPart = TextPart | ToolPart;

interface UiMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinking: string;
  parts: AssistantPart[];
  error: string;
  stopReason: string;
  durationMs?: number;
  createdAt: number;
  streaming?: boolean;
}

interface Thread {
  id: string;
  sessionId: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  stopReason: string;
  messages: UiMessage[];
}

interface PersistedState {
  version: 1;
  activeThreadId: string;
  threads: Thread[];
}

interface SettingsSnapshot {
  provider: "anthropic" | "openai";
  base_url: string;
  model_id: string;
  max_tokens: number;
  effort: "" | "off" | "low" | "medium" | "high" | "max";
  api_key_configured: boolean;
  api_key_source: string;
}

interface SettingsPayload {
  provider: string;
  base_url: string;
  model_id: string;
  max_tokens: number;
  effort: string;
  api_key: string | null;
}

interface ChatEvent {
  kind: "thinking" | "text" | "tool_use" | "tool_result" | "done" | "error";
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  elapsed_ms?: number;
  stop_reason?: string;
  session_id?: string;
  message?: string;
}

interface SlashCommand {
  name: string;
  usage: string;
  description: string;
  group: string;
  aliases?: string[];
  takesArgument?: boolean;
}

interface ActivityItem {
  key: string;
  tag: string;
  title: string;
  detail: string;
  status: string;
}

const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "resume",
    usage: "/resume [id]",
    description: "选择并续接历史会话",
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "clear",
    usage: "/clear",
    description: "清空当前上下文并开新会话",
    group: "SESSION",
  },
  {
    name: "new",
    usage: "/new",
    description: "新开一个空会话",
    group: "SESSION",
  },
  {
    name: "status",
    usage: "/status",
    description: "查看当前会话与运行状态",
    group: "RUNTIME",
  },
  {
    name: "model",
    usage: "/model <id>",
    description: "切换运行模型；不带参数打开设置",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "config",
    usage: "/config",
    description: "打开 config.ini 模型设置",
    group: "RUNTIME",
    aliases: ["settings"],
  },
  {
    name: "now",
    usage: "/now",
    description: "触发 now_time 示例",
    group: "TASK",
  },
  {
    name: "files",
    usage: "/files",
    description: "查看 sandbox 目录",
    group: "TASK",
  },
  {
    name: "boundary",
    usage: "/boundary",
    description: "触发 reason / act / report 示例",
    group: "TASK",
  },
  {
    name: "help",
    usage: "/help",
    description: "显示全部命令",
    group: "SYSTEM",
  },
];
const SLASH_COMMANDS_BY_NAME = new Map<string, SlashCommand>();
for (const command of SLASH_COMMANDS) {
  SLASH_COMMANDS_BY_NAME.set(command.name, command);
  for (const alias of command.aliases ?? []) {
    SLASH_COMMANDS_BY_NAME.set(alias, command);
  }
}

function requireElement<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing element: ${selector}`);
  }
  return element;
}

const appShell = requireElement<HTMLElement>("#app-shell");
const threadList = requireElement<HTMLElement>("#thread-list");
const threadCount = requireElement<HTMLElement>("#thread-count");
const newThreadButton = requireElement<HTMLButtonElement>("#new-thread");
const railCloseButton = requireElement<HTMLButtonElement>("#rail-close");
const railOpenButton = requireElement<HTMLButtonElement>("#rail-open");
const runRailToggle = requireElement<HTMLButtonElement>("#run-rail-toggle");
const scrim = requireElement<HTMLElement>("#scrim");
const threadKicker = requireElement<HTMLElement>("#thread-kicker");
const threadTitle = requireElement<HTMLElement>("#thread-title");
const connectionPill = requireElement<HTMLElement>("#connection-pill");
const conversation = requireElement<HTMLElement>("#conversation");
const conversationScroll = requireElement<HTMLElement>("#conversation-scroll");
const composer = requireElement<HTMLFormElement>("#composer");
const promptInput = requireElement<HTMLTextAreaElement>("#prompt");
const composerStatus = requireElement<HTMLElement>("#composer-status");
const sendButton = requireElement<HTMLButtonElement>("#send");
const sendLabel = requireElement<HTMLElement>("#send-label");
const commandPalette = requireElement<HTMLElement>("#command-palette");
const commandList = requireElement<HTMLElement>("#command-list");
const runState = requireElement<HTMLElement>("#run-state");
const runStateCopy = requireElement<HTMLElement>("#run-state-copy");
const runSession = requireElement<HTMLElement>("#run-session");
const runTurns = requireElement<HTMLElement>("#run-turns");
const runTools = requireElement<HTMLElement>("#run-tools");
const runElapsed = requireElement<HTMLElement>("#run-elapsed");
const runStop = requireElement<HTMLElement>("#run-stop");
const activityList = requireElement<HTMLElement>("#activity-list");
const activityCount = requireElement<HTMLElement>("#activity-count");
const runtimeLight = requireElement<HTMLElement>("#runtime-light");
const ledgerLight = requireElement<HTMLElement>("#ledger-light");
const toast = requireElement<HTMLElement>("#toast");
const settingsModal = requireElement<HTMLElement>("#settings-modal");
const settingsCloseButton = requireElement<HTMLButtonElement>("#settings-close");
const settingsCancelButton = requireElement<HTMLButtonElement>("#settings-cancel");
const settingsForm = requireElement<HTMLFormElement>("#settings-form");
const settingsProvider = requireElement<HTMLSelectElement>("#settings-provider");
const settingsModel = requireElement<HTMLInputElement>("#settings-model");
const settingsBaseUrl = requireElement<HTMLInputElement>("#settings-base-url");
const settingsMaxTokens = requireElement<HTMLInputElement>("#settings-max-tokens");
const settingsEffort = requireElement<HTMLSelectElement>("#settings-effort");
const settingsApiKey = requireElement<HTMLInputElement>("#settings-api-key");
const settingsKeySource = requireElement<HTMLElement>("#settings-key-source");
const settingsStatus = requireElement<HTMLElement>("#settings-status");
const settingsSave = requireElement<HTMLButtonElement>("#settings-save");
const settingsSaveLabel = requireElement<HTMLElement>("#settings-save-label");
const resumeModal = requireElement<HTMLElement>("#resume-modal");
const resumeCloseButton = requireElement<HTMLButtonElement>("#resume-close");
const resumeList = requireElement<HTMLElement>("#resume-list");
const resumeSummary = requireElement<HTMLElement>("#resume-summary");

let state = loadState();
let streaming = false;
let runStartedAt = 0;
let toastTimer: number | undefined;
let renderFrame: number | undefined;
let pendingAssistantId = "";
let commandMatches: SlashCommand[] = [...SLASH_COMMANDS];
let commandSelection = 0;
let resumeMatches: Thread[] = [];
let resumeSelection = 0;
let commandReceipt: {
  threadId: string;
  title: string;
  body: string;
  error: boolean;
} | null = null;

function createId(): string {
  return crypto.randomUUID();
}

function createThread(): Thread {
  const now = Date.now();
  return {
    id: createId(),
    sessionId: "",
    title: "未命名运行",
    createdAt: now,
    updatedAt: now,
    stopReason: "",
    messages: [],
  };
}

function loadState(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as PersistedState;
      if (parsed.version === 1 && Array.isArray(parsed.threads) && parsed.threads.length) {
        const threads = parsed.threads.slice(0, 24).map((thread) => ({
          ...thread,
          messages: Array.isArray(thread.messages)
            ? thread.messages.map((message) => ({
                ...message,
                thinking: message.thinking ?? "",
                parts: Array.isArray(message.parts) ? message.parts : [],
                error: message.error ?? "",
                stopReason: message.stopReason ?? "",
                streaming: false,
              }))
            : [],
        }));
        const activeThreadId = threads.some(
          (thread) => thread.id === parsed.activeThreadId,
        )
          ? parsed.activeThreadId
          : threads[0].id;
        return { version: 1, activeThreadId, threads };
      }
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
  const thread = createThread();
  return { version: 1, activeThreadId: thread.id, threads: [thread] };
}

function activeThread(): Thread {
  return (
    state.threads.find((thread) => thread.id === state.activeThreadId) ??
    state.threads[0]
  );
}

function persistState(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    showToast("本地会话缓存写入失败");
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderMarkdown(value: string): string {
  const parsed = marked.parse(value) as string;
  return DOMPurify.sanitize(parsed, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target"],
  });
}

function shortSession(sessionId: string): string {
  return sessionId ? sessionId.slice(0, 8) : "PENDING";
}

function threadTitleFor(prompt: string): string {
  const singleLine = prompt.replace(/\s+/g, " ").trim();
  return singleLine.length > 42 ? `${singleLine.slice(0, 42)}…` : singleLine;
}

function relativeTime(timestamp: number): string {
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (Math.abs(seconds) < 60) {
    return formatter.format(seconds, "second");
  }
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) {
    return formatter.format(minutes, "minute");
  }
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) {
    return formatter.format(hours, "hour");
  }
  return formatter.format(Math.round(hours / 24), "day");
}

function lastAssistant(thread: Thread): UiMessage | undefined {
  return [...thread.messages].reverse().find((message) => message.role === "assistant");
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

function renderThreadList(): void {
  const sorted = [...state.threads].sort((a, b) => b.updatedAt - a.updatedAt);
  threadCount.textContent = String(sorted.length);
  threadList.innerHTML = sorted
    .map((thread) => {
      const selected = thread.id === state.activeThreadId;
      const messageCount = thread.messages.filter(
        (message) => message.role === "user",
      ).length;
      const badge = thread.sessionId ? shortSession(thread.sessionId).slice(0, 4) : "NEW";
      return `
        <button
          class="thread-item${selected ? " is-active" : ""}"
          type="button"
          data-thread-id="${escapeHtml(thread.id)}"
          aria-current="${selected ? "true" : "false"}"
        >
          <span class="thread-marker" aria-hidden="true"></span>
          <span class="thread-copy">
            <strong>${escapeHtml(thread.title)}</strong>
            <small>${relativeTime(thread.updatedAt)} · ${messageCount} turns</small>
          </span>
          <span class="thread-badge">${badge}</span>
        </button>
      `;
    })
    .join("");
}

function emptyStateMarkup(): string {
  return `
    <section class="empty-state">
      <header class="empty-boot">
        <span class="eyebrow">SESSION 00 / LOCAL RUNTIME</span>
        <span class="empty-signal"><i></i> READY</span>
      </header>
      <div class="empty-console" aria-label="Agent 已就绪">
        <span class="empty-prefix">agentlearn@local</span>
        <span class="empty-path">~/sandbox</span>
        <span class="empty-caret">$</span>
        <span class="empty-command">awaiting input</span>
        <span class="empty-cursor" aria-hidden="true">_</span>
      </div>
      <p class="empty-copy">
        输入任务，模型请求、工具执行和 trace 会沿同一条执行线展开。
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
        <span>ENTER / RUN</span>
        <span>SHIFT+ENTER / NEWLINE</span>
        <span>JSONL + SQLITE / LOCAL</span>
      </div>
    </section>
  `;
}

function renderTool(tool: ToolEvent): string {
  const args = JSON.stringify(tool.input, null, 2);
  const output =
    tool.state === "running"
      ? `<span class="tool-pending">waiting for result…</span>`
      : `<pre>${escapeHtml(tool.output || "(no output)")}</pre>`;
  const duration =
    tool.elapsedMs === undefined ? "" : `<span>${tool.elapsedMs}ms</span>`;
  return `
    <section class="tool-event ${tool.isError ? "is-error" : ""} ${tool.state === "running" ? "is-running" : ""}">
      <header>
        <span class="tool-label">${tool.isError ? "TOOL ERROR" : "TOOL CALL"}</span>
        <code>${escapeHtml(tool.name)}</code>
        ${duration}
      </header>
      <pre class="tool-input">${escapeHtml(args === "{}" ? "(no arguments)" : args)}</pre>
      ${output}
    </section>
  `;
}

function renderAssistant(message: UiMessage): string {
  const parts = message.parts
    .map((part) => {
      if (part.type === "tool") {
        return renderTool(part.tool);
      }
      if (!part.text) {
        return "";
      }
      return `<div class="markdown">${renderMarkdown(part.text)}</div>`;
    })
    .join("");
  const reasoning = message.thinking
    ? `
      <details class="reasoning-block"${message.streaming ? " open" : ""}>
        <summary>
          <span>REASONING</span>
          <span>${message.thinking.length.toLocaleString()} chars</span>
        </summary>
        <pre>${escapeHtml(message.thinking)}</pre>
      </details>
    `
    : "";
  const waiting =
    message.streaming && !message.text
      ? `
        <div class="thinking-line" aria-label="模型正在生成">
          <span></span><span></span><span></span>
          <em>${message.thinking ? "FORMING RESPONSE" : "WAITING FOR FIRST TOKEN"}</em>
        </div>
      `
      : "";
  const error = message.error
    ? `<div class="message-error"><strong>RUN ERROR</strong><p>${escapeHtml(message.error)}</p></div>`
    : "";
  const stop =
    message.stopReason && !message.streaming
      ? `<span>STOP ${escapeHtml(message.stopReason)}</span>`
      : "";
  return `
    <article class="message assistant-message" data-message-id="${escapeHtml(message.id)}">
      <header class="message-heading">
        <span class="message-avatar">A</span>
        <span>AGENT</span>
        <span class="message-rule"></span>
        <time>${new Date(message.createdAt).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        })}</time>
      </header>
      ${reasoning}
      <div class="assistant-flow">${waiting}${parts}</div>
      ${error}
      <footer class="message-footer">
        ${stop}
        ${message.durationMs !== undefined ? `<span>${message.durationMs}ms</span>` : ""}
      </footer>
    </article>
  `;
}

function renderUser(message: UiMessage): string {
  return `
    <article class="message user-message message-appear" data-message-id="${escapeHtml(message.id)}">
      <header class="user-heading">
        <span>YOU</span>
        <time>${new Date(message.createdAt).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        })}</time>
      </header>
      <p>${escapeHtml(message.text)}</p>
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
        <span>COMMAND / ${escapeHtml(commandReceipt.title)}</span>
        <span>LOCAL ONLY</span>
      </header>
      <pre>${escapeHtml(commandReceipt.body)}</pre>
    </section>
  `;
}

function renderConversation(forceBottom = false): void {
  const thread = activeThread();
  const nearBottom =
    conversationScroll.scrollHeight -
      conversationScroll.scrollTop -
      conversationScroll.clientHeight <
    140;
  if (!thread.messages.length) {
    conversation.innerHTML = emptyStateMarkup() + renderCommandReceipt();
  } else {
    conversation.innerHTML =
      thread.messages
        .map((message) =>
          message.role === "user" ? renderUser(message) : renderAssistant(message),
        )
        .join("") + renderCommandReceipt();
  }
  if (forceBottom || nearBottom) {
    requestAnimationFrame(() => {
      conversationScroll.scrollTop = conversationScroll.scrollHeight;
    });
  }
}

function renderHeader(): void {
  const thread = activeThread();
  threadTitle.textContent = thread.title;
  threadKicker.textContent = thread.sessionId
    ? `SESSION ${shortSession(thread.sessionId)}`
    : "NEW SESSION";
}

function renderRunLedger(): void {
  const thread = activeThread();
  const assistant = lastAssistant(thread);
  const tools = assistantTools(assistant);
  const status = streaming
    ? "STREAMING"
    : assistant?.error
      ? "ERROR"
      : thread.messages.length
        ? "COMPLETE"
        : "READY";
  const statusClass =
    status === "STREAMING"
      ? "is-running"
      : status === "ERROR"
        ? "is-error"
        : "is-ready";
  const statusCopy =
    status === "STREAMING"
      ? "模型请求或工具仍在执行"
      : status === "ERROR"
        ? "上一轮没有正常结束"
        : status === "COMPLETE"
          ? "最近一轮已经结束"
          : "等待第一条消息";
  runState.textContent = status;
  runState.className = statusClass;
  runStateCopy.textContent = statusCopy;
  runSession.textContent = shortSession(thread.sessionId);
  runTurns.textContent = String(
    thread.messages.filter((message) => message.role === "user").length,
  );
  runTools.textContent = String(tools.length);
  runStop.textContent = thread.stopReason || "—";
  if (!streaming) {
    runElapsed.textContent =
      assistant?.durationMs !== undefined ? `${assistant.durationMs}ms` : "—";
  }
  setStatusLight(runtimeLight, statusClass);
  setStatusLight(ledgerLight, statusClass);
  connectionPill.className = `connection-pill ${statusClass}`;
  connectionPill.innerHTML = `<span class="status-light ${statusClass}"></span>${status}`;

  const activity: ActivityItem[] = [];
  if (assistant?.thinking) {
    activity.push({
      key: "reasoning",
      tag: "THINK",
      title: "Reasoning captured",
      detail: `${assistant.thinking.length.toLocaleString()} chars`,
      status: "done",
    });
  }
  for (const tool of [...tools].reverse()) {
    activity.push({
      key: tool.id,
      tag: tool.isError ? "ERR" : tool.state === "running" ? "RUN" : "TOOL",
      title: tool.name,
      detail:
        tool.state === "running"
          ? "executing"
          : `${tool.elapsedMs ?? 0}ms · ${tool.output?.split("\n")[0]?.slice(0, 48) ?? "done"}`,
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

function renderEverything(forceBottom = false): void {
  renderThreadList();
  renderHeader();
  renderConversation(forceBottom);
  renderRunLedger();
  sendButton.disabled = streaming;
  sendLabel.textContent = streaming ? "RUNNING" : "RUN";
  composerStatus.textContent = streaming
    ? "STREAM OPEN · WAITING FOR DONE"
    : "ENTER TO SEND · SHIFT+ENTER FOR NEWLINE";
}

function updateAssistant(message: UiMessage): void {
  pendingAssistantId = message.id;
  if (renderFrame !== undefined) {
    return;
  }
  renderFrame = requestAnimationFrame(() => {
    renderFrame = undefined;
    const thread = activeThread();
    const current = thread.messages.find(
      (candidate) => candidate.id === pendingAssistantId,
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
    } else {
      const wrapper = document.createElement("div");
      wrapper.innerHTML = renderAssistant(current);
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

function getAssistantTool(message: UiMessage, id: string): ToolEvent | undefined {
  return message.parts
    .filter((part): part is ToolPart => part.type === "tool")
    .map((part) => part.tool)
    .find((tool) => tool.id === id);
}

function handleChatEvent(
  event: ChatEvent,
  thread: Thread,
  assistant: UiMessage,
): boolean {
  if (event.kind === "thinking") {
    assistant.thinking += event.text ?? "";
    updateAssistant(assistant);
    return false;
  }
  if (event.kind === "text") {
    appendAssistantText(assistant, event.text ?? "");
    updateAssistant(assistant);
    return false;
  }
  if (event.kind === "tool_use" && event.id) {
    const tool: ToolEvent = {
      id: event.id,
      name: event.name ?? "tool",
      input: event.input ?? {},
      state: "running",
    };
    assistant.parts.push({ type: "tool", tool });
    updateAssistant(assistant);
    return false;
  }
  if (event.kind === "tool_result" && event.id) {
    const tool = getAssistantTool(assistant, event.id);
    if (tool) {
      tool.state = "done";
      tool.output = event.output ?? "";
      tool.isError = Boolean(event.is_error);
      tool.elapsedMs = event.elapsed_ms;
    }
    updateAssistant(assistant);
    return false;
  }
  if (event.kind === "error") {
    assistant.error = event.message ?? "未知错误";
    thread.stopReason = "error";
    return false;
  }
  if (event.kind === "done") {
    thread.sessionId = event.session_id ?? thread.sessionId;
    thread.stopReason = event.stop_reason ?? "—";
    assistant.stopReason = thread.stopReason;
    return true;
  }
  return false;
}

async function consumeSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const payload = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (payload) {
        onEvent(JSON.parse(payload) as ChatEvent);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

async function readError(response: Response): Promise<string> {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail || raw;
  } catch {
    return raw || `HTTP ${response.status}`;
  }
}

function setSettingsStatus(
  message: string,
  tone: "idle" | "busy" | "ok" | "error" = "idle",
): void {
  settingsStatus.textContent = message;
  settingsStatus.className = `settings-status is-${tone}`;
}

function applySettings(snapshot: SettingsSnapshot): void {
  settingsProvider.value = snapshot.provider;
  settingsModel.value = snapshot.model_id;
  settingsBaseUrl.value = snapshot.base_url;
  settingsMaxTokens.value = String(snapshot.max_tokens);
  settingsEffort.value = snapshot.effort;
  settingsApiKey.value = "";
  settingsApiKey.placeholder = snapshot.api_key_configured
    ? "留空保持当前密钥"
    : "输入 API Key，或写 ${ENV_VAR}";
  settingsKeySource.textContent = `当前密钥：${snapshot.api_key_source} · 留空不改`;
}

function setSettingsBusy(busy: boolean): void {
  settingsSave.disabled = busy;
  settingsProvider.disabled = busy;
  settingsModel.disabled = busy;
  settingsBaseUrl.disabled = busy;
  settingsMaxTokens.disabled = busy;
  settingsEffort.disabled = busy;
  settingsApiKey.disabled = busy;
  settingsSaveLabel.textContent = busy ? "同步中…" : "保存并应用";
}

async function loadSettings(): Promise<void> {
  setSettingsBusy(true);
  setSettingsStatus("正在读取本机配置…", "busy");
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    setSettingsStatus("配置已读取，修改后保存即可生效。", "ok");
    settingsModel.focus();
    settingsModel.select();
  } catch (error) {
    const message =
      error instanceof TypeError
        ? "连不上本地后端。先在 server/ 运行：uv run python main.py desktop"
        : error instanceof Error
          ? error.message
          : String(error);
    setSettingsStatus(message, "error");
    settingsProvider.focus();
  } finally {
    setSettingsBusy(false);
  }
}

function openSettings(): void {
  settingsModal.hidden = false;
  settingsForm.reset();
  applySettings({
    provider: "anthropic",
    base_url: "",
    model_id: "",
    max_tokens: 64000,
    effort: "",
    api_key_configured: false,
    api_key_source: "正在读取",
  });
  void loadSettings();
}

function closeSettings(): void {
  settingsModal.hidden = true;
  promptInput.focus();
}

async function saveSettings(): Promise<void> {
  const maxTokens = Number.parseInt(settingsMaxTokens.value, 10);
  if (!Number.isFinite(maxTokens) || maxTokens <= 0) {
    setSettingsStatus("MAX TOKENS 必须是正整数。", "error");
    settingsMaxTokens.focus();
    return;
  }
  const apiKey = settingsApiKey.value.trim();
  const payload: SettingsPayload = {
    provider: settingsProvider.value,
    base_url: settingsBaseUrl.value.trim(),
    model_id: settingsModel.value.trim(),
    max_tokens: maxTokens,
    effort: settingsEffort.value,
    api_key: apiKey || null,
  };
  setSettingsBusy(true);
  setSettingsStatus("正在写入 config.ini 并校验…", "busy");
  try {
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    setSettingsStatus("已保存；下一次模型请求使用新配置。", "ok");
    showToast("模型设置已保存，下一轮生效");
  } catch (error) {
    setSettingsStatus(
      error instanceof Error ? error.message : String(error),
      "error",
    );
  } finally {
    setSettingsBusy(false);
  }
}

async function submitPrompt(rawPrompt: string): Promise<void> {
  const question = rawPrompt.trim();
  if (!question) {
    return;
  }
  const slashCommand = parseSlashCommand(question);
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
  if (streaming) {
    showToast("当前轮还没结束，先等 done 事件");
    return;
  }

  commandReceipt = null;
  const thread = activeThread();
  const assistant: UiMessage = {
    id: createId(),
    role: "assistant",
    text: "",
    thinking: "",
    parts: [],
    error: "",
    stopReason: "",
    createdAt: Date.now(),
    streaming: true,
  };
  thread.messages.push({
    id: createId(),
    role: "user",
    text: question,
    thinking: "",
    parts: [],
    error: "",
    stopReason: "",
    createdAt: Date.now(),
  });
  thread.messages.push(assistant);
  if (thread.title === "未命名运行") {
    thread.title = threadTitleFor(question);
  }
  thread.updatedAt = Date.now();
  streaming = true;
  runStartedAt = Date.now();
  promptInput.value = "";
  resizePrompt();
  renderEverything(true);
  schedulePersist();

  let gotDone = false;
  let receivedError = false;
  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        session_id: thread.sessionId,
      }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    if (!response.body) {
      throw new Error("浏览器没有拿到 SSE body");
    }
    await consumeSse(response.body, (event) => {
      if (event.kind === "error") {
        receivedError = true;
        showToast(event.message ?? "本轮执行出错");
      }
      gotDone = handleChatEvent(event, thread, assistant) || gotDone;
    });
    if (!gotDone && !receivedError) {
      throw new Error("SSE 连接结束但没有收到 done 事件");
    }
  } catch (error) {
    const message =
      error instanceof TypeError
        ? "连不上本地后端。先在 server/ 运行：uv run python main.py desktop"
        : error instanceof Error
          ? error.message
          : String(error);
    assistant.error = message;
    thread.stopReason = "error";
    showToast(message);
  } finally {
    assistant.streaming = false;
    assistant.durationMs = Date.now() - runStartedAt;
    streaming = false;
    thread.updatedAt = Date.now();
    persistState();
    renderEverything(true);
    promptInput.focus();
  }
}

function newThread(): boolean {
  if (streaming) {
    showToast("当前轮结束后再新建会话");
    return false;
  }
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

function switchThread(threadId: string): void {
  if (streaming && threadId !== state.activeThreadId) {
    showToast("当前轮结束后再切换会话");
    return;
  }
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
}

function findResumeThread(argument: string): Thread | null {
  const query = argument.trim().toLowerCase();
  if (!query) {
    return null;
  }
  const exact = state.threads.filter(
    (thread) =>
      thread.id.toLowerCase() === query ||
      thread.sessionId.toLowerCase() === query,
  );
  const candidates =
    exact.length > 0
      ? exact
      : state.threads.filter(
          (thread) =>
            thread.id.toLowerCase().startsWith(query) ||
            thread.sessionId.toLowerCase().startsWith(query),
        );
  if (candidates.length === 1) {
    return candidates[0];
  }
  showToast(
    candidates.length > 1
      ? "匹配到多个会话，请多输入几位 id"
      : `没有会话 id 以 ${argument} 开头`,
  );
  return null;
}

function renderResumeList(): void {
  const currentId = state.activeThreadId;
  resumeSummary.textContent = `${resumeMatches.length} 个本地会话 · 当前 ${shortSession(
    activeThread().sessionId,
  )}`;
  resumeList.innerHTML = resumeMatches
    .map((thread, index) => {
      const turns = thread.messages.filter(
        (message) => message.role === "user",
      ).length;
      const current = thread.id === currentId;
      return `
        <button
          class="resume-item${index === resumeSelection ? " is-selected" : ""}${current ? " is-current" : ""}"
          type="button"
          role="option"
          aria-selected="${index === resumeSelection}"
          data-resume-index="${index}"
        >
          <span class="resume-item-time">${relativeTime(thread.updatedAt)}</span>
          <span class="resume-item-id">${escapeHtml(
            thread.sessionId ? shortSession(thread.sessionId) : "PENDING",
          )}</span>
          <span class="resume-item-turns">${turns} TURNS</span>
          ${current ? '<span class="resume-current">CURRENT</span>' : ""}
          <strong>${escapeHtml(thread.title)}</strong>
          <small>${escapeHtml(thread.stopReason || "no completed run")}</small>
        </button>
      `;
    })
    .join("");
}

function openResume(): void {
  if (streaming) {
    showToast("当前轮结束后再切换会话");
    return;
  }
  resumeMatches = [...state.threads].sort((a, b) => b.updatedAt - a.updatedAt);
  const alternate = resumeMatches.findIndex(
    (thread) => thread.id !== state.activeThreadId,
  );
  resumeSelection = alternate >= 0 ? alternate : 0;
  renderResumeList();
  resumeModal.hidden = false;
  promptInput.blur();
  resumeList
    .querySelector<HTMLButtonElement>(".resume-item.is-selected")
    ?.focus();
}

function closeResume(): void {
  resumeModal.hidden = true;
}

function moveResumeSelection(delta: number): void {
  if (resumeModal.hidden || !resumeMatches.length) {
    return;
  }
  resumeSelection =
    (resumeSelection + delta + resumeMatches.length) % resumeMatches.length;
  renderResumeList();
  resumeList
    .querySelector<HTMLButtonElement>(".resume-item.is-selected")
    ?.focus();
}

function selectResume(index: number): void {
  const thread = resumeMatches[index];
  if (!thread) {
    return;
  }
  switchThread(thread.id);
  if (!streaming) {
    showToast(`已续接：${thread.title}`);
  }
}

function resizePrompt(): void {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 160)}px`;
}

function showToast(message: string): void {
  toast.textContent = message;
  toast.classList.add("is-visible");
  if (toastTimer !== undefined) {
    window.clearTimeout(toastTimer);
  }
  toastTimer = window.setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 3800);
}

function closeCommandPalette(): void {
  commandPalette.hidden = true;
}

function renderCommandPalette(): void {
  let group = "";
  commandList.innerHTML = commandMatches
    .map(
      (command, index) => {
        const heading =
          command.group !== group
            ? `<div class="command-group">${escapeHtml(command.group)}</div>`
            : "";
        group = command.group;
        return `${heading}
          <button
            class="command-option${index === commandSelection ? " is-selected" : ""}"
            type="button"
            role="option"
            aria-selected="${index === commandSelection}"
            data-command-index="${index}"
          >
            <code>${command.usage}</code>
            <span>${command.description}</span>
          </button>
        `;
      },
    )
    .join("");
}

function updateCommandPalette(): void {
  const text = promptInput.value.trim();
  if (!text.startsWith("/") || /\s/.test(text)) {
    closeCommandPalette();
    return;
  }
  const prefix = text.slice(1).toLowerCase();
  commandMatches = SLASH_COMMANDS.filter((command) =>
    command.name.startsWith(prefix),
  );
  if (!commandMatches.length) {
    closeCommandPalette();
    return;
  }
  commandSelection = Math.min(commandSelection, commandMatches.length - 1);
  renderCommandPalette();
  commandPalette.hidden = false;
}

function moveCommandSelection(delta: number): void {
  if (commandPalette.hidden || !commandMatches.length) {
    return;
  }
  commandSelection =
    (commandSelection + delta + commandMatches.length) % commandMatches.length;
  renderCommandPalette();
}

function completeSelectedCommand(): void {
  const command = commandMatches[commandSelection];
  if (!command) {
    return;
  }
  promptInput.value = `/${command.name}${command.takesArgument ? " " : ""}`;
  resizePrompt();
  closeCommandPalette();
  promptInput.focus();
  promptInput.setSelectionRange(promptInput.value.length, promptInput.value.length);
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

async function quickSetModel(modelId: string): Promise<void> {
  try {
    const currentResponse = await fetch(`${API_BASE}/settings`);
    if (!currentResponse.ok) {
      throw new Error(await readError(currentResponse));
    }
    const current = (await currentResponse.json()) as SettingsSnapshot;
    const payload: SettingsPayload = {
      provider: current.provider,
      base_url: current.base_url,
      model_id: modelId,
      max_tokens: current.max_tokens,
      effort: current.effort,
      api_key: null,
    };
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    showToast(`模型已切换：${snapshot.model_id}，下一轮生效`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error));
  }
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
      model = ((await response.json()) as SettingsSnapshot).model_id;
    }
  } catch {
    model = "unavailable · backend offline";
  } finally {
    window.clearTimeout(timeout);
  }
  const assistant = lastAssistant(thread);
  const tools = assistantTools(assistant);
  const stateLabel = streaming
    ? "STREAMING"
    : assistant?.error
      ? "ERROR"
      : thread.messages.length
        ? "COMPLETE"
        : "READY";
  showCommandResult(
    "STATUS",
    [
      `session   ${thread.sessionId || "pending"}`,
      `model     ${model}`,
      `state     ${stateLabel}`,
      `turns     ${thread.messages.filter((message) => message.role === "user").length}`,
      `tools     ${tools.length}`,
      `stop      ${thread.stopReason || "—"}`,
      "storage   JSONL + SQLite on server · UI index in localStorage",
    ].join("\n"),
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
    commandSelection = 0;
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
    if (streaming) {
      showToast("当前轮结束后再改设置");
      return;
    }
    await quickSetModel(argument);
    return;
  }
  if (command.name === "status") {
    await showStatus();
    return;
  }
  if (streaming) {
    showToast("当前轮还没结束，先等 done 事件");
    return;
  }
  if (command.name === "resume") {
    if (argument) {
      const thread = findResumeThread(argument);
      if (thread) {
        switchThread(thread.id);
        showToast(`已续接：${thread.title}`);
      }
      return;
    }
    openResume();
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

newThreadButton.addEventListener("click", newThread);
railCloseButton.addEventListener("click", closeThreadRail);
railOpenButton.addEventListener("click", openThreadRail);
scrim.addEventListener("click", closeThreadRail);
settingsCloseButton.addEventListener("click", closeSettings);
settingsCancelButton.addEventListener("click", closeSettings);
settingsModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-settings-close]")) {
    closeSettings();
  }
});
resumeCloseButton.addEventListener("click", closeResume);
resumeModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-resume-close]")) {
    closeResume();
  }
});
settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveSettings();
});
runRailToggle.addEventListener("click", () => {
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
conversation.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-prompt]",
  );
  if (button?.dataset.prompt) {
    void submitPrompt(button.dataset.prompt);
  }
});
commandList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-command-index]",
  );
  if (button?.dataset.commandIndex === undefined) {
    return;
  }
  commandSelection = Number(button.dataset.commandIndex);
  completeSelectedCommand();
});
resumeList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-resume-index]",
  );
  if (button?.dataset.resumeIndex === undefined) {
    return;
  }
  selectResume(Number(button.dataset.resumeIndex));
});
composer.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitPrompt(promptInput.value);
});
promptInput.addEventListener("keydown", (event) => {
  const paletteVisible = !commandPalette.hidden;
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
    completeSelectedCommand();
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
    if (paletteVisible) {
      const parsed = parseSlashCommand(promptInput.value.trim());
      if (!parsed?.command) {
        completeSelectedCommand();
        return;
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
  const resumeVisible = !resumeModal.hidden;
  if (resumeVisible) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveResumeSelection(1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveResumeSelection(-1);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeResume();
      promptInput.focus();
      return;
    }
  }
  const settingsVisible = !settingsModal.hidden;
  if (settingsVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSettings();
    }
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
    closeThreadRail();
    promptInput.blur();
  }
});
window.setInterval(() => {
  if (streaming) {
    runElapsed.textContent = `${Date.now() - runStartedAt}ms`;
  }
}, 100);

renderEverything();
resizePrompt();
