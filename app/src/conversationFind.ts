// 当前会话内查找。命中高亮由对话重绘时回头调用。
import type { ConversationSearchMatch, UiMessage } from "./types";
import { escapeHtml } from "./format";
import {
  conversation,
  conversationFindCloseButton,
  conversationFindCount,
  conversationFindInput,
  conversationFindList,
  conversationFindModal,
  promptInput,
} from "./dom";
import { showToast } from "./toast";
import { activeThread } from "./state";

let conversationFindMatches: ConversationSearchMatch[] = [];
let conversationFindSelection = 0;
let conversationFindThreadId = "";
let conversationFindMessageId = "";

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
  conversationFindCount.textContent = `${conversationFindMatches.length} 条匹配`;
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
              <span>${match.role === "user" ? "你" : "智能体"}</span>
              <strong>${escapeHtml(match.snippet)}</strong>
            </button>
          `,
        )
        .join("")
    : `<div class="conversation-find-empty">没有匹配的已渲染消息</div>`;
  const selected = conversationFindList.querySelector(".is-selected");
  selected?.scrollIntoView({ block: "nearest" });
}

export function applyConversationFindHighlight(): void {
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

export function openConversationFind(query = ""): void {
  conversationFindInput.value = query;
  filterConversationMatches(query);
  renderConversationFindList();
  conversationFindModal.hidden = false;
  conversationFindInput.focus();
  conversationFindInput.select();
}

export function closeConversationFind(): void {
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

export function findConversationMatch(query: string): void {
  filterConversationMatches(query);
  if (!selectConversationFindMatch(0)) {
    conversationFindThreadId = "";
    conversationFindMessageId = "";
    applyConversationFindHighlight();
    showToast(query.trim() ? "当前会话没有匹配消息" : "当前会话还没有消息");
  }
}

export function isConversationFindOpen(): boolean {
  return !conversationFindModal.hidden;
}

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
