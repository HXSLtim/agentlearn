// 斜杠命令面板和 @ 文件补全。方向键仍由输入框的按键处理函数来调。
import type { WorkspacePathsResponse } from "./types";
import { API_BASE, readError } from "./api";
import { escapeHtml } from "./format";
import { SLASH_COMMANDS, type SlashCommand } from "./slashCommands";
import {
  commandList,
  commandPalette,
  commandPaletteEnterAction,
  commandPaletteTitle,
  promptInput,
} from "./dom";
import { resizePrompt, activeThread } from "./main";

let commandMatches: SlashCommand[] = [...SLASH_COMMANDS];
let commandSelection = 0;
let paletteMode: "command" | "file" = "command";
let fileMatches: string[] = [];
let fileSelection = 0;
let fileCompletionTimer: number | undefined;
let fileCompletionSequence = 0;

export function resetCommandSelection(): void {
  commandSelection = 0;
}

export function isCommandPaletteOpen(): boolean {
  return !commandPalette.hidden;
}

export function isFilePalette(): boolean {
  return paletteMode === "file";
}

export function chooseCommand(index: number): void {
  commandSelection = index;
  completeSelectedCommand();
}

export function chooseFile(index: number): void {
  fileSelection = index;
  completeSelectedFile();
}

export function closeCommandPalette(): void {
  fileCompletionSequence += 1;
  if (fileCompletionTimer !== undefined) {
    window.clearTimeout(fileCompletionTimer);
    fileCompletionTimer = undefined;
  }
  commandPalette.hidden = true;
  paletteMode = "command";
  fileMatches = [];
  fileSelection = 0;
  commandPaletteTitle.textContent = "命令索引";
  commandPaletteEnterAction.textContent = "Enter 执行";
  commandPalette.ariaLabel = "命令面板";
}

function renderCommandPalette(): void {
  commandPaletteTitle.textContent = "命令索引";
  commandPaletteEnterAction.textContent = "Enter 执行";
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
  commandPaletteTitle.textContent = "文件路径";
  commandPaletteEnterAction.textContent = "Enter 补全";
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
            <span>文件路径</span>
          </button>
        `,
        )
        .join("")
    : `<div class="command-group">没有匹配路径</div>`;
}

function scheduleFilePalette(token: { query: string; tokenStart: number }): void {
  const sequence = ++fileCompletionSequence;
  paletteMode = "file";
  fileMatches = [];
  fileSelection = 0;
  renderFilePalette();
  commandList.innerHTML = `<div class="command-group">正在读取路径…</div>`;
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
    // @file 补全跟会话的工作目录走（D35）：补的是会话所在项目的文件。
    const sessionId = activeThread().sessionId;
    const response = await fetch(
      `${API_BASE}/workspace/paths?q=${encodeURIComponent(token.query)}&limit=50${
        sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ""
      }`,
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
    commandList.innerHTML = `<div class="command-group">路径补全不可用</div>`;
  }
}

export function updateCommandPalette(): void {
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
  // 按组连续排列：组内保持原表顺序，组间按首次出现排。
  // 否则跨组的命令交替出现时，面板里会重复冒出同名分组头。
  const groupRank = new Map<string, number>();
  for (const command of SLASH_COMMANDS) {
    if (!groupRank.has(command.group)) {
      groupRank.set(command.group, groupRank.size);
    }
  }
  commandMatches = SLASH_COMMANDS.filter((command) =>
    [command.name, ...(command.aliases ?? [])].some((name) =>
      name.startsWith(prefix),
    ),
  ).sort((a, b) => (groupRank.get(a.group) ?? 0) - (groupRank.get(b.group) ?? 0));
  if (!commandMatches.length) {
    closeCommandPalette();
    return;
  }
  commandSelection = Math.min(commandSelection, commandMatches.length - 1);
  renderCommandPalette();
  commandPalette.hidden = false;
}

export function moveCommandSelection(delta: number): void {
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

export function completeSelectedCommand(): void {
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

export function completeSelectedFile(): void {
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


commandList.addEventListener("click", (event) => {
  if (isFilePalette()) {
    const fileButton = (event.target as Element).closest<HTMLButtonElement>(
      "[data-file-index]",
    );
    if (fileButton?.dataset.fileIndex === undefined) {
      return;
    }
    chooseFile(Number(fileButton.dataset.fileIndex));
    return;
  }
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-command-index]",
  );
  if (button?.dataset.commandIndex === undefined) {
    return;
  }
  chooseCommand(Number(button.dataset.commandIndex));
});
