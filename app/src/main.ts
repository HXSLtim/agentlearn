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

type ApprovalMode = "approve" | "auto" | "yolo";
type SteeringMode = "queue" | "cooperative" | "preemptive";
type ToolState = "waiting" | "running" | "done";
type TranscriptMode = "normal" | "thinking" | "verbose";

interface PlanEvent {
  id: string;
  threadId: string;
  sessionId: string;
  text: string;
  confirmed: boolean;
  discarded: boolean;
  executed: boolean;
}

interface ImageAttachment {
  media_type: string;
  data: string;
}

interface ToolEvent {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  image?: ImageAttachment;
  hasImage?: boolean;
  isError?: boolean;
  elapsedMs?: number;
  approvalId?: string;
  state: ToolState;
}

interface TextPart {
  type: "text";
  text: string;
}

interface ThinkingPart {
  type: "thinking";
  text: string;
}

interface ToolPart {
  type: "tool";
  tool: ToolEvent;
}

interface PlanPart {
  type: "plan";
  plan: PlanEvent;
}

type AssistantPart = TextPart | ThinkingPart | ToolPart | PlanPart;

interface UiMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  images?: ImageAttachment[];
  thinking: string;
  parts: AssistantPart[];
  error: string;
  stopReason: string;
  durationMs?: number;
  createdAt: number;
  streaming?: boolean;
  queued?: boolean;
  steeringState?: "queued" | "acknowledged" | "delivered" | "failed";
}

interface TurnUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_ratio: number;
}

interface ContextWarning {
  estimate_tokens: number;
  budget_tokens: number;
  ratio: number;
}

interface Thread {
  id: string;
  sessionId: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  stopReason: string;
  approvalMode: ApprovalMode;
  steeringMode: SteeringMode;
  model: string;
  pinned: boolean;
  archived: boolean;
  usage?: TurnUsage;
  contextWarning?: ContextWarning;
  serverTurns?: number;
  runStartedAt?: number;
  stopping?: boolean;
  messagesLoaded?: boolean;
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
  pricing: {
    currency: string;
    input_per_million: string;
    cache_read_per_million: string;
    cache_write_per_million: string;
    output_per_million: string;
  } | null;
}

interface SettingsPayload {
  provider: string;
  base_url: string;
  model_id: string;
  max_tokens: number;
  effort: string;
  api_key: string | null;
}

interface ModelListResponse {
  provider: "anthropic" | "openai";
  models: string[];
}

interface CapabilitiesResponse {
  tools: Array<{
    name: string;
    source: string;
    description: string;
  }>;
  mcp: Array<{
    alias: string;
    state: string;
    command?: string;
  }>;
  skills: Array<{ name: string; description: string }>;
  environment: {
    cwd: string;
    sandbox: string;
    sandbox_boundary: string;
    memory_file: string;
  };
  runtime: {
    python: string;
    provider: string;
    model: string;
    effort: string;
    triggers: number;
  };
}

type ApprovalScope = "once" | "session";

interface ChatEvent {
  kind:
    | "thinking"
    | "text"
    | "tool_use"
    | "tool_approval"
    | "tool_result"
    | "plan"
    | "usage"
    | "context_warning"
    | "steering_ack"
    | "steering_injected"
    | "steering_cancelled"
    | "done"
    | "error";
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  denied?: boolean;
  elapsed_ms?: number;
  stop_reason?: string;
  session_id?: string;
  message?: string;
  approval_id?: string;
  mode?: ApprovalMode;
  has_image?: boolean;
  usage?: TurnUsage;
  estimate_tokens?: number;
  budget_tokens?: number;
  ratio?: number;
  plan_id?: string;
}

interface ApprovalModeResponse {
  ok: boolean;
  active: boolean;
  mode: ApprovalMode;
  auto_approved: string[];
}

interface PlanConfirmResponse {
  ok: boolean;
  plan_id: string;
  confirmed: boolean;
}

interface ServerSession {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  turns: number;
  stop_reason: string;
  pinned: boolean;
  archived: boolean;
}

interface SessionForkResponse {
  ok: boolean;
  session_id: string;
  copied: number;
}

interface SessionSearchHit {
  session_id: string;
  title: string;
  message_index: number;
  role: string;
  snippet: string;
}

interface SessionSearchResponse {
  q: string;
  hits: SessionSearchHit[];
}

interface ConversationSearchMatch {
  messageId: string;
  role: UiMessage["role"];
  snippet: string;
}

interface ServerContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result" | "image";
  text?: string;
  thinking?: string;
  id?: string;
  tool_use_id?: string;
  name?: string;
  input?: Record<string, unknown>;
  content?: string;
  is_error?: boolean;
  media_type?: string;
  data?: string;
  image?: ImageAttachment | null;
}

interface ServerMessage {
  role: "user" | "assistant";
  content: ServerContentBlock[];
}

interface TraceSpan {
  span_id: string;
  parent_id: string | null;
  name: string;
  start_time: string;
  end_time: string | null;
  status: "running" | "ok" | "error" | "cancelled";
  data: Record<string, unknown>;
  duration_ms: number | null;
}

interface TraceResponse {
  spans: TraceSpan[];
  answers: Record<string, string>;
}

interface WorkspaceDiffFile {
  path: string;
  old_path: string | null;
  status: string;
  binary: boolean;
  truncated: boolean;
  hunks: WorkspaceDiffHunk[];
  additions: number;
  deletions: number;
}

interface WorkspaceDiffHunk {
  header: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: WorkspaceDiffLine[];
}

interface WorkspaceDiffLine {
  kind: "context" | "add" | "delete" | "meta";
  old_line: number | null;
  new_line: number | null;
  text: string;
}

interface WorkspaceDiffResponse {
  root: string;
  files: WorkspaceDiffFile[];
  untracked_count: number;
  include_untracked: boolean;
}

interface WorkspacePathsResponse {
  root: string;
  prefix: string;
  paths: string[];
  total: number;
  limited: boolean;
}

interface StopResponse {
  ok: boolean;
  active: boolean;
  cancelled: boolean;
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
    name: "memory",
    usage: "/memory [内容]",
    description: "往跨会话记忆记一条；不带参数查看",
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "find",
    usage: "/find [关键词]",
    description: "定位当前会话里的一条已渲染消息",
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "status",
    usage: "/status",
    description: "查看当前会话与运行状态",
    group: "RUNTIME",
  },
  {
    name: "trace",
    usage: "/trace",
    description: "打开当前会话的 span 树与回答配对",
    group: "RUNTIME",
  },
  {
    name: "stop",
    usage: "/stop",
    description: "在下一个检查点停止当前运行轮",
    group: "RUNTIME",
  },
  {
    name: "retry",
    usage: "/retry",
    description: "重跑上一条失败的请求，不新增用户消息卡片",
    group: "RUNTIME",
  },
  {
    name: "steering",
    usage: "/steering [mode]",
    description: "切换 queue / cooperative / preemptive",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "effort",
    usage: "/effort [level]",
    description: "切换思考等级；不带参数循环",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "approval",
    usage: "/approval [mode]",
    description: "切换 approve / auto / yolo",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "plan",
    usage: "/plan <task>",
    description: "Read-only exploration, then a human-confirmed plan",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "capabilities",
    usage: "/capabilities",
    description: "Show tools, MCP health, runtime and sandbox facts",
    group: "SYSTEM",
  },
  {
    name: "transcript",
    usage: "/transcript [mode]",
    description: "切换 normal / thinking / verbose 三档正文视图",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "copy",
    usage: "/copy",
    description: "复制最近一条完整回答",
    group: "SYSTEM",
  },
  {
    name: "export",
    usage: "/export",
    description: "把当前会话导出为 Markdown",
    group: "SYSTEM",
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
const traceToggle = requireElement<HTMLButtonElement>("#trace-toggle");
const runRailToggle = requireElement<HTMLButtonElement>("#run-rail-toggle");
const runRail = requireElement<HTMLElement>("#run-rail");
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
const stopButton = requireElement<HTMLButtonElement>("#stop");
const composerShell = requireElement<HTMLElement>("#composer-shell");
const composerAttachments = requireElement<HTMLElement>("#composer-attachments");
const commandPalette = requireElement<HTMLElement>("#command-palette");
const commandList = requireElement<HTMLElement>("#command-list");
const commandPaletteTitle = requireElement<HTMLElement>("#command-palette-title");
const commandPaletteEnterAction = requireElement<HTMLElement>(
  "#command-palette-enter-action",
);
const runState = requireElement<HTMLElement>("#run-state");
const runStateCopy = requireElement<HTMLElement>("#run-state-copy");
const runSession = requireElement<HTMLElement>("#run-session");
const runTurns = requireElement<HTMLElement>("#run-turns");
const runTools = requireElement<HTMLElement>("#run-tools");
const runElapsed = requireElement<HTMLElement>("#run-elapsed");
const runInputTokens = requireElement<HTMLElement>("#run-input-tokens");
const runOutputTokens = requireElement<HTMLElement>("#run-output-tokens");
const runCacheRead = requireElement<HTMLElement>("#run-cache-read");
const runCacheWrite = requireElement<HTMLElement>("#run-cache-write");
const runCacheHit = requireElement<HTMLElement>("#run-cache-hit");
const runCost = requireElement<HTMLElement>("#run-cost");
const runEffort = requireElement<HTMLElement>("#run-effort");
const runApproval = requireElement<HTMLElement>("#run-approval");
const runSteering = requireElement<HTMLElement>("#run-steering");
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
const settingsModel = requireElement<HTMLSelectElement>("#settings-model");
const settingsModelCount = requireElement<HTMLElement>(
  "#settings-model-count",
);
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
const resumeSearch = requireElement<HTMLInputElement>("#resume-search");
const resumeRenameButton = requireElement<HTMLButtonElement>("#resume-rename");
const resumePinButton = requireElement<HTMLButtonElement>("#resume-pin");
const resumeActiveViewButton = requireElement<HTMLButtonElement>(
  "#resume-active-view",
);
const resumeArchiveViewButton = requireElement<HTMLButtonElement>(
  "#resume-archive-view",
);
const resumeArchiveButton = requireElement<HTMLButtonElement>("#resume-archive");
const resumeDeleteButton = requireElement<HTMLButtonElement>("#resume-delete");
const resumeDeleteConfirm = requireElement<HTMLElement>(
  "#resume-delete-confirm",
);
const resumeDeleteTitle = requireElement<HTMLElement>("#resume-delete-title");
const resumeDeleteCancelButton = requireElement<HTMLButtonElement>(
  "#resume-delete-cancel",
);
const resumeDeleteConfirmButton = requireElement<HTMLButtonElement>(
  "#resume-delete-confirm-button",
);
const resumeForkButton = requireElement<HTMLButtonElement>("#resume-fork");
const resumeForkForm = requireElement<HTMLFormElement>("#resume-fork-form");
const resumeForkSource = requireElement<HTMLElement>("#resume-fork-source");
const resumeForkMax = requireElement<HTMLElement>("#resume-fork-max");
const resumeForkInput = requireElement<HTMLInputElement>(
  "#resume-fork-through",
);
const resumeForkPreview = requireElement<HTMLElement>(
  "#resume-fork-preview",
);
const resumeForkCancelButton = requireElement<HTMLButtonElement>(
  "#resume-fork-cancel",
);
const resumeForkSubmitButton = requireElement<HTMLButtonElement>(
  "#resume-fork-submit",
);
const resumeRenameForm = requireElement<HTMLFormElement>("#resume-rename-form");
const resumeRenameInput = requireElement<HTMLInputElement>("#resume-rename-input");
const resumeRenameCancel = requireElement<HTMLButtonElement>("#resume-rename-cancel");
const conversationFindModal = requireElement<HTMLElement>(
  "#conversation-find-modal",
);
const conversationFindCloseButton = requireElement<HTMLButtonElement>(
  "#conversation-find-close",
);
const conversationFindInput = requireElement<HTMLInputElement>(
  "#conversation-find-input",
);
const conversationFindList = requireElement<HTMLElement>(
  "#conversation-find-list",
);
const conversationFindCount = requireElement<HTMLElement>(
  "#conversation-find-count",
);
const traceModal = requireElement<HTMLElement>("#trace-modal");
const traceCloseButton = requireElement<HTMLButtonElement>("#trace-close");
const traceRefreshButton = requireElement<HTMLButtonElement>("#trace-refresh");
const traceSummary = requireElement<HTMLElement>("#trace-summary");
const traceTree = requireElement<HTMLElement>("#trace-tree");
const traceCount = requireElement<HTMLElement>("#trace-count");
const traceDetail = requireElement<HTMLElement>("#trace-detail");
const traceSelectedState = requireElement<HTMLElement>("#trace-selected-state");
const workspaceDiffToggle = requireElement<HTMLButtonElement>("#workspace-diff-toggle");
const workspaceDiffModal = requireElement<HTMLElement>("#workspace-diff-modal");
const workspaceDiffCloseButton = requireElement<HTMLButtonElement>("#workspace-diff-close");
const workspaceDiffRefreshButton = requireElement<HTMLButtonElement>("#workspace-diff-refresh");
const workspaceDiffSummary = requireElement<HTMLElement>("#workspace-diff-summary");
const workspaceDiffList = requireElement<HTMLElement>("#workspace-diff-list");

let state = loadState();
let currentEffort: SettingsSnapshot["effort"] = "";
let currentPricing: SettingsSnapshot["pricing"] = null;
let currentModelOptions: string[] = [];
let approvalModeSync: Promise<void> = Promise.resolve();
const autoApprovedApprovalIds = new Set<string>();
let toastTimer: number | undefined;
let renderFrame: number | undefined;
let renderTarget: { threadId: string; messageId: string } | null = null;
let commandMatches: SlashCommand[] = [...SLASH_COMMANDS];
let commandSelection = 0;
type PaletteMode = "command" | "file";
let paletteMode: PaletteMode = "command";
let pendingImages: ImageAttachment[] = [];
let fileMatches: string[] = [];
let fileSelection = 0;
let fileCompletionTimer: number | undefined;
let fileCompletionSequence = 0;
let resumeMatches: Thread[] = [];
let resumeSelection = 0;
let resumeQuery = "";
let resumeArchived = false;
let contentHits = new Map<string, SessionSearchHit>();
let contentSearchSequence = 0;
let renameThreadId = "";
let deleteThreadId = "";
let forkThreadId = "";
let forkMessages: ServerMessage[] = [];
let conversationFindMatches: ConversationSearchMatch[] = [];
let conversationFindSelection = 0;
let conversationFindThreadId = "";
let conversationFindMessageId = "";
let traceSpans: TraceSpan[] = [];
let traceAnswers: Record<string, string> = {};
let traceSelectedSpanId = "";
let traceSessionId = "";
let traceLoadSequence = 0;
let workspaceDiff: WorkspaceDiffResponse | null = null;
let workspaceDiffLoadSequence = 0;
let transcriptMode: TranscriptMode = "normal";
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
    approvalMode: "approve",
    steeringMode: "queue",
    model: "",
    pinned: false,
    archived: false,
    messagesLoaded: true,
    messages: [],
  };
}

