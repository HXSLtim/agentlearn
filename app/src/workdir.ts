// 会话工作目录（D35）：跟会话走，不跟某一轮。设一次，这个会话之后
// 每一轮的 bash / 后台任务都从那里起跑；换会话就是换目录。
// 自带状态与 DOM 句柄、自接线自己的按钮——和 settings / resume 同一套
// 模块规矩；从 main 取活绑定（activeThread）在回调里用。
import { API_BASE, readError } from "./api";
import { activeThread } from "./state";
import { shellBridge } from "./shellBridge";
import { requireElement, promptInput } from "./dom";
import { showToast } from "./toast";

const workdirButton = requireElement<HTMLButtonElement>("#workdir-button");
const workdirModal = requireElement<HTMLElement>("#workdir-modal");
const workdirInput = requireElement<HTMLInputElement>("#workdir-input");
const workdirStatus = requireElement<HTMLElement>("#workdir-status");
const workdirForm = requireElement<HTMLFormElement>("#workdir-form");
const workdirResetButton = requireElement<HTMLButtonElement>("#workdir-reset");
const workdirCloseButton = requireElement<HTMLButtonElement>("#workdir-close");

// 界面显示的短名：默认就是「sandbox」；设过的目录取最后两段，
// 一般是「父目录/项目名」，足够认出是哪个项目，整条路径放 title。
export function workdirLabel(workdir: string): string {
  if (!workdir) {
    return "sandbox";
  }
  const parts = workdir.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || workdir;
}

export function renderWorkdir(): void {
  const thread = activeThread();
  workdirButton.textContent = `目录 ${workdirLabel(thread.workdir)}`;
  workdirButton.title = thread.workdir
    ? `会话工作目录：${thread.workdir}（点击修改，整个会话生效）`
    : "会话工作目录：默认 data/sandbox（点击修改）";
}

function openWorkdir(): void {
  workdirInput.value = activeThread().workdir;
  workdirStatus.textContent = "";
  workdirModal.hidden = false;
  workdirInput.focus();
  workdirInput.select();
}

export function isWorkdirOpen(): boolean {
  return !workdirModal.hidden;
}

function closeWorkdir(): void {
  workdirModal.hidden = true;
  promptInput.focus();
}

async function saveWorkdir(raw: string): Promise<void> {
  const thread = activeThread();
  const cleaned = raw.trim();
  // 本地先记住（localStorage 随 schedulePersist 落盘）。
  thread.workdir = cleaned;
  shellBridge().renderEverything();
  if (!thread.sessionId) {
    // 会话行还没建（首条消息没发过）：PATCH 没有落脚点，
    // 这个值随下一次 /chat 的 workdir 字段落上去。
    closeWorkdir();
    showToast(cleaned ? `工作目录已设：${cleaned}（新会话生效）` : "工作目录已恢复默认（新会话生效）");
    return;
  }
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(thread.sessionId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workdir: cleaned }),
      },
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    closeWorkdir();
    showToast(cleaned ? `工作目录已设：${cleaned}` : "工作目录已恢复默认 sandbox");
  } catch (error) {
    // 服务端拒了（路径不存在之类）：本地值保留但如实报告，用户改对了再存。
    workdirStatus.textContent =
      error instanceof Error ? error.message : String(error);
  }
}

workdirButton.addEventListener("click", openWorkdir);
workdirForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveWorkdir(workdirInput.value);
});
workdirCloseButton.addEventListener("click", closeWorkdir);
workdirResetButton.addEventListener("click", () => {
  void saveWorkdir("");
});
workdirModal.addEventListener("click", (event) => {
  if ((event.target as Element).closest("[data-workdir-close]")) {
    closeWorkdir();
  }
});
document.addEventListener("keydown", (event) => {
  if (workdirModal.hidden || event.key !== "Escape") {
    return;
  }
  // 模块自己的键：比 main 的总分发先注册、先收到，关掉自己即可。
  event.preventDefault();
  event.stopImmediatePropagation();
  closeWorkdir();
});
