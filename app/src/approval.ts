// 执行等级与单次审批。按钮在对话流里，这里自己听点击。
import type { ApprovalMode, ApprovalModeResponse, ApprovalScope, Thread } from "./types";
import { API_BASE, readError } from "./api";
import { approvalLabel } from "./format";
import { conversation } from "./dom";
import { showToast } from "./toast";
import {
  activeThread,
  isThreadRunning,
  persistState,
  state,
} from "./state";
import { shellBridge } from "./shellBridge";

export const autoApprovedApprovalIds = new Set<string>();

let approvalModeSync: Promise<void> = Promise.resolve();

export async function respondToApproval(
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

export function markAutoApproved(thread: Thread, approvalIds: string[]): void {
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
    shellBridge().renderConversation(false);
    shellBridge().renderRunLedger();
  }
}

export function syncRuntimeApprovalMode(thread: Thread, mode: ApprovalMode): void {
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
        }；下一轮仍使用 ${approvalLabel(mode)}`,
      );
    }
  });
}

export function cycleApprovalMode(): void {
  const modes: ApprovalMode[] = ["approve", "auto", "yolo"];
  const thread = activeThread();
  const index = modes.indexOf(thread.approvalMode);
  setApprovalMode(modes[(index + 1) % modes.length]);
}

export function setApprovalMode(mode: ApprovalMode): void {
  const thread = activeThread();
  thread.approvalMode = mode;
  thread.updatedAt = Date.now();
  persistState();
  shellBridge().renderThreadList();
  shellBridge().renderRunLedger();
  const descriptions: Record<ApprovalMode, string> = {
    approve: "每次工具调用都要人工接受",
    auto: "只读工具自动放行，bash 需确认",
    yolo: "全部工具直接执行",
  };
  showToast(
    `执行等级：${approvalLabel(thread.approvalMode)} · ${
      descriptions[thread.approvalMode]
    }${isThreadRunning(thread) ? " · 后续工具立即生效" : " · 下一轮生效"}`,
  );
  if (isThreadRunning(thread)) {
    syncRuntimeApprovalMode(thread, thread.approvalMode);
  }
}

conversation.addEventListener("click", (event) => {
  const approvalButton = (event.target as Element).closest<HTMLButtonElement>(
    "[data-approval-id]",
  );
  if (!approvalButton?.dataset.approvalId) {
    return;
  }
  event.stopImmediatePropagation();
  const gate = approvalButton.closest(".tool-approval-gate");
  const reasonInput = gate?.querySelector<HTMLInputElement>("input.approval-reason");
  void respondToApproval(
    approvalButton.dataset.approvalId,
    approvalButton.dataset.approved === "true",
    approvalButton.dataset.approvalScope === "session" ? "session" : "once",
    reasonInput?.value.trim() ?? "",
  );
});