const IMAGE_MEDIA_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/webp",
  "image/gif",
]);

function parseImage(
  image: Pick<Partial<ImageAttachment>, "media_type" | "data"> | null | undefined,
): ImageAttachment | undefined {
  const mediaType = image?.media_type ?? "";
  const data = image?.data ?? "";
  if (
    !IMAGE_MEDIA_TYPES.has(mediaType) ||
    !data ||
    data.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(data)
  ) {
    return undefined;
  }
  return { media_type: mediaType, data };
}

function renderPendingImages(): void {
  pendingImages = pendingImages
    .map(parseImage)
    .filter((image): image is ImageAttachment => Boolean(image));
  composerAttachments.hidden = !pendingImages.length;
  composerAttachments.innerHTML = pendingImages
    .map(
      (image, index) => `
        <figure class="composer-attachment">
          <img src="data:${image.media_type};base64,${image.data}" alt="待发送图片 ${index + 1}" />
          <button type="button" data-remove-image="${index}">REMOVE</button>
        </figure>
      `,
    )
    .join("");
}

async function imageFileToAttachment(file: File): Promise<ImageAttachment | undefined> {
  if (!IMAGE_MEDIA_TYPES.has(file.type)) {
    return undefined;
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(typeof reader.result === "string" ? reader.result : "");
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error(`读取 ${file.name} 失败`));
    };
    reader.readAsDataURL(file);
  });
  const prefix = `data:${file.type};base64,`;
  if (!dataUrl.startsWith(prefix)) {
    return undefined;
  }
  return parseImage({
    media_type: file.type,
    data: dataUrl.slice(prefix.length),
  });
}

async function addImageFiles(
  files: FileList | File[] | undefined,
): Promise<void> {
  const images = await Promise.all(
    Array.from(files ?? []).map((file) => imageFileToAttachment(file)),
  );
  const valid = images.filter((image): image is ImageAttachment => Boolean(image));
  if (!valid.length) {
    showToast("没有可发送的图片附件");
    return;
  }
  pendingImages = [...pendingImages, ...valid];
  renderPendingImages();
  promptInput.focus();
  showToast(`已添加 ${valid.length} 张图片 · 发送时使用 queue`);
}

function normalizeStoredMessage(message: UiMessage): UiMessage {
  const parts: AssistantPart[] = Array.isArray(message.parts)
    ? message.parts
    : [];
  const images = (message.images ?? [])
    .map(parseImage)
    .filter((image): image is ImageAttachment => Boolean(image));
  if (
    message.thinking &&
    !parts.some((part) => part.type === "thinking")
  ) {
    parts.unshift({ type: "thinking", text: message.thinking });
  }
  return {
    ...message,
    images,
    thinking: message.thinking ?? "",
    parts,
    error: message.error ?? "",
    stopReason: message.stopReason ?? "",
    streaming: false,
    queued: false,
    steeringState:
      message.steeringState === "delivered"
        ? "delivered"
        : message.steeringState
          ? "failed"
          : undefined,
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
            ? thread.messages.map(normalizeStoredMessage)
            : [],
          approvalMode:
            thread.approvalMode === "auto" || thread.approvalMode === "yolo"
              ? (thread.approvalMode as ApprovalMode)
              : "approve",
          steeringMode:
            thread.steeringMode === "cooperative" ||
            thread.steeringMode === "preemptive"
              ? (thread.steeringMode as SteeringMode)
              : "queue",
          model: typeof thread.model === "string" ? thread.model : "",
          pinned: thread.pinned === true,
          archived: thread.archived === true,
          serverTurns:
            typeof thread.serverTurns === "number"
              ? thread.serverTurns
              : undefined,
          runStartedAt: undefined,
          stopping: undefined,
          messagesLoaded: Array.isArray(thread.messages)
            ? thread.messages.length > 0
            : false,
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
    const createdAt = baseTime + index;
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
    thread.title = session.title || thread.title || "未命名运行";
    thread.model = session.model || thread.model;
    thread.pinned = session.pinned === true;
    thread.archived = session.archived === true;
    thread.serverTurns = session.turns;
    thread.stopReason = session.stop_reason || thread.stopReason;
    thread.createdAt = parseServerTime(session.created_at);
    thread.updatedAt = parseServerTime(session.updated_at);
  }
}

