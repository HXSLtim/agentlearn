// index.html 的 DOM 句柄表：启动时一次性取齐，按需导入。

export function requireElement<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing element: ${selector}`);
  }
  return element;
}

export const appShell = requireElement<HTMLElement>("#app-shell");

export const threadList = requireElement<HTMLElement>("#thread-list");

export const threadCount = requireElement<HTMLElement>("#thread-count");

export const newThreadButton = requireElement<HTMLButtonElement>("#new-thread");

export const railCloseButton = requireElement<HTMLButtonElement>("#rail-close");

export const railOpenButton = requireElement<HTMLButtonElement>("#rail-open");

export const traceToggle = requireElement<HTMLButtonElement>("#trace-toggle");

export const runRailToggle = requireElement<HTMLButtonElement>("#run-rail-toggle");

export const runRail = requireElement<HTMLElement>("#run-rail");

export const scrim = requireElement<HTMLElement>("#scrim");

export const threadKicker = requireElement<HTMLElement>("#thread-kicker");

export const threadTitle = requireElement<HTMLElement>("#thread-title");

export const connectionPill = requireElement<HTMLElement>("#connection-pill");

export const conversation = requireElement<HTMLElement>("#conversation");

export const conversationScroll = requireElement<HTMLElement>("#conversation-scroll");

export const composer = requireElement<HTMLFormElement>("#composer");

export const promptInput = requireElement<HTMLTextAreaElement>("#prompt");

export const composerStatus = requireElement<HTMLElement>("#composer-status");

export const sendButton = requireElement<HTMLButtonElement>("#send");

export const sendLabel = requireElement<HTMLElement>("#send-label");

export const stopButton = requireElement<HTMLButtonElement>("#stop");

export const composerShell = requireElement<HTMLElement>("#composer-shell");

export const composerAttachments = requireElement<HTMLElement>("#composer-attachments");

export const commandPalette = requireElement<HTMLElement>("#command-palette");

export const commandList = requireElement<HTMLElement>("#command-list");

export const commandPaletteTitle = requireElement<HTMLElement>("#command-palette-title");

export const commandPaletteEnterAction = requireElement<HTMLElement>(
  "#command-palette-enter-action",
);

export const runState = requireElement<HTMLElement>("#run-state");

export const runStateCopy = requireElement<HTMLElement>("#run-state-copy");

export const runSession = requireElement<HTMLElement>("#run-session");

export const runTurns = requireElement<HTMLElement>("#run-turns");

export const runTools = requireElement<HTMLElement>("#run-tools");

export const runElapsed = requireElement<HTMLElement>("#run-elapsed");

export const runInputTokens = requireElement<HTMLElement>("#run-input-tokens");

export const runOutputTokens = requireElement<HTMLElement>("#run-output-tokens");

export const runCacheRead = requireElement<HTMLElement>("#run-cache-read");

export const runCacheWrite = requireElement<HTMLElement>("#run-cache-write");

export const runCacheHit = requireElement<HTMLElement>("#run-cache-hit");

export const runCost = requireElement<HTMLElement>("#run-cost");

export const runEffort = requireElement<HTMLElement>("#run-effort");

export const runApproval = requireElement<HTMLElement>("#run-approval");

export const runSteering = requireElement<HTMLElement>("#run-steering");

export const runStop = requireElement<HTMLElement>("#run-stop");

export const activityList = requireElement<HTMLElement>("#activity-list");

export const activityCount = requireElement<HTMLElement>("#activity-count");

export const runtimeLight = requireElement<HTMLElement>("#runtime-light");

export const ledgerLight = requireElement<HTMLElement>("#ledger-light");

export const toast = requireElement<HTMLElement>("#toast");

export const settingsModal = requireElement<HTMLElement>("#settings-modal");

export const settingsCloseButton = requireElement<HTMLButtonElement>("#settings-close");

export const settingsCancelButton = requireElement<HTMLButtonElement>("#settings-cancel");

export const settingsForm = requireElement<HTMLFormElement>("#settings-form");

export const settingsProvider = requireElement<HTMLSelectElement>("#settings-provider");

export const settingsModel = requireElement<HTMLSelectElement>("#settings-model");

export const settingsModelCount = requireElement<HTMLElement>(
  "#settings-model-count",
);

export const settingsBaseUrl = requireElement<HTMLInputElement>("#settings-base-url");

export const settingsMaxTokens = requireElement<HTMLInputElement>("#settings-max-tokens");

export const settingsEffort = requireElement<HTMLSelectElement>("#settings-effort");

export const settingsApiKey = requireElement<HTMLInputElement>("#settings-api-key");

export const settingsKeySource = requireElement<HTMLElement>("#settings-key-source");

export const settingsStatus = requireElement<HTMLElement>("#settings-status");

export const settingsSave = requireElement<HTMLButtonElement>("#settings-save");

export const settingsSaveLabel = requireElement<HTMLElement>("#settings-save-label");

export const resumeModal = requireElement<HTMLElement>("#resume-modal");

export const resumeCloseButton = requireElement<HTMLButtonElement>("#resume-close");

export const resumeList = requireElement<HTMLElement>("#resume-list");

export const resumeSummary = requireElement<HTMLElement>("#resume-summary");

export const resumeSearch = requireElement<HTMLInputElement>("#resume-search");

export const resumeRenameButton = requireElement<HTMLButtonElement>("#resume-rename");

export const resumePinButton = requireElement<HTMLButtonElement>("#resume-pin");

export const resumeActiveViewButton = requireElement<HTMLButtonElement>(
  "#resume-active-view",
);

export const resumeArchiveViewButton = requireElement<HTMLButtonElement>(
  "#resume-archive-view",
);

export const resumeArchiveButton = requireElement<HTMLButtonElement>("#resume-archive");

export const resumeDeleteButton = requireElement<HTMLButtonElement>("#resume-delete");

export const resumeDeleteConfirm = requireElement<HTMLElement>(
  "#resume-delete-confirm",
);

export const resumeDeleteTitle = requireElement<HTMLElement>("#resume-delete-title");

export const resumeDeleteCancelButton = requireElement<HTMLButtonElement>(
  "#resume-delete-cancel",
);

export const resumeDeleteConfirmButton = requireElement<HTMLButtonElement>(
  "#resume-delete-confirm-button",
);

export const resumeForkButton = requireElement<HTMLButtonElement>("#resume-fork");

export const resumeForkForm = requireElement<HTMLFormElement>("#resume-fork-form");

export const resumeForkSource = requireElement<HTMLElement>("#resume-fork-source");

export const resumeForkMax = requireElement<HTMLElement>("#resume-fork-max");

export const resumeForkInput = requireElement<HTMLInputElement>(
  "#resume-fork-through",
);

export const resumeForkPreview = requireElement<HTMLElement>(
  "#resume-fork-preview",
);

export const resumeForkCancelButton = requireElement<HTMLButtonElement>(
  "#resume-fork-cancel",
);

export const resumeForkSubmitButton = requireElement<HTMLButtonElement>(
  "#resume-fork-submit",
);

export const resumeRenameForm = requireElement<HTMLFormElement>("#resume-rename-form");

export const resumeRenameInput = requireElement<HTMLInputElement>("#resume-rename-input");

export const resumeRenameCancel = requireElement<HTMLButtonElement>("#resume-rename-cancel");

export const conversationFindModal = requireElement<HTMLElement>(
  "#conversation-find-modal",
);

export const conversationFindCloseButton = requireElement<HTMLButtonElement>(
  "#conversation-find-close",
);

export const conversationFindInput = requireElement<HTMLInputElement>(
  "#conversation-find-input",
);

export const conversationFindList = requireElement<HTMLElement>(
  "#conversation-find-list",
);

export const conversationFindCount = requireElement<HTMLElement>(
  "#conversation-find-count",
);

export const traceModal = requireElement<HTMLElement>("#trace-modal");

export const traceCloseButton = requireElement<HTMLButtonElement>("#trace-close");

export const traceRefreshButton = requireElement<HTMLButtonElement>("#trace-refresh");

export const traceSummary = requireElement<HTMLElement>("#trace-summary");

export const traceTree = requireElement<HTMLElement>("#trace-tree");

export const traceCount = requireElement<HTMLElement>("#trace-count");

export const traceDetail = requireElement<HTMLElement>("#trace-detail");

export const traceSelectedState = requireElement<HTMLElement>("#trace-selected-state");

export const workspaceDiffToggle = requireElement<HTMLButtonElement>("#workspace-diff-toggle");

export const workspaceDiffModal = requireElement<HTMLElement>("#workspace-diff-modal");

export const workspaceDiffCloseButton = requireElement<HTMLButtonElement>("#workspace-diff-close");

export const workspaceDiffRefreshButton = requireElement<HTMLButtonElement>("#workspace-diff-refresh");

export const workspaceDiffSummary = requireElement<HTMLElement>("#workspace-diff-summary");

export const workspaceDiffList = requireElement<HTMLElement>("#workspace-diff-list");
