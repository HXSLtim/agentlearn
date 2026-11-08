// 续接：会话列表、改名、置顶、归档、删除、分叉。自己接线自己的弹层。
import type {
  ServerMessage,
  ServerSession,
  SessionForkResponse,
  SessionSearchHit,
  SessionSearchResponse,
  Thread,
} from "./types";
import { API_BASE, readError } from "./api";
import { escapeHtml, relativeTime, shortSession } from "./format";
import {
  promptInput,
  resumeActiveViewButton,
  resumeArchiveButton,
  resumeArchiveViewButton,
  resumeCloseButton,
  resumeDeleteButton,
  resumeDeleteCancelButton,
  resumeDeleteConfirm,
  resumeDeleteConfirmButton,
  resumeDeleteTitle,
  resumeForkButton,
  resumeForkCancelButton,
  resumeForkForm,
  resumeForkInput,
  resumeForkMax,
  resumeForkPreview,
  resumeForkSource,
  resumeForkSubmitButton,
  resumeList,
  resumeModal,
  resumePinButton,
  resumeRenameButton,
  resumeRenameCancel,
  resumeRenameForm,
  resumeRenameInput,
  resumeSearch,
  resumeSummary,
} from "./dom";
import { showToast } from "./toast";
import {
  activeThread,
  createThread,
  persistState,
  state,
} from "./state";
import { shellBridge } from "./shellBridge";

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

export function isResumeOpen(): boolean {
  return !resumeModal.hidden;
}

export function findResumeThread(argument: string): Thread | null {
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
  resumePinButton.textContent = selectedThread?.pinned ? "取消置顶" : "置顶";
  resumeArchiveButton.disabled = !selectedThread?.sessionId;
  resumeArchiveButton.textContent = selectedThread?.archived
    ? "取消归档"
    : "归档";
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
            thread.sessionId ? shortSession(thread.sessionId) : "待定",
          )}</span>
          <span class="resume-item-turns">${turns} 轮</span>
          ${current ? '<span class="resume-current">当前</span>' : ""}
          <strong>${escapeHtml(thread.title)}</strong>
          <small>${thread.pinned ? "置顶 · " : ""}${escapeHtml(thread.model || "模型未知")} · ${escapeHtml(
            thread.stopReason || "未完成一轮",
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

export async function openResume(): Promise<void> {
  resumeArchived = false;
  await shellBridge().fetchServerSessions(true);
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
  await shellBridge().fetchServerSessions(true, archived);
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
    shellBridge().renderEverything();
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
    shellBridge().renderEverything();
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
    shellBridge().renderEverything();
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
    shellBridge().renderEverything();
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
  return `${message.role === "user" ? "你" : "智能体"}: ${text || "[无文本块]"}`;
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
    resumeForkMax.textContent = `共 ${messages.length} 条消息`;
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
    await shellBridge().fetchServerSessions(false);
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
    shellBridge().switchThread(created.id);
    showToast(`已 fork 前 ${result.copied} 条消息`);
  } catch (error) {
    showToast(
      `fork 失败：${error instanceof Error ? error.message : String(error)}`,
    );
  } finally {
    resumeForkSubmitButton.disabled = false;
  }
}

export function closeResume(): void {
  resumeModal.hidden = true;
  cancelResumeRename();
  cancelResumeDelete();
  cancelResumeFork();
}

export function moveResumeSelection(delta: number): void {
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

export async function selectResume(index: number): Promise<void> {
  const thread = resumeMatches[index];
  if (!thread) {
    return;
  }
  const shell = shellBridge();
  await shell.hydrateThread(thread, true);
  shell.switchThread(thread.id);
  showToast(`已续接：${thread.title}`);
}

export function handleResumeKeydown(event: KeyboardEvent): boolean {
  if (!isResumeOpen()) {
    return false;
  }
  if (!resumeForkForm.hidden) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelResumeFork();
      resumeSearch.focus();
    }
    return true;
  }
  if (!resumeRenameForm.hidden) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelResumeRename();
      resumeSearch.focus();
      return true;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      void saveResumeRename();
      return true;
    }
    return true;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveResumeSelection(1);
    return true;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    moveResumeSelection(-1);
    return true;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    void selectResume(resumeSelection);
    return true;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeResume();
    promptInput.focus();
    return true;
  }
  return true;
}

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

resumeList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-resume-index]",
  );
  if (button?.dataset.resumeIndex === undefined) {
    return;
  }
  void selectResume(Number(button.dataset.resumeIndex));
});