async function fetchServerSessions(
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

async function hydrateThread(thread: Thread, force = false): Promise<void> {
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

function effortLabel(effort: SettingsSnapshot["effort"]): string {
  return effort || "default (omit)";
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

function runningAssistant(thread: Thread): UiMessage | undefined {
  const running = [...thread.messages]
    .reverse()
    .filter((message) => message.role === "assistant" && message.streaming);
  return running.find((message) => !message.queued) ?? running[0];
}

function isThreadRunning(thread: Thread): boolean {
  return runningAssistant(thread) !== undefined;
}

function threadStatus(thread: Thread): string {
  const active = runningAssistant(thread);
  if (active) {
    if (thread.stopping) {
      return "STOPPING";
    }
    return assistantTools(active).some((tool) => tool.state === "waiting")
      ? "APPROVAL"
      : "RUNNING";
  }
  const assistant = lastAssistant(thread);
  if (assistant?.stopReason === "cancelled" || thread.stopReason === "cancelled") {
    return "CANCELLED";
  }
  if (assistant?.error || thread.stopReason === "error") {
    return "ERROR";
  }
  return thread.messages.length ? "DONE" : "READY";
}

function threadStatusClass(status: string): "is-ready" | "is-running" | "is-error" {
  if (status === "RUNNING" || status === "APPROVAL" || status === "STOPPING") {
    return "is-running";
  }
  if (status === "ERROR") {
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

function renderThreadList(): void {
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
      const badge = thread.sessionId ? shortSession(thread.sessionId).slice(0, 4) : "NEW";
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
            <small><b class="thread-state ${statusClass}">${status}</b> · ${thread.pinned ? "PIN · " : ""}${relativeTime(thread.updatedAt)} · ${messageCount} turns</small>
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

function renderTool(tool: ToolEvent, detailed: boolean): string {
  const args = JSON.stringify(tool.input, null, 2);
  const showInput = detailed || tool.state === "waiting";
  const toolImage = tool.image
    ? renderImages([tool.image], "工具结果图片")
    : tool.hasImage
      ? `<div class="tool-image-pending">IMAGE ATTACHED · RELOADING FROM TRANSCRIPT</div>`
      : "";
  const output =
    tool.state === "waiting"
      ? `
        <div class="tool-approval-gate">
          <span>HOST EXECUTION REQUIRES APPROVAL</span>
          <input
            type="text"
            class="approval-reason"
            data-approval-id="${escapeHtml(tool.approvalId ?? "")}"
            placeholder="DENIAL REASON (OPTIONAL)"
            aria-label="拒绝理由（可选）"
          />
          <div>
            <button type="button" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="false" data-approval-scope="once">DENY</button>
            <button type="button" class="approve" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="true" data-approval-scope="once">ACCEPT ONCE</button>
            <button type="button" class="approve" data-approval-id="${escapeHtml(tool.approvalId ?? "")}" data-approved="true" data-approval-scope="session">ACCEPT SESSION</button>
          </div>
        </div>
      `
      : tool.state === "running"
        ? `<span class="tool-pending">waiting for result…</span>`
        : `<pre>${escapeHtml(tool.output || "(no output)")}</pre>${toolImage}`;
  const duration =
    tool.elapsedMs === undefined ? "" : `<span>${tool.elapsedMs}ms</span>`;
  const label =
    tool.state === "waiting"
      ? "TOOL APPROVAL"
      : tool.isError
        ? "TOOL ERROR"
        : "TOOL CALL";
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
      ${showInput ? `<pre class="tool-input">${escapeHtml(args === "{}" ? "(no arguments)" : args)}</pre>` : ""}
      ${output}
    </section>
  `;
}

function renderPlan(plan: PlanEvent): string {
  const actions = plan.discarded
    ? `<span class="plan-state">PLAN DISCARDED</span>`
    : plan.executed
      ? `<span class="plan-state">PLAN EXECUTION STARTED</span>`
      : plan.confirmed
        ? `<span class="plan-state">PLAN CONFIRMED</span>`
      : `
        <div class="plan-actions">
          <button type="button" data-plan-id="${escapeHtml(plan.id)}" data-plan-action="discard">DISCARD PLAN</button>
          <button type="button" class="confirm" data-plan-id="${escapeHtml(plan.id)}" data-plan-action="confirm">CONFIRM &amp; EXECUTE</button>
        </div>
      `;
  return `
    <section class="plan-card" aria-label="待确认计划">
      <header>
        <span>PLAN MODE / HUMAN CONFIRMATION</span>
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

function renderAssistant(message: UiMessage): string {
  const parts = message.parts
    .map((part, index) => {
      if (part.type === "tool") {
        const needsApproval = part.tool.state === "waiting";
        if (transcriptMode !== "verbose" && !needsApproval) {
          return "";
        }
        return renderTool(part.tool, transcriptMode === "verbose");
      }
      if (part.type === "plan") {
        return renderPlan(part.plan);
      }
      if (part.type === "thinking") {
        if (transcriptMode === "normal") {
          return "";
        }
        if (!part.text) {
          return "";
        }
        const live = Boolean(
          message.streaming && index === message.parts.length - 1,
        );
        if (live) {
          const tail = part.text.slice(-280);
          return `
            <section class="reasoning-live" aria-label="模型正在思考">
              <header>
                <span><i aria-hidden="true"></i> THINKING / LIVE</span>
                <span>${part.text.length.toLocaleString()} chars</span>
              </header>
              <pre>${tail.length < part.text.length ? "…" : ""}${escapeHtml(tail)}</pre>
            </section>
          `;
        }
        return `
          <details class="reasoning-block"${transcriptMode === "thinking" || transcriptMode === "verbose" ? " open" : ""}>
            <summary>
              <span>REASONING</span>
              <span>${part.text.length.toLocaleString()} chars</span>
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
    message.streaming && !message.text
      ? `
        <div class="thinking-line" aria-label="模型正在生成">
          <span></span><span></span><span></span>
          <em>${
            message.queued
              ? "QUEUED · WAITING FOR TURN SLOT"
              : message.thinking
                ? "FORMING RESPONSE"
                : "WAITING FOR FIRST TOKEN"
          }</em>
        </div>
      `
      : "";
  const error = message.error
    ? `
      <div class="message-error">
        <div>
          <strong>RUN ERROR</strong>
          <p>${escapeHtml(message.error)}</p>
        </div>
        <button class="retry-run" type="button" data-retry-message-id="${escapeHtml(message.id)}">RETRY</button>
      </div>
    `
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
  const steering =
    message.steeringState
      ? `<span class="steering-state is-${message.steeringState}">${message.steeringState.toUpperCase()}</span>`
      : "";
  return `
    <article class="message user-message message-appear" data-message-id="${escapeHtml(message.id)}">
      <header class="user-heading">
        <span>YOU ${steering}</span>
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
        <span>COMMAND / ${escapeHtml(commandReceipt.title)}</span>
        <span>LOCAL ONLY</span>
      </header>
      <pre>${escapeHtml(commandReceipt.body)}</pre>
    </section>
  `;
}

function messageSearchText(message: UiMessage): string {
  if (message.role === "user") {
    return message.text;
  }
  const chunks = message.parts.map((part) => {
    if (part.type === "text" || part.type === "thinking") {
      return part.text;
    }
    if (part.type === "plan") {
      return part.plan.text;
    }
    return `${part.tool.name}\n${JSON.stringify(part.tool.input)}\n${
      part.tool.output ?? ""
    }`;
  });
  return chunks.join("\n");
}

function conversationSearchSnippet(text: string, query: string): string {
  const compact = text.replace(/\s+/g, " ").trim();
  const normalizedQuery = query.trim().toLowerCase();
  const position = normalizedQuery
    ? compact.toLowerCase().indexOf(normalizedQuery)
    : 0;
  const start = position <= 30 ? 0 : position - 30;
  return `${start > 0 ? "…" : ""}${compact.slice(start, start + 180)}${
    start + 180 < compact.length ? "…" : ""
  }`;
}

function filterConversationMatches(query: string): void {
  const normalized = query.trim().toLowerCase();
  conversationFindMatches = activeThread()
    .messages.filter((message) => {
      if (!normalized) {
        return true;
      }
      return messageSearchText(message).toLowerCase().includes(normalized);
    })
    .map((message) => ({
      messageId: message.id,
      role: message.role,
      snippet: conversationSearchSnippet(messageSearchText(message), query),
    }));
  conversationFindSelection = 0;
}

function renderConversationFindList(): void {
  conversationFindCount.textContent = `${conversationFindMatches.length} MATCH${
    conversationFindMatches.length === 1 ? "" : "ES"
  }`;
  conversationFindList.innerHTML = conversationFindMatches.length
    ? conversationFindMatches
        .map(
          (match, index) => `
            <button
              class="conversation-find-item${
                index === conversationFindSelection ? " is-selected" : ""
              }"
              type="button"
              role="option"
              aria-selected="${index === conversationFindSelection}"
              data-find-index="${index}"
            >
              <span>${match.role === "user" ? "YOU" : "AGENT"}</span>
              <strong>${escapeHtml(match.snippet)}</strong>
            </button>
          `,
        )
        .join("")
    : `<div class="conversation-find-empty">没有匹配的已渲染消息</div>`;
  const selected = conversationFindList.querySelector(".is-selected");
  selected?.scrollIntoView({ block: "nearest" });
}

function applyConversationFindHighlight(): void {
  conversation
    .querySelectorAll(".is-search-match")
    .forEach((element) => element.classList.remove("is-search-match"));
  if (
    !conversationFindMessageId ||
    conversationFindThreadId !== activeThread().id
  ) {
    return;
  }
  conversation
    .querySelector(
      `[data-message-id="${CSS.escape(conversationFindMessageId)}"]`,
    )
    ?.classList.add("is-search-match");
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
  applyConversationFindHighlight();
}

function renderHeader(): void {
  const thread = activeThread();
  threadTitle.textContent = thread.title;
  threadKicker.textContent = thread.sessionId
    ? `SESSION ${shortSession(thread.sessionId)}`
    : "NEW SESSION";
  traceToggle.disabled = !thread.sessionId;
  traceToggle.title = thread.sessionId
    ? "打开当前会话执行轨迹（/trace）"
    : "当前会话还没有 trace";
}

function renderRunLedger(): void {
  const thread = activeThread();
  const active = runningAssistant(thread);
  const assistant = active ?? lastAssistant(thread);
  const tools = assistantTools(assistant);
  const waitingApproval = tools.some((tool) => tool.state === "waiting");
  const status = active
    ? thread.stopping
      ? "STOPPING"
      : waitingApproval
      ? "APPROVAL"
      : "STREAMING"
    : threadStatus(thread) === "CANCELLED"
      ? "CANCELLED"
      : assistant?.error
        ? "ERROR"
        : thread.messages.length
          ? "COMPLETE"
          : "READY";
  const statusClass =
    status === "STREAMING" || status === "APPROVAL" || status === "STOPPING"
      ? "is-running"
      : status === "ERROR"
        ? "is-error"
        : "is-ready";
  const statusCopy =
    status === "STOPPING"
      ? "停止请求已发送，等待下一个检查点收线"
      : status === "APPROVAL"
      ? "工具等待人工接受或拒绝"
      : status === "STREAMING"
        ? `${thread.steeringMode.toUpperCase()} steering · 模型请求或工具仍在执行`
      : status === "ERROR"
        ? "上一轮没有正常结束"
        : status === "CANCELLED"
          ? "上一轮在检查点被取消"
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
  const usage = thread.usage;
  runInputTokens.textContent = String(usage?.input_tokens ?? 0);
  runOutputTokens.textContent = String(usage?.output_tokens ?? 0);
  runCacheRead.textContent = String(usage?.cache_read_tokens ?? 0);
  runCacheWrite.textContent = String(usage?.cache_write_tokens ?? 0);
  runCacheHit.textContent = `${usage?.cache_hit_ratio ?? 0}‰`;
  runCost.textContent = formatCost(usage);
  runEffort.textContent = effortLabel(currentEffort);
  runApproval.textContent = thread.approvalMode.toUpperCase();
  runSteering.textContent = thread.steeringMode.toUpperCase();
  runStop.textContent = thread.stopReason || "—";
  if (active && thread.runStartedAt) {
    runElapsed.textContent = `${Date.now() - thread.runStartedAt}ms`;
  } else {
    runElapsed.textContent =
      assistant?.durationMs !== undefined ? `${assistant.durationMs}ms` : "—";
  }
  setStatusLight(runtimeLight, statusClass);
  setStatusLight(ledgerLight, statusClass);
  connectionPill.className = `connection-pill ${statusClass}`;
  connectionPill.innerHTML = `<span class="status-light ${statusClass}"></span>${status}`;

  const activity: ActivityItem[] = [];
  if (thread.contextWarning) {
    activity.push({
      key: "context-warning",
      tag: "WARN",
      title: "CONTEXT NEAR BUDGET",
      detail: `${thread.contextWarning.estimate_tokens.toLocaleString()} / ${thread.contextWarning.budget_tokens.toLocaleString()} tokens`,
      status: "error",
    });
  }
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
      tag: tool.isError
        ? "ERR"
        : tool.state === "waiting"
          ? "WAIT"
          : tool.state === "running"
            ? "RUN"
            : "TOOL",
      title: tool.name,
      detail:
        tool.state === "waiting"
          ? "approval required"
          : tool.state === "running"
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

function formatCost(usage: TurnUsage | undefined): string {
  if (!currentPricing) {
    return "NOT CALCULATED";
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

function renderEverything(forceBottom = false): void {
  const thread = activeThread();
  const running = isThreadRunning(thread);
  const stopping = Boolean(thread.stopping && running);
  renderThreadList();
  renderHeader();
  renderConversation(forceBottom);
  renderRunLedger();
  sendButton.disabled = stopping;
  sendLabel.textContent = running ? "STEER" : "RUN";
  stopButton.hidden = !running;
  stopButton.disabled = stopping;
  stopButton.textContent = stopping ? "STOPPING" : "STOP";
  composerStatus.textContent = stopping
    ? "STOPPING · WAITING FOR CHECKPOINT"
    : running
      ? `${thread.steeringMode.toUpperCase()} · ENTER TO STEER · ALT+S SWITCH`
      : "ENTER TO SEND · SHIFT+ENTER FOR NEWLINE";
  promptInput.placeholder = running
    ? `当前轮运行中 · ${thread.steeringMode.toUpperCase()} steering`
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

interface StreamContext {
  thread: Thread;
  assistant: UiMessage;
  userMessage?: UiMessage;
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

function renderModelOptions(models: string[], selectedModel: string): void {
  const options = [...models];
  if (selectedModel && !options.includes(selectedModel)) {
    options.unshift(selectedModel);
  }
  settingsModel.replaceChildren(
    ...options.map((modelId) => {
      const option = document.createElement("option");
      option.value = modelId;
      option.textContent = modelId;
      return option;
    }),
  );
  settingsModel.value = selectedModel;
  currentModelOptions = options;
  settingsModelCount.textContent = models.length
    ? `${models.length} MODELS`
    : "NO MODELS RETURNED";
}

function applySettings(snapshot: SettingsSnapshot): void {
  currentEffort = snapshot.effort;
  currentPricing = snapshot.pricing;
  settingsProvider.value = snapshot.provider;
  renderModelOptions(currentModelOptions, snapshot.model_id);
  settingsBaseUrl.value = snapshot.base_url;
  settingsMaxTokens.value = String(snapshot.max_tokens);
  settingsEffort.value = snapshot.effort;
  settingsApiKey.value = "";
  settingsApiKey.placeholder = snapshot.api_key_configured
    ? "留空保持当前密钥"
    : "输入 API Key，或写 ${ENV_VAR}";
  settingsKeySource.textContent = `当前密钥：${snapshot.api_key_source} · 留空不改`;
  runEffort.textContent = effortLabel(currentEffort);
}

async function loadModelOptions(
  selectedModel: string,
  loadedLabel = "配置已读取",
): Promise<void> {
  settingsModel.disabled = true;
  settingsModelCount.textContent = "READING MODELS…";
  renderModelOptions([selectedModel], selectedModel);
  try {
    const response = await fetch(`${API_BASE}/models`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const parsed = (await response.json()) as ModelListResponse;
    renderModelOptions(parsed.models, selectedModel);
    setSettingsStatus(
      `${loadedLabel}；模型列表 ${parsed.models.length} 个可选项。`,
      "ok",
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : String(error);
    renderModelOptions([selectedModel], selectedModel);
    settingsModelCount.textContent = "MODEL LIST UNAVAILABLE";
    setSettingsStatus(`${loadedLabel}；模型列表不可用：${message}`, "error");
  } finally {
    settingsModel.disabled = false;
  }
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
    await loadModelOptions(snapshot.model_id);
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
    pricing: null,
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
  let savedModelId = "";
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
    savedModelId = snapshot.model_id;
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
  if (savedModelId) {
    await loadModelOptions(savedModelId, "配置已保存");
  }
}

async function submitPrompt(rawPrompt: string): Promise<void> {
  const question = rawPrompt.trim();
  const images = pendingImages;
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
  pendingImages = [];
  renderPendingImages();
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
  if (thread.title === "未命名运行") {
    thread.title = threadTitleFor(question);
  }
  thread.model = settingsModel.value.trim() || thread.model;
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
  if (pendingImages.length) {
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
  if (thread.title === "未命名运行") {
    thread.title = threadTitleFor(task);
  }
  thread.model = settingsModel.value.trim() || thread.model;
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
    `${thread.steeringMode.toUpperCase()} steering 已发送 · ${
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
      }),
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
  } finally {
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
      if (settingsModal.hidden && resumeModal.hidden && traceModal.hidden) {
        promptInput.focus();
      }
    }
  }
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
      thread.stopping = false;
      showToast("后端没有正在运行的轮次；本地事件流可能刚刚结束");
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

function traceDuration(span: TraceSpan): string {
  return span.duration_ms === null ? "open" : `${span.duration_ms}ms`;
}

function traceStatusClass(status: TraceSpan["status"]): string {
  return `is-${status}`;
}

function traceSpanHint(span: TraceSpan): string {
  if (span.name === "llm_request") {
    const ttft = span.data.ttft_ms;
    const model = span.data.model;
    return [model, ttft !== undefined && ttft !== null ? `ttft ${ttft}ms` : ""]
      .filter(Boolean)
      .join(" · ");
  }
  if (span.name === "tool_call") {
    return String(span.data.tool ?? "tool");
  }
  return "";
}

function traceTreeMarkup(): string {
  const ids = new Set(traceSpans.map((span) => span.span_id));
  const children = new Map<string, TraceSpan[]>();
  for (const span of traceSpans) {
    if (!span.parent_id || !ids.has(span.parent_id)) {
      continue;
    }
    const bucket = children.get(span.parent_id) ?? [];
    bucket.push(span);
    children.set(span.parent_id, bucket);
  }
  const roots = traceSpans.filter(
    (span) => !span.parent_id || !ids.has(span.parent_id),
  );
  const visited = new Set<string>();

  const renderSpan = (span: TraceSpan, depth: number): string => {
    if (visited.has(span.span_id)) {
      return "";
    }
    visited.add(span.span_id);
    const selected = span.span_id === traceSelectedSpanId;
    const hint = traceSpanHint(span);
    return `
      <button
        class="trace-node ${selected ? "is-selected" : ""}"
        type="button"
        role="treeitem"
        aria-selected="${selected}"
        data-span-id="${escapeHtml(span.span_id)}"
        style="--trace-depth: ${depth}"
      >
        <span class="trace-node-guide" aria-hidden="true"></span>
        <span class="trace-node-dot ${traceStatusClass(span.status)}" aria-hidden="true"></span>
        <span class="trace-node-main">
          <strong>${escapeHtml(span.name)}</strong>
          <small>${escapeHtml(span.status)} · ${traceDuration(span)}${hint ? ` · ${escapeHtml(hint)}` : ""}</small>
        </span>
      </button>
      ${(children.get(span.span_id) ?? [])
        .map((child) => renderSpan(child, depth + 1))
        .join("")}
    `;
  };

  return roots.map((span) => renderSpan(span, 0)).join("");
}

function traceField(label: string, value: unknown): string {
  const rendered =
    value === undefined || value === null || value === ""
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return `
    <div class="trace-field">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(rendered)}</code>
    </div>
  `;
}

function traceDetailMarkup(span: TraceSpan | undefined): string {
  if (!span) {
    return `
      <div class="trace-empty">
        <strong>NO SPAN SELECTED</strong>
        <p>从左侧选择一步，查看入参、耗时、状态和落盘回答。</p>
      </div>
    `;
  }
  const data = span.data;
  const answer = traceAnswers[span.span_id] ?? "";
  const known = new Set([
    "message",
    "model",
    "messages",
    "ttft_ms",
    "stop_reason",
    "effort",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_ratio",
    "tool",
    "input",
    "output",
  ]);
  const remaining = Object.fromEntries(
    Object.entries(data).filter(([key]) => !known.has(key)),
  );
  let body = "";
  if (span.name === "llm_request") {
    body = `
      <section class="trace-detail-section">
        <h3>MODEL REQUEST</h3>
        <div class="trace-fields">
          ${traceField("model", data.model)}
          ${traceField("messages", data.messages)}
          ${traceField("ttft", data.ttft_ms === undefined ? "—" : `${data.ttft_ms}ms`)}
          ${traceField("stop", data.stop_reason)}
          ${traceField("effort", data.effort)}
          ${traceField("tokens", `in ${data.input_tokens ?? "—"} / out ${data.output_tokens ?? "—"}`)}
          ${traceField(
            "cache",
            `read ${data.cache_read_tokens ?? "—"} / write ${data.cache_write_tokens ?? "—"} / hit ${data.cache_hit_ratio ?? "—"}‰`,
          )}
        </div>
      </section>
    `;
  } else if (span.name === "tool_call") {
    body = `
      <section class="trace-detail-section">
        <h3>TOOL CALL</h3>
        <div class="trace-fields">
          ${traceField("tool", data.tool)}
        </div>
      </section>
      <section class="trace-detail-section">
        <h3>INPUT</h3>
        <pre>${escapeHtml(JSON.stringify(data.input ?? {}, null, 2))}</pre>
      </section>
      <section class="trace-detail-section">
        <h3>OUTPUT</h3>
        <pre class="${span.status === "error" ? "is-error" : ""}">${escapeHtml(String(data.output ?? "—"))}</pre>
      </section>
    `;
  }
  if (Object.keys(remaining).length) {
    body += `
      <section class="trace-detail-section">
        <h3>SPAN DATA</h3>
        <pre>${escapeHtml(JSON.stringify(remaining, null, 2))}</pre>
      </section>
    `;
  }
  if (data.message) {
    body += `
      <section class="trace-detail-section">
        <h3>ERROR MESSAGE</h3>
        <pre class="is-error">${escapeHtml(String(data.message))}</pre>
      </section>
    `;
  }
  if (answer) {
    body += `
      <section class="trace-detail-section trace-answer">
        <h3>PAIRED ANSWER</h3>
        <div class="markdown">${renderMarkdown(answer)}</div>
      </section>
    `;
  }
  const localStart = new Date(span.start_time).toLocaleString("zh-CN");
  const localEnd = span.end_time
    ? new Date(span.end_time).toLocaleString("zh-CN")
    : "—";
  return `
    <article class="trace-span-card">
      <header>
        <div>
          <span class="eyebrow">SPAN / ${escapeHtml(span.span_id.slice(0, 12))}</span>
          <h2>${escapeHtml(span.name)}</h2>
        </div>
        <span class="trace-status ${traceStatusClass(span.status)}">${escapeHtml(span.status.toUpperCase())}</span>
      </header>
      <div class="trace-fields trace-span-meta">
        ${traceField("duration", traceDuration(span))}
        ${traceField("start", localStart)}
        ${traceField("end", localEnd)}
        ${traceField("parent", span.parent_id ?? "root")}
      </div>
      ${body || `<div class="trace-empty"><p>这段 span 没有附加数据。</p></div>`}
    </article>
  `;
}

function selectTraceSpan(spanId: string): void {
  traceSelectedSpanId = spanId;
  const span = traceSpans.find((candidate) => candidate.span_id === spanId);
  traceTree.innerHTML = traceTreeMarkup();
  traceDetail.innerHTML = traceDetailMarkup(span);
  traceSelectedState.textContent = span
    ? `${span.status.toUpperCase()} · ${traceDuration(span)}`
    : "WAITING";
}

function renderTrace(): void {
  traceCount.textContent = String(traceSpans.length);
  const turns = traceSpans.filter((span) => span.name === "turn").length;
  const errors = traceSpans.filter((span) => span.status === "error").length;
  const running = traceSpans.filter(
    (span) => span.status === "running",
  ).length;
  const wall = traceSpans
    .filter((span) => span.name === "turn")
    .reduce((total, span) => total + (span.duration_ms ?? 0), 0);
  traceSummary.innerHTML = `
    <span>SESSION ${escapeHtml(shortSession(traceSessionId))}</span>
    <span>TURNS ${turns}</span>
    <span>SPANS ${traceSpans.length}</span>
    <span>ERRORS ${errors}</span>
    <span>RUNNING ${running}</span>
    <span>WALL ${wall}ms</span>
  `;
  traceTree.innerHTML = traceTreeMarkup();
  selectTraceSpan(traceSelectedSpanId);
  requestAnimationFrame(() => {
    traceTree
      .querySelector<HTMLElement>(".trace-node.is-selected")
      ?.scrollIntoView({ block: "center" });
  });
}

async function loadTrace(): Promise<void> {
  const thread = activeThread();
  traceSessionId = thread.sessionId;
  if (!traceSessionId) {
    traceSpans = [];
    traceAnswers = {};
    traceSelectedSpanId = "";
    traceSummary.innerHTML = "<span>SESSION PENDING</span><span>NO TRACE</span>";
    traceTree.innerHTML = "";
    traceDetail.innerHTML = `
      <div class="trace-empty">
        <strong>NO SESSION YET</strong>
        <p>先发送一轮消息，span 从第一轮开始写入。</p>
      </div>
    `;
    traceCount.textContent = "0";
    traceSelectedState.textContent = "WAITING";
    return;
  }
  const sequence = ++traceLoadSequence;
  traceRefreshButton.disabled = true;
  traceSummary.innerHTML = `<span>SESSION ${escapeHtml(shortSession(traceSessionId))}</span><span>READING SQLITE + JSONL…</span>`;
  traceTree.innerHTML = `<div class="trace-loading">LOADING SPAN TREE</div>`;
  traceDetail.innerHTML = `<div class="trace-loading">READING TRACE</div>`;
  traceSelectedState.textContent = "LOADING";
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(traceSessionId)}/trace`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const trace = (await response.json()) as TraceResponse;
    if (sequence !== traceLoadSequence || traceModal.hidden) {
      return;
    }
    traceSpans = trace.spans;
    traceAnswers = trace.answers;
    const preferred =
      [...traceSpans].reverse().find(
        (span) => span.name === "llm_request" || span.name === "tool_call",
      ) ??
      traceSpans[0];
    traceSelectedSpanId = preferred?.span_id ?? "";
    renderTrace();
  } catch (error) {
    if (sequence !== traceLoadSequence) {
      return;
    }
    traceSpans = [];
    traceAnswers = {};
    traceTree.innerHTML = "";
    traceCount.textContent = "0";
    traceSummary.innerHTML = `<span>SESSION ${escapeHtml(shortSession(traceSessionId))}</span><span>TRACE UNAVAILABLE</span>`;
    traceSelectedState.textContent = "ERROR";
    traceDetail.innerHTML = `
      <div class="trace-empty is-error">
        <strong>TRACE READ FAILED</strong>
        <p>${escapeHtml(error instanceof Error ? error.message : String(error))}</p>
      </div>
    `;
  } finally {
    if (sequence === traceLoadSequence) {
      traceRefreshButton.disabled = false;
    }
  }
}

function openTrace(): void {
  const thread = activeThread();
  traceSessionId = thread.sessionId;
  traceModal.hidden = false;
  document.title = `AgentLearn · trace ${shortSession(thread.sessionId)}`;
  void loadTrace();
}

function renderWorkspaceDiff(): void {
  if (!workspaceDiff) {
    return;
  }
  workspaceDiffSummary.innerHTML = `
    <span>${escapeHtml(workspaceDiff.root)}</span>
    <span>TRACKED ${workspaceDiff.files.filter((file) => file.status !== "untracked").length}</span>
    <span>UNTRACKED ${workspaceDiff.include_untracked ? "INCLUDED" : "EXCLUDED"} ${workspaceDiff.untracked_count}</span>
  `;
  workspaceDiffList.innerHTML = workspaceDiff.files.length
    ? workspaceDiff.files
        .map(
          (file) => `
            <details class="workspace-diff-item is-${escapeHtml(file.status)}" open>
              <summary>
                <span class="workspace-diff-status">${escapeHtml(file.status.toUpperCase())}</span>
                <span class="workspace-diff-file-heading">
                <strong>${escapeHtml(file.path)}</strong>
                ${file.old_path ? `<small>${escapeHtml(file.old_path)} → ${escapeHtml(file.path)}</small>` : ""}
                <small class="workspace-diff-counts">+${file.additions} −${file.deletions}</small>
                ${file.binary ? '<small class="workspace-diff-binary">BINARY</small>' : ""}
                ${file.truncated ? '<small class="workspace-diff-truncated">PATCH TRUNCATED</small>' : ""}
                </span>
                <span class="workspace-diff-fold" aria-hidden="true"></span>
              </summary>
              <div class="workspace-diff-file-main">
                ${file.hunks.map((hunk) => `
                  <section class="workspace-diff-hunk">
                    <header>${escapeHtml(hunk.header)}</header>
                    <div class="workspace-diff-lines">
                      ${hunk.lines.map((line) => `
                        <div class="workspace-diff-line is-${line.kind}">
                          <span>${line.old_line ?? ""}</span>
                          <span>${line.new_line ?? ""}</span>
                          <code>${escapeHtml(line.text)}</code>
                        </div>
                      `).join("")}
                    </div>
                  </section>
                `).join("")}
              </div>
            </details>
          `,
        )
        .join("")
    : `<div class="workspace-diff-empty">没有已跟踪文件的变更。</div>`;
}

async function loadWorkspaceDiff(): Promise<void> {
  const sequence = ++workspaceDiffLoadSequence;
  workspaceDiffRefreshButton.disabled = true;
  workspaceDiffSummary.innerHTML = "<span>READING GIT STATUS…</span>";
  workspaceDiffList.innerHTML = `<div class="workspace-diff-empty">READING WORKSPACE</div>`;
  try {
    const response = await fetch(
      `${API_BASE}/workspace/diff?include_untracked=true`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as WorkspaceDiffResponse;
    if (sequence !== workspaceDiffLoadSequence || workspaceDiffModal.hidden) {
      return;
    }
    workspaceDiff = result;
    renderWorkspaceDiff();
  } catch (error) {
    if (sequence !== workspaceDiffLoadSequence) {
      return;
    }
    workspaceDiff = null;
    workspaceDiffSummary.innerHTML = "<span>WORKSPACE UNAVAILABLE</span>";
    workspaceDiffList.innerHTML = `
      <div class="workspace-diff-empty is-error">
        <strong>WORKSPACE READ FAILED</strong>
        <p>${escapeHtml(error instanceof Error ? error.message : String(error))}</p>
      </div>
    `;
  } finally {
    if (sequence === workspaceDiffLoadSequence) {
      workspaceDiffRefreshButton.disabled = false;
    }
  }
}

function openWorkspaceDiff(): void {
  workspaceDiffModal.hidden = false;
  void loadWorkspaceDiff();
}

function closeWorkspaceDiff(): void {
  workspaceDiffLoadSequence += 1;
  workspaceDiffModal.hidden = true;
  promptInput.focus();
}

function closeTrace(): void {
  traceLoadSequence += 1;
  traceModal.hidden = true;
  document.title = "AgentLearn";
  promptInput.focus();
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

function switchThread(threadId: string): void {
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
            thread.id.toLowerCase().includes(query) ||
            thread.sessionId.toLowerCase().includes(query) ||
            thread.title.toLowerCase().includes(query) ||
            thread.model.toLowerCase().includes(query) ||
            thread.stopReason.toLowerCase().includes(query),
        );
  if (candidates.length === 1) {
    return candidates[0];
  }
  showToast(
    candidates.length > 1
      ? "匹配到多个会话，请补充标题、id 或模型关键词"
      : `没有 id / 标题 / 模型匹配 ${argument} 的会话`,
  );
  return null;
}

function filterResumeThreads(): void {
  const query = resumeQuery.trim().toLowerCase();
  const all = state.threads
    .filter((thread) => thread.archived === resumeArchived)
    .sort(
      (a, b) =>
        Number(b.pinned) - Number(a.pinned) || b.updatedAt - a.updatedAt,
    );
  const contentMatchIds = new Set(contentHits.keys());
  resumeMatches = query
    ? all.filter(
        (thread) =>
          thread.id.toLowerCase().includes(query) ||
          thread.sessionId.toLowerCase().includes(query) ||
          thread.title.toLowerCase().includes(query) ||
          thread.model.toLowerCase().includes(query) ||
            thread.stopReason.toLowerCase().includes(query) ||
            contentMatchIds.has(thread.sessionId),
      )
    : all;
  resumeSelection = Math.min(
    resumeSelection,
    Math.max(0, resumeMatches.length - 1),
  );
}

function renderResumeList(): void {
  const currentId = state.activeThreadId;
  resumeSummary.textContent = `${resumeMatches.length} 个会话 · 正文命中 ${contentHits.size} · 当前 ${shortSession(
    activeThread().sessionId,
  )}`;
  const selectedThread = resumeMatches[resumeSelection];
  if (deleteThreadId && selectedThread?.id !== deleteThreadId) {
    cancelResumeDelete();
  }
  if (forkThreadId && selectedThread?.id !== forkThreadId) {
    cancelResumeFork();
  }
  resumeRenameButton.disabled = !selectedThread?.sessionId;
  resumePinButton.disabled = !selectedThread?.sessionId;
  resumePinButton.textContent = selectedThread?.pinned ? "UNPIN" : "PIN";
  resumeArchiveButton.disabled = !selectedThread?.sessionId;
  resumeArchiveButton.textContent = selectedThread?.archived
    ? "UNARCHIVE"
    : "ARCHIVE";
  resumeDeleteButton.disabled = !selectedThread?.sessionId;
  resumeForkButton.disabled = !selectedThread?.sessionId;
  resumeList.innerHTML = resumeMatches
    .map((thread, index) => {
      const turns =
        thread.serverTurns ??
        thread.messages.filter((message) => message.role === "user").length;
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
          <small>${thread.pinned ? "PIN · " : ""}${escapeHtml(thread.model || "model unknown")} · ${escapeHtml(
            thread.stopReason || "no completed run",
          )}</small>
          ${
            contentHits.has(thread.sessionId)
              ? `<em class="resume-content-hit">${escapeHtml(
                  contentHits.get(thread.sessionId)?.snippet ?? "",
                )}</em>`
              : ""
          }
        </button>
      `;
    })
    .join("");
}

async function openResume(): Promise<void> {
  resumeArchived = false;
  await fetchServerSessions(true);
  resumeQuery = "";
  contentHits.clear();
  contentSearchSequence += 1;
  resumeSearch.value = "";
  filterResumeThreads();
  const alternate = resumeMatches.findIndex(
    (thread) => thread.id !== state.activeThreadId,
  );
  resumeSelection = alternate >= 0 ? alternate : 0;
  cancelResumeRename();
  renderResumeList();
  renderResumeViewTabs();
  resumeModal.hidden = false;
  resumeSearch.focus();
}

function renderResumeViewTabs(): void {
  resumeActiveViewButton.setAttribute(
    "aria-pressed",
    resumeArchived ? "false" : "true",
  );
  resumeArchiveViewButton.setAttribute(
    "aria-pressed",
    resumeArchived ? "true" : "false",
  );
}

async function switchResumeView(archived: boolean): Promise<void> {
  if (resumeArchived === archived) {
    return;
  }
  resumeArchived = archived;
  resumeQuery = "";
  contentHits.clear();
  contentSearchSequence += 1;
  resumeSearch.value = "";
  resumeSelection = 0;
  renderResumeViewTabs();
  filterResumeThreads();
  renderResumeList();
  await fetchServerSessions(true, archived);
  resumeSelection = 0;
  filterResumeThreads();
  renderResumeList();
}

async function searchSessionContent(query: string): Promise<void> {
  const sequence = ++contentSearchSequence;
  contentHits = new Map();
  filterResumeThreads();
  renderResumeList();
  const needle = query.trim();
  if (!needle || resumeModal.hidden) {
    return;
  }
  try {
    const response = await fetch(
      `${API_BASE}/sessions/search?q=${encodeURIComponent(needle)}`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as SessionSearchResponse;
    if (sequence !== contentSearchSequence || resumeModal.hidden) {
      return;
    }
    for (const hit of result.hits) {
      if (!contentHits.has(hit.session_id)) {
        contentHits.set(hit.session_id, hit);
      }
    }
    filterResumeThreads();
    renderResumeList();
  } catch (error) {
    if (sequence === contentSearchSequence) {
      showToast(
        `正文搜索失败：${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  }
}

function startResumeRename(): void {
  const thread = resumeMatches[resumeSelection];
  if (!thread?.sessionId) {
    showToast("当前会话还没有 server id，不能重命名");
    return;
  }
  renameThreadId = thread.id;
  resumeRenameInput.value = thread.title;
  resumeRenameForm.hidden = false;
  resumeRenameInput.focus();
  resumeRenameInput.select();
}

function cancelResumeRename(): void {
  renameThreadId = "";
  resumeRenameForm.hidden = true;
  resumeRenameInput.value = "";
  resumeRenameButton.disabled = false;
}

async function saveResumeRename(): Promise<void> {
  const thread = state.threads.find((item) => item.id === renameThreadId);
  const title = resumeRenameInput.value.trim();
  if (!thread?.sessionId) {
    cancelResumeRename();
    return;
  }
  if (!title) {
    showToast("标题不能为空");
    resumeRenameInput.focus();
    return;
  }
  resumeRenameButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const updated = (await response.json()) as ServerSession;
    thread.title = updated.title || title;
    persistState();
    cancelResumeRename();
    filterResumeThreads();
    renderResumeList();
    renderEverything();
    showToast("会话已重命名");
  } catch (error) {
    showToast(
      `重命名失败：${error instanceof Error ? error.message : String(error)}`,
    );
  } finally {
    resumeRenameButton.disabled = false;
  }
}

async function toggleResumePin(): Promise<void> {
  const thread = resumeMatches[resumeSelection];
  if (!thread?.sessionId) {
    showToast("当前会话还没有 server id，不能 pin");
    return;
  }
  resumePinButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: !thread.pinned }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const updated = (await response.json()) as ServerSession;
    thread.pinned = updated.pinned === true;
    persistState();
    filterResumeThreads();
    resumeSelection = Math.max(
      0,
      resumeMatches.findIndex((item) => item.id === thread.id),
    );
    renderResumeList();
    renderEverything();
    showToast(thread.pinned ? "会话已 pin" : "会话已取消 pin");
  } catch (error) {
    showToast(
      `pin 状态更新失败：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  } finally {
    resumePinButton.disabled = false;
  }
}

async function toggleResumeArchive(): Promise<void> {
  const thread = resumeMatches[resumeSelection];
  if (!thread?.sessionId) {
    showToast("当前会话还没有 server id，不能归档");
    return;
  }
  resumeArchiveButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !thread.archived }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const updated = (await response.json()) as ServerSession;
    thread.archived = updated.archived === true;
    thread.pinned = updated.pinned === true;
    persistState();
    filterResumeThreads();
    resumeSelection = Math.max(
      0,
      resumeMatches.findIndex((item) => item.id === thread.id),
    );
    renderResumeList();
    renderEverything();
    showToast(thread.archived ? "会话已归档" : "会话已恢复");
  } catch (error) {
    showToast(
      `归档状态更新失败：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  } finally {
    resumeArchiveButton.disabled = false;
  }
}

function cancelResumeDelete(): void {
  deleteThreadId = "";
  resumeDeleteConfirm.hidden = true;
  resumeDeleteTitle.textContent = "";
  resumeDeleteButton.disabled = false;
}

function requestResumeDelete(): void {
  const thread = resumeMatches[resumeSelection];
  if (!thread?.sessionId) {
    showToast("当前会话还没有 server id，不能删除");
    return;
  }
  deleteThreadId = thread.id;
  resumeDeleteTitle.textContent = thread.title;
  resumeDeleteConfirm.hidden = false;
  resumeDeleteConfirmButton.focus();
}

async function confirmResumeDelete(): Promise<void> {
  const thread = state.threads.find((item) => item.id === deleteThreadId);
  if (!thread?.sessionId) {
    cancelResumeDelete();
    return;
  }
  resumeDeleteButton.disabled = true;
  resumeDeleteConfirmButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    state.threads = state.threads.filter((item) => item.id !== thread.id);
    if (state.activeThreadId === thread.id) {
      const nextThread = state.threads.find((item) => !item.archived);
      if (nextThread) {
        state.activeThreadId = nextThread.id;
      } else {
        const created = createThread();
        state.threads.push(created);
        state.activeThreadId = created.id;
      }
    }
    cancelResumeDelete();
    persistState();
    filterResumeThreads();
    resumeSelection = Math.min(
      resumeSelection,
      Math.max(0, resumeMatches.length - 1),
    );
    renderResumeList();
    renderEverything();
    showToast("会话已永久删除");
  } catch (error) {
    showToast(
      `永久删除失败：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  } finally {
    resumeDeleteButton.disabled = false;
    resumeDeleteConfirmButton.disabled = false;
  }
}

function cancelResumeFork(): void {
  forkThreadId = "";
  forkMessages = [];
  resumeForkForm.hidden = true;
  resumeForkSource.textContent = "";
  resumeForkMax.textContent = "";
  resumeForkInput.value = "1";
  resumeForkPreview.textContent = "";
  resumeForkButton.disabled = false;
}

function messagePreview(message: ServerMessage): string {
  const text = message.content
    .filter((block) => block.type === "text")
    .map((block) => (block.type === "text" ? block.text : ""))
    .join(" ")
    .trim();
  return `${message.role.toUpperCase()}: ${text || "[non-text block]"}`;
}

function updateResumeForkPreview(): void {
  const through = Number(resumeForkInput.value);
  const message = forkMessages[through - 1];
  resumeForkPreview.textContent = message
    ? `#${through} ${messagePreview(message)}`
    : "选择的 N 超出消息范围";
}

async function startResumeFork(): Promise<void> {
  const thread = resumeMatches[resumeSelection];
  if (!thread?.sessionId) {
    showToast("当前会话还没有 server id，不能 fork");
    return;
  }
  resumeForkButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}/messages`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const messages = (await response.json()) as ServerMessage[];
    if (!messages.length) {
      showToast("这个会话没有可复制的消息");
      return;
    }
    forkThreadId = thread.id;
    forkMessages = messages;
    resumeForkSource.textContent = thread.title;
    resumeForkInput.min = "1";
    resumeForkInput.max = String(messages.length);
    resumeForkInput.value = "1";
    resumeForkMax.textContent = `OF ${messages.length} MESSAGES`;
    resumeForkForm.hidden = false;
    updateResumeForkPreview();
    resumeForkInput.focus();
    resumeForkInput.select();
  } catch (error) {
    showToast(
      `读取 fork 源消息失败：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  } finally {
    resumeForkButton.disabled = false;
  }
}

async function saveResumeFork(): Promise<void> {
  const source = state.threads.find((item) => item.id === forkThreadId);
  if (!source?.sessionId || !forkMessages.length) {
    cancelResumeFork();
    return;
  }
  const through = Number(resumeForkInput.value);
  if (
    !Number.isInteger(through) ||
    through < 1 ||
    through > forkMessages.length
  ) {
    showToast(`through 要在 1..${forkMessages.length} 之间`);
    resumeForkInput.focus();
    return;
  }
  resumeForkSubmitButton.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(source.sessionId)}/fork`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ through }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as SessionForkResponse;
    await fetchServerSessions(false);
    let created = state.threads.find(
      (thread) => thread.sessionId === result.session_id,
    );
    if (!created) {
      created = createThread();
      created.sessionId = result.session_id;
      created.title = `${source.title}（分支）`;
      created.model = source.model;
      created.serverTurns = forkMessages
        .slice(0, through)
        .filter((message) => message.role === "user").length;
      state.threads.push(created);
    }
    created.messagesLoaded = false;
    cancelResumeFork();
    persistState();
    switchThread(created.id);
    showToast(`已 fork 前 ${result.copied} 条消息`);
  } catch (error) {
    showToast(
      `fork 失败：${error instanceof Error ? error.message : String(error)}`,
    );
  } finally {
    resumeForkSubmitButton.disabled = false;
  }
}

function closeResume(): void {
  resumeModal.hidden = true;
  cancelResumeRename();
  cancelResumeDelete();
  cancelResumeFork();
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

async function selectResume(index: number): Promise<void> {
  const thread = resumeMatches[index];
  if (!thread) {
    return;
  }
  await hydrateThread(thread, true);
  switchThread(thread.id);
  showToast(`已续接：${thread.title}`);
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
  fileCompletionSequence += 1;
  if (fileCompletionTimer !== undefined) {
    window.clearTimeout(fileCompletionTimer);
    fileCompletionTimer = undefined;
  }
  commandPalette.hidden = true;
  paletteMode = "command";
  fileMatches = [];
  fileSelection = 0;
  commandPaletteTitle.textContent = "COMMAND INDEX";
  commandPaletteEnterAction.textContent = "ENTER RUN";
  commandPalette.ariaLabel = "命令面板";
}

function renderCommandPalette(): void {
  commandPaletteTitle.textContent = "COMMAND INDEX";
  commandPaletteEnterAction.textContent = "ENTER RUN";
  commandPalette.ariaLabel = "命令面板";
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

function activeFileToken(
  value: string,
  caret: number,
): { query: string; tokenStart: number } | null {
  const match = value.slice(0, caret).match(/(?:^|\s)@([^\s]*)$/);
  if (!match) {
    return null;
  }
  return {
    query: match[1],
    tokenStart:
      (match.index ?? 0) + match[0].length - match[1].length - 1,
  };
}

function renderFilePalette(): void {
  commandPaletteTitle.textContent = "FILE PATHS";
  commandPaletteEnterAction.textContent = "ENTER COMPLETE";
  commandPalette.ariaLabel = "文件路径补全";
  commandList.innerHTML = fileMatches.length
    ? fileMatches
        .map(
          (path, index) => `
          <button
            class="command-option${index === fileSelection ? " is-selected" : ""}"
            type="button"
            role="option"
            aria-selected="${index === fileSelection}"
            data-file-index="${index}"
          >
            <code>${escapeHtml(path)}</code>
            <span>FILE PATH</span>
          </button>
        `,
        )
        .join("")
    : `<div class="command-group">NO MATCHING PATH</div>`;
}

function scheduleFilePalette(token: { query: string; tokenStart: number }): void {
  const sequence = ++fileCompletionSequence;
  paletteMode = "file";
  fileMatches = [];
  fileSelection = 0;
  renderFilePalette();
  commandList.innerHTML = `<div class="command-group">READING PATH NAMES…</div>`;
  commandPalette.hidden = false;
  if (fileCompletionTimer !== undefined) {
    window.clearTimeout(fileCompletionTimer);
  }
  fileCompletionTimer = window.setTimeout(() => {
    fileCompletionTimer = undefined;
    void loadFilePalette(token, sequence);
  }, 120);
}

async function loadFilePalette(
  token: { query: string; tokenStart: number },
  sequence: number,
): Promise<void> {
  try {
    const response = await fetch(
      `${API_BASE}/workspace/paths?q=${encodeURIComponent(token.query)}&limit=50`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const result = (await response.json()) as WorkspacePathsResponse;
    const current = activeFileToken(
      promptInput.value,
      promptInput.selectionStart ?? promptInput.value.length,
    );
    if (
      sequence !== fileCompletionSequence ||
      paletteMode !== "file" ||
      !current ||
      current.tokenStart !== token.tokenStart
    ) {
      return;
    }
    fileMatches = result.paths;
    fileSelection = 0;
    renderFilePalette();
  } catch {
    if (sequence !== fileCompletionSequence || paletteMode !== "file") {
      return;
    }
    fileMatches = [];
    renderFilePalette();
    commandList.innerHTML = `<div class="command-group">PATH COMPLETION UNAVAILABLE</div>`;
  }
}

function updateCommandPalette(): void {
  const caret = promptInput.selectionStart ?? promptInput.value.length;
  const fileToken = activeFileToken(promptInput.value, caret);
  if (fileToken) {
    scheduleFilePalette(fileToken);
    return;
  }
  if (paletteMode === "file") {
    closeCommandPalette();
  }
  const text = promptInput.value.trim();
  if (!text.startsWith("/") || /\s/.test(text)) {
    closeCommandPalette();
    return;
  }
  const prefix = text.slice(1).toLowerCase();
  commandMatches = SLASH_COMMANDS.filter((command) =>
    [command.name, ...(command.aliases ?? [])].some((name) =>
      name.startsWith(prefix),
    ),
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
  if (commandPalette.hidden) {
    return;
  }
  if (paletteMode === "file") {
    if (!fileMatches.length) {
      return;
    }
    fileSelection =
      (fileSelection + delta + fileMatches.length) % fileMatches.length;
    renderFilePalette();
    return;
  }
  if (commandMatches.length) {
    commandSelection =
      (commandSelection + delta + commandMatches.length) % commandMatches.length;
    renderCommandPalette();
  }
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

function completeSelectedFile(): void {
  const path = fileMatches[fileSelection];
  if (!path) {
    return;
  }
  const caret = promptInput.selectionStart ?? promptInput.value.length;
  const token = activeFileToken(promptInput.value, caret);
  if (!token) {
    closeCommandPalette();
    return;
  }
  promptInput.value =
    promptInput.value.slice(0, token.tokenStart) +
    "@" +
    path +
    " " +
    promptInput.value.slice(caret);
  const nextCaret = token.tokenStart + path.length + 2;
  promptInput.setSelectionRange(nextCaret, nextCaret);
  resizePrompt();
  closeCommandPalette();
  promptInput.focus();
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

function openConversationFind(query = ""): void {
  conversationFindInput.value = query;
  filterConversationMatches(query);
  renderConversationFindList();
  conversationFindModal.hidden = false;
  conversationFindInput.focus();
  conversationFindInput.select();
}

function closeConversationFind(): void {
  conversationFindModal.hidden = true;
  promptInput.focus();
}

function moveConversationFindSelection(delta: number): void {
  if (!conversationFindMatches.length) {
    return;
  }
  conversationFindSelection =
    (conversationFindSelection + delta + conversationFindMatches.length) %
    conversationFindMatches.length;
  renderConversationFindList();
}

function selectConversationFindMatch(index: number): boolean {
  const match = conversationFindMatches[index];
  if (!match) {
    return false;
  }
  const target = conversation.querySelector<HTMLElement>(
    `[data-message-id="${CSS.escape(match.messageId)}"]`,
  );
  if (!target) {
    return false;
  }
  conversation
    .querySelectorAll(".is-search-match")
    .forEach((element) => element.classList.remove("is-search-match"));
  target.classList.add("is-search-match");
  conversationFindThreadId = activeThread().id;
  conversationFindMessageId = match.messageId;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  conversationFindModal.hidden = true;
  return true;
}

function findConversationMatch(query: string): void {
  filterConversationMatches(query);
  if (!selectConversationFindMatch(0)) {
    conversationFindThreadId = "";
    conversationFindMessageId = "";
    applyConversationFindHighlight();
    showToast(query.trim() ? "当前会话没有匹配消息" : "当前会话还没有消息");
  }
}

async function refreshEffort(): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) {
      return;
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    currentEffort = snapshot.effort;
    currentPricing = snapshot.pricing;
    renderModelOptions(currentModelOptions, snapshot.model_id);
    runEffort.textContent = effortLabel(currentEffort);
    renderRunLedger();
  } catch {
    runEffort.textContent = "unavailable";
  }
}

async function setEffort(effort: SettingsSnapshot["effort"]): Promise<void> {
  try {
    const currentResponse = await fetch(`${API_BASE}/settings`);
    if (!currentResponse.ok) {
      throw new Error(await readError(currentResponse));
    }
    const current = (await currentResponse.json()) as SettingsSnapshot;
    const payload: SettingsPayload = {
      provider: current.provider,
      base_url: current.base_url,
      model_id: current.model_id,
      max_tokens: current.max_tokens,
      effort,
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
    showToast(`思考等级：${effortLabel(snapshot.effort)} · 本轮不变，下一轮生效`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error));
  }
}

async function cycleEffort(): Promise<void> {
  const order: SettingsSnapshot["effort"][] = [
    "",
    "low",
    "medium",
    "high",
    "max",
  ];
  const index = order.indexOf(currentEffort);
  await setEffort(index < 0 ? "low" : order[(index + 1) % order.length]);
}

async function respondToApproval(
  approvalId: string,
  approved: boolean,
  scope: ApprovalScope = "once",
  reason = "",
): Promise<void> {
  const buttons = Array.from(
    conversation.querySelectorAll<HTMLButtonElement>(
      `[data-approval-id="${CSS.escape(approvalId)}"]`,
    ),
  );
  for (const button of buttons) {
    button.disabled = true;
  }
  const reasonInputs = Array.from(
    conversation.querySelectorAll<HTMLInputElement>(
      `input.approval-reason[data-approval-id="${CSS.escape(approvalId)}"]`,
    ),
  );
  for (const input of reasonInputs) {
    input.disabled = true;
  }
  try {
    const response = await fetch(`${API_BASE}/approval`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        approval_id: approvalId,
        approved,
        scope,
        reason,
      }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    showToast(
      approved
        ? scope === "session"
          ? "本会话后续同名工具已免问"
          : "已允许工具执行"
        : "已拒绝工具执行",
    );
  } catch (error) {
    for (const button of buttons) {
      button.disabled = false;
    }
    for (const input of reasonInputs) {
      input.disabled = false;
    }
    showToast(error instanceof Error ? error.message : String(error));
  }
}

function markAutoApproved(thread: Thread, approvalIds: string[]): void {
  if (!approvalIds.length) {
    return;
  }
  const approved = new Set(approvalIds);
  for (const approvalId of approved) {
    autoApprovedApprovalIds.add(approvalId);
  }
  for (const message of thread.messages) {
    for (const part of message.parts) {
      if (
        part.type === "tool" &&
        part.tool.approvalId &&
        approved.has(part.tool.approvalId)
      ) {
        part.tool.state = "running";
        part.tool.approvalId = undefined;
      }
    }
  }
  thread.updatedAt = Date.now();
  persistState();
  if (thread.id === state.activeThreadId) {
    renderConversation(false);
    renderRunLedger();
  }
}

function syncRuntimeApprovalMode(thread: Thread, mode: ApprovalMode): void {
  const sessionId = thread.sessionId;
  if (!sessionId) {
    return;
  }
  approvalModeSync = approvalModeSync.then(async () => {
    try {
      const response = await fetch(`${API_BASE}/approval-mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, mode }),
      });
      if (!response.ok) {
        throw new Error(await readError(response));
      }
      const result = (await response.json()) as ApprovalModeResponse;
      markAutoApproved(thread, result.auto_approved);
    } catch (error) {
      showToast(
        `实时切换执行等级失败：${
          error instanceof Error ? error.message : String(error)
        }；下一轮仍使用 ${mode.toUpperCase()}`,
      );
    }
  });
}

