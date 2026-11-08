// 功能抽屉只依赖 shell 能力契约，反向不再 import main.ts。
import type { Thread } from "./types";

export interface ShellBridge {
  fetchServerSessions(showToastOnFailure?: boolean, archived?: boolean): Promise<void>;
  hydrateThread(thread: Thread, force?: boolean): Promise<void>;
  renderConversation(forceBottom?: boolean): void;
  renderEverything(forceBottom?: boolean): void;
  renderRunLedger(): void;
  renderThreadList(): void;
  resizePrompt(): void;
  switchThread(threadId: string): void;
}

let bridge: ShellBridge | undefined;

export function setShellBridge(nextBridge: ShellBridge): void {
  bridge = nextBridge;
}

export function shellBridge(): ShellBridge {
  if (!bridge) {
    throw new Error("Application shell is not initialized");
  }
  return bridge;
}
