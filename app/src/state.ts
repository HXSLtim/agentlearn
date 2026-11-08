// 会话本地状态：只管线程状态与 localStorage，不认识 DOM 和渲染。
import type {
  ApprovalMode,
  AssistantPart,
  ImageAttachment,
  PersistedState,
  SteeringMode,
  Thread,
  UiMessage,
} from "./types";
import { parseImage } from "./composerImages";
import { isAutoThreadTitle, threadTitleFor } from "./format";
import { showToast } from "./toast";

const STORAGE_KEY = "agentlearn.desktop.v1";

export let state = loadState();

export function createId(): string {
  return crypto.randomUUID();
}

export function createThread(): Thread {
  const now = Date.now();
  return {
    id: createId(),
    sessionId: "",
    title: "新会话",
    createdAt: now,
    updatedAt: now,
    stopReason: "",
    approvalMode: "approve",
    steeringMode: "queue",
    model: "",
    pinned: false,
    archived: false,
    workdir: "",
    messagesLoaded: true,
    messages: [],
  };
}

function normalizeStoredMessage(message: UiMessage): UiMessage {
  const parts: AssistantPart[] = Array.isArray(message.parts)
    ? message.parts
    : [];
  const images = (message.images ?? [])
    .map(parseImage)
    .filter((image): image is ImageAttachment => Boolean(image));
  if (message.thinking && !parts.some((part) => part.type === "thinking")) {
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
          title: (() => {
            const firstUserText = Array.isArray(thread.messages)
              ? thread.messages.find(
                  (message) =>
                    message.role === "user" && (message.text ?? "").trim(),
                )?.text
              : undefined;
            if (isAutoThreadTitle(thread.title ?? "")) {
              return firstUserText
                ? threadTitleFor(firstUserText)
                : "新会话";
            }
            return thread.title;
          })(),
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
          workdir: typeof thread.workdir === "string" ? thread.workdir : "",
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

export function activeThread(): Thread {
  return (
    state.threads.find((thread) => thread.id === state.activeThreadId) ??
    state.threads[0]
  );
}

export function persistState(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    showToast("本地会话缓存写入失败");
  }
}

export function runningAssistant(thread: Thread): UiMessage | undefined {
  const running = [...thread.messages]
    .reverse()
    .filter((message) => message.role === "assistant" && message.streaming);
  return running.find((message) => !message.queued) ?? running[0];
}

export function isThreadRunning(thread: Thread): boolean {
  return runningAssistant(thread) !== undefined;
}