function cycleApprovalMode(): void {
  const modes: ApprovalMode[] = ["approve", "auto", "yolo"];
  const thread = activeThread();
  const index = modes.indexOf(thread.approvalMode);
  setApprovalMode(modes[(index + 1) % modes.length]);
}

function setApprovalMode(mode: ApprovalMode): void {
  const thread = activeThread();
  thread.approvalMode = mode;
  thread.updatedAt = Date.now();
  persistState();
  renderThreadList();
  renderRunLedger();
  const descriptions: Record<ApprovalMode, string> = {
    approve: "每次工具调用都要 Accept",
    auto: "只读工具自动放行，bash 需确认",
    yolo: "全部工具直接执行",
  };
  showToast(
    `执行等级：${thread.approvalMode.toUpperCase()} · ${
      descriptions[thread.approvalMode]
    }${isThreadRunning(thread) ? " · 后续工具立即生效" : " · 下一轮生效"}`,
  );
  if (isThreadRunning(thread)) {
    syncRuntimeApprovalMode(thread, thread.approvalMode);
  }
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
      "TOOLS",
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
      title: "CAPABILITIES",
      body,
      error: false,
    };
  } catch (error) {
    commandReceipt = {
      threadId: thread.id,
      title: "CAPABILITIES",
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
    normal: "只看用户消息、回答和审批；隐藏思考块与工具明细。",
    thinking: "在 normal 基础上显示思考块；工具仍只保留审批。",
    verbose: "显示思考块、工具入参和工具出参。",
  };
  showCommandResult("TRANSCRIPT", `${mode.toUpperCase()}\n${descriptions[mode]}`);
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
        "MEMORY",
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
    showCommandResult("MEMORY", `已记下（下一圈请求生效）：\n${result.line}`);
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
      currentEffort = snapshot.effort;
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
    "STATUS",
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
  showToast(`已复制最近回答 · ${answer.text.length.toLocaleString()} chars`);
}

