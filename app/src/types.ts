// 协议与 UI 的纯类型定义：服务端响应形状、消息块、会话与设置快照。

export type ApprovalMode = "approve" | "auto" | "yolo";

export type SteeringMode = "queue" | "cooperative" | "preemptive";

export type ToolState = "waiting" | "running" | "done";

export type TranscriptMode = "normal" | "thinking" | "verbose";

export interface PlanEvent {
  id: string;
  threadId: string;
  sessionId: string;
  text: string;
  confirmed: boolean;
  discarded: boolean;
  executed: boolean;
}

export interface ImageAttachment {
  media_type: string;
  data: string;
}

export interface ToolEvent {
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

export interface TextPart {
  type: "text";
  text: string;
}

export interface ThinkingPart {
  type: "thinking";
  text: string;
}

export interface ToolPart {
  type: "tool";
  tool: ToolEvent;
}

export interface PlanPart {
  type: "plan";
  plan: PlanEvent;
}

export type AssistantPart = TextPart | ThinkingPart | ToolPart | PlanPart;

export interface UiMessage {
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

export interface TurnUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_ratio: number;
}

export interface ContextWarning {
  estimate_tokens: number;
  budget_tokens: number;
  ratio: number;
}

export interface Thread {
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
  // 会话工作目录（D35）：空字符串 = 默认 data/sandbox。跟会话走，
  // 不是某一轮的参数；服务端 sessions.workdir 是真值来源。
  workdir: string;
  usage?: TurnUsage;
  contextWarning?: ContextWarning;
  serverTurns?: number;
  runStartedAt?: number;
  stopping?: boolean;
  messagesLoaded?: boolean;
  messages: UiMessage[];
}

export interface PersistedState {
  version: 1;
  activeThreadId: string;
  threads: Thread[];
}

export interface SettingsSnapshot {
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

export interface SettingsPayload {
  provider: string;
  base_url: string;
  model_id: string;
  max_tokens: number;
  effort: string;
  api_key: string | null;
}

export interface ModelListResponse {
  provider: "anthropic" | "openai";
  models: string[];
}

export interface CapabilitiesResponse {
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

export type ApprovalScope = "once" | "session";

export interface ChatEvent {
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

export interface ApprovalModeResponse {
  ok: boolean;
  active: boolean;
  mode: ApprovalMode;
  auto_approved: string[];
}

export interface PlanConfirmResponse {
  ok: boolean;
  plan_id: string;
  confirmed: boolean;
}

export interface ServerSession {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  turns: number;
  stop_reason: string;
  pinned: boolean;
  archived: boolean;
  workdir: string;
}

export interface SessionForkResponse {
  ok: boolean;
  session_id: string;
  copied: number;
}

export interface SessionSearchHit {
  session_id: string;
  title: string;
  message_index: number;
  role: string;
  snippet: string;
}

export interface SessionSearchResponse {
  q: string;
  hits: SessionSearchHit[];
}

export interface ConversationSearchMatch {
  messageId: string;
  role: UiMessage["role"];
  snippet: string;
}

export interface ServerContentBlock {
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

export interface ServerMessage {
  role: "user" | "assistant";
  content: ServerContentBlock[];
  /** JSONL 落盘时间（秒）；老数据没有这个键。 */
  ts?: number | null;
}

export interface TraceSpan {
  span_id: string;
  parent_id: string | null;
  name: string;
  start_time: string;
  end_time: string | null;
  status: "running" | "ok" | "error" | "cancelled";
  data: Record<string, unknown>;
  duration_ms: number | null;
}

export interface TraceResponse {
  spans: TraceSpan[];
  answers: Record<string, string>;
}

export interface WorkspaceDiffFile {
  path: string;
  old_path: string | null;
  status: string;
  binary: boolean;
  truncated: boolean;
  omitted?: boolean;
  hunks: WorkspaceDiffHunk[];
  additions: number;
  deletions: number;
}

export interface WorkspaceDiffHunk {
  header: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: WorkspaceDiffLine[];
}

export interface WorkspaceDiffLine {
  kind: "context" | "add" | "delete" | "meta";
  old_line: number | null;
  new_line: number | null;
  text: string;
}

export interface WorkspaceDiffResponse {
  root: string;
  files: WorkspaceDiffFile[];
  untracked_count: number;
  include_untracked: boolean;
  partial?: boolean;
  omitted_count?: number;
}

export interface WorkspacePathsResponse {
  root: string;
  prefix: string;
  paths: string[];
  total: number;
  limited: boolean;
}

export interface StopResponse {
  ok: boolean;
  active: boolean;
  cancelled: boolean;
}

export interface ActivityItem {
  key: string;
  tag: string;
  title: string;
  detail: string;
  status: string;
}

export type PaletteMode = "command" | "file";

export interface StreamContext {
  thread: Thread;
  assistant: UiMessage;
  userMessage?: UiMessage;
}
