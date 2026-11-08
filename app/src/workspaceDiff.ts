// 工作区 diff 抽屉：文件列表与 hunk 的只读渲染，自己接线自己的按钮。
import { API_BASE, readError } from "./api";
import { escapeHtml } from "./format";
import { promptInput, requireElement } from "./dom";
import { activeThread } from "./state";
import type { WorkspaceDiffResponse } from "./types";

const DIFF_STATUS_LABELS: Record<string, string> = {
  modified: "已修改",
  added: "新增",
  deleted: "删除",
  renamed: "重命名",
  copied: "复制",
  untracked: "未跟踪",
  typechange: "类型变更",
};

const workspaceDiffModal = requireElement<HTMLElement>("#workspace-diff-modal");
const workspaceDiffCloseButton = requireElement<HTMLButtonElement>("#workspace-diff-close");
const workspaceDiffRefreshButton = requireElement<HTMLButtonElement>("#workspace-diff-refresh");
const workspaceDiffSummary = requireElement<HTMLElement>("#workspace-diff-summary");
const workspaceDiffList = requireElement<HTMLElement>("#workspace-diff-list");

let workspaceDiff: WorkspaceDiffResponse | null = null;

let workspaceDiffLoadSequence = 0;
let workspaceDiffAbort: AbortController | null = null;

function renderWorkspaceDiff(): void {
  if (!workspaceDiff) {
    return;
  }
  workspaceDiffSummary.innerHTML = `
    <span>${escapeHtml(workspaceDiff.root)}</span>
    <span>已跟踪 ${workspaceDiff.files.filter((file) => file.status !== "untracked").length}</span>
    <span>未跟踪 ${workspaceDiff.untracked_count}${workspaceDiff.include_untracked ? "（已计入）" : "（未计入）"}</span>
    ${workspaceDiff.omitted_count ? `<span>未展开 ${workspaceDiff.omitted_count}</span>` : ""}
    ${workspaceDiff.partial ? "<span>读取未完成</span>" : ""}
  `;
  workspaceDiffList.innerHTML = workspaceDiff.files.length
    ? workspaceDiff.files
        .map(
          (file) => `
            <details class="workspace-diff-item is-${escapeHtml(file.status)}" ${file.omitted ? "" : "open"}>
              <summary>
                <span class="workspace-diff-status">${escapeHtml(DIFF_STATUS_LABELS[file.status] ?? file.status.toUpperCase())}</span>
                <span class="workspace-diff-file-heading">
                <strong>${escapeHtml(file.path)}</strong>
                ${file.old_path ? `<small>${escapeHtml(file.old_path)} → ${escapeHtml(file.path)}</small>` : ""}
                ${file.omitted ? "" : `<small class="workspace-diff-counts">+${file.additions} −${file.deletions}</small>`}
                ${file.binary ? '<small class="workspace-diff-binary">二进制</small>' : ""}
                ${file.truncated ? '<small class="workspace-diff-truncated">补丁已截断</small>' : ""}
                ${file.omitted ? '<small class="workspace-diff-truncated">未展开</small>' : ""}
                </span>
                <span class="workspace-diff-fold" aria-hidden="true"></span>
              </summary>
              <div class="workspace-diff-file-main">
                ${file.omitted ? "" : file.hunks.map((hunk) => `
                  <section class="workspace-diff-hunk">
                    <header>${escapeHtml(hunk.header)}</header>
                    <div class="workspace-diff-lines">
                      ${hunk.lines.map((line) => `
                        <div class="workspace-diff-line is-${escapeHtml(line.kind)}">
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

export async function loadWorkspaceDiff(): Promise<void> {
  workspaceDiffAbort?.abort();
  const abort = new AbortController();
  workspaceDiffAbort = abort;
  const timer = window.setTimeout(() => abort.abort(), 25_000);
  const sequence = ++workspaceDiffLoadSequence;
  workspaceDiffRefreshButton.disabled = true;
  workspaceDiffSummary.innerHTML = "<span>正在读取 git status…</span>";
  workspaceDiffList.innerHTML = `<div class="workspace-diff-empty">正在读取工作区</div>`;
  try {
    // diff 跟会话的工作目录走（D35）：会话在哪个项目里干活就看那个项目。
    // 新会话还没有 session id，服务端改走默认 sandbox，不再扫进程 cwd。
    const sessionId = activeThread().sessionId;
    const response = await fetch(
      `${API_BASE}/workspace/diff?include_untracked=true${
        sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ""
      }`,
      { signal: abort.signal },
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
    const aborted = error instanceof DOMException && error.name === "AbortError";
    workspaceDiff = null;
    workspaceDiffSummary.innerHTML = "<span>工作区不可用</span>";
    workspaceDiffList.innerHTML = `
      <div class="workspace-diff-empty is-error">
        <strong>${aborted ? "读取已停止" : "工作区读取失败"}</strong>
        <p>${escapeHtml(
          aborted
            ? "读取超时，已停止等待。可以关闭或重新加载。"
            : error instanceof Error
              ? error.message
              : String(error),
        )}</p>
      </div>
    `;
  } finally {
    window.clearTimeout(timer);
    if (sequence === workspaceDiffLoadSequence) {
      workspaceDiffRefreshButton.disabled = false;
    }
  }
}

const workspaceDiffToggleButton = requireElement<HTMLButtonElement>("#workspace-diff-toggle");

export function openWorkspaceDiff(): void {
  workspaceDiffModal.hidden = false;
  workspaceDiffToggleButton.setAttribute("aria-pressed", "true");
  void loadWorkspaceDiff();
}

export function closeWorkspaceDiff(): void {
  workspaceDiffLoadSequence += 1;
  workspaceDiffAbort?.abort();
  workspaceDiffModal.hidden = true;
  workspaceDiffToggleButton.setAttribute("aria-pressed", "false");
  promptInput.focus();
}

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