async function exportCurrentSession(): Promise<void> {
  const thread = activeThread();
  if (!thread.sessionId) {
    showCommandResult("EXPORT", "当前会话还没有落盘，不能导出。", true);
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
      "EXPORT",
      `SESSION ${shortSession(thread.sessionId)}\nMarkdown 下载已开始：${filename}`,
    );
  } catch (error) {
    showCommandResult(
      "EXPORT",
      error instanceof Error ? error.message : String(error),
      true,
    );
  }
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
        "TRANSCRIPT",
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
settingsCloseButton.addEventListener("click", closeSettings);
settingsCancelButton.addEventListener("click", closeSettings);
settingsModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-settings-close]")) {
    closeSettings();
  }
});
resumeCloseButton.addEventListener("click", closeResume);
resumeRenameButton.addEventListener("click", startResumeRename);
resumePinButton.addEventListener("click", () => {
  void toggleResumePin();
});
resumeActiveViewButton.addEventListener("click", () => {
  void switchResumeView(false);
});
resumeArchiveViewButton.addEventListener("click", () => {
  void switchResumeView(true);
});
resumeArchiveButton.addEventListener("click", () => {
  void toggleResumeArchive();
});
resumeDeleteButton.addEventListener("click", requestResumeDelete);
resumeDeleteCancelButton.addEventListener("click", cancelResumeDelete);
resumeDeleteConfirmButton.addEventListener("click", () => {
  void confirmResumeDelete();
});
resumeForkButton.addEventListener("click", () => {
  void startResumeFork();
});
resumeForkCancelButton.addEventListener("click", cancelResumeFork);
resumeForkInput.addEventListener("input", updateResumeForkPreview);
resumeForkForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveResumeFork();
});
resumeRenameCancel.addEventListener("click", cancelResumeRename);
resumeRenameForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveResumeRename();
});
resumeSearch.addEventListener("input", () => {
  resumeQuery = resumeSearch.value;
  resumeSelection = 0;
  void searchSessionContent(resumeQuery);
  filterResumeThreads();
  renderResumeList();
});
resumeModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-resume-close]")) {
    closeResume();
  }
});
conversationFindCloseButton.addEventListener("click", closeConversationFind);
conversationFindInput.addEventListener("input", () => {
  filterConversationMatches(conversationFindInput.value);
  renderConversationFindList();
});
conversationFindInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveConversationFindSelection(1);
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    moveConversationFindSelection(-1);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    selectConversationFindMatch(conversationFindSelection);
  }
});
conversationFindModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-conversation-find-close]")) {
    closeConversationFind();
  }
});
conversationFindList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-find-index]",
  );
  if (button?.dataset.findIndex === undefined) {
    return;
  }
  selectConversationFindMatch(Number(button.dataset.findIndex));
});
traceCloseButton.addEventListener("click", closeTrace);
traceRefreshButton.addEventListener("click", () => {
  void loadTrace();
});
traceModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-trace-close]")) {
    closeTrace();
  }
});
traceTree.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-span-id]",
  );
  if (button?.dataset.spanId) {
    selectTraceSpan(button.dataset.spanId);
  }
});
workspaceDiffCloseButton.addEventListener("click", closeWorkspaceDiff);
workspaceDiffRefreshButton.addEventListener("click", () => {
  void loadWorkspaceDiff();
});
workspaceDiffModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-workspace-diff-close]")) {
    closeWorkspaceDiff();
  }
});
stopButton.addEventListener("click", () => {
  void stopActiveTurn();
});
settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveSettings();
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
conversation.addEventListener("click", (event) => {
  const retryButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-retry-message-id]",
  );
  if (retryButton?.dataset.retryMessageId) {
    void retryFailedTurn(activeThread(), retryButton.dataset.retryMessageId);
    return;
  }
  const planButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-plan-id]",
  );
  if (planButton?.dataset.planId) {
    void handlePlanAction(planButton.dataset.planId, planButton.dataset.planAction ?? "");
    return;
  }
  const approvalButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-approval-id]",
  );
  if (approvalButton?.dataset.approvalId) {
    const gate = approvalButton.closest(".tool-approval-gate");
    const reasonInput = gate?.querySelector<HTMLInputElement>(
      "input.approval-reason",
    );
    void respondToApproval(
      approvalButton.dataset.approvalId,
      approvalButton.dataset.approved === "true",
      approvalButton.dataset.approvalScope === "session" ? "session" : "once",
      reasonInput?.value.trim() ?? "",
    );
    return;
  }
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-prompt]",
  );
  if (button?.dataset.prompt) {
    void submitPrompt(button.dataset.prompt);
  }
});
commandList.addEventListener("click", (event) => {
  if (paletteMode === "file") {
    const fileButton = (event.target as Element).closest<HTMLButtonElement>(
      "[data-file-index]",
    );
    if (fileButton?.dataset.fileIndex === undefined) {
      return;
    }
    fileSelection = Number(fileButton.dataset.fileIndex);
    completeSelectedFile();
    return;
  }
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-command-index]",
  );
  if (button?.dataset.commandIndex === undefined) {
    return;
  }
  commandSelection = Number(button.dataset.commandIndex);
  completeSelectedCommand();
});
composerAttachments.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-remove-image]",
  );
  if (button?.dataset.removeImage === undefined) {
    return;
  }
  const index = Number(button.dataset.removeImage);
  if (!Number.isInteger(index) || index < 0 || index >= pendingImages.length) {
    return;
  }
  pendingImages.splice(index, 1);
  renderPendingImages();
});
composer.addEventListener("dragover", (event) => {
  event.preventDefault();
  composerShell.classList.add("is-dragover");
});
composer.addEventListener("dragleave", (event) => {
  if (event.relatedTarget instanceof Node && composer.contains(event.relatedTarget)) {
    return;
  }
  composerShell.classList.remove("is-dragover");
});
composer.addEventListener("drop", (event) => {
  event.preventDefault();
  composerShell.classList.remove("is-dragover");
  void addImageFiles(event.dataTransfer?.files);
});
promptInput.addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
  if (!files.length) {
    return;
  }
  event.preventDefault();
  void addImageFiles(files);
});
resumeList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-resume-index]",
  );
  if (button?.dataset.resumeIndex === undefined) {
    return;
  }
  void selectResume(Number(button.dataset.resumeIndex));
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
    if (paletteMode === "file") {
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
      if (paletteMode === "file") {
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
  const conversationFindVisible = !conversationFindModal.hidden;
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
  const resumeVisible = !resumeModal.hidden;
  if (resumeVisible) {
    if (!resumeForkForm.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelResumeFork();
        resumeSearch.focus();
      }
      return;
    }
    if (!resumeRenameForm.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        cancelResumeRename();
        resumeSearch.focus();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        void saveResumeRename();
        return;
      }
      return;
    }
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
    if (event.key === "Enter") {
      event.preventDefault();
      void selectResume(resumeSelection);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeResume();
      promptInput.focus();
      return;
    }
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
    runElapsed.textContent = `${Date.now() - thread.runStartedAt}ms`;
  }
}, 100);

renderEverything();
resizePrompt();
void refreshEffort();
if (window.matchMedia("(max-width: 1180px)").matches) {
  runRailToggle.setAttribute("aria-pressed", "false");
}
