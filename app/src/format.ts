// 纯格式化助手：转义、缩写、时间与标签。无状态，随便引用。
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { SettingsSnapshot } from "./types";

marked.setOptions({ gfm: true, breaks: true });

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function shortSession(sessionId: string): string {
  return sessionId ? sessionId.slice(0, 8) : "PENDING";
}

export function effortLabel(effort: SettingsSnapshot["effort"]): string {
  return effort || "default (omit)";
}

export function threadTitleFor(prompt: string): string {
  const singleLine = prompt.replace(/\s+/g, " ").trim();
  return singleLine.length > 42 ? `${singleLine.slice(0, 42)}…` : singleLine;
}

export function relativeTime(timestamp: number): string {
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (Math.abs(seconds) < 60) {
    return formatter.format(seconds, "second");
  }
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) {
    return formatter.format(minutes, "minute");
  }
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) {
    return formatter.format(hours, "hour");
  }
  return formatter.format(Math.round(hours / 24), "day");
}




export function renderMarkdown(value: string): string {
  const parsed = marked.parse(value) as string;
  return DOMPurify.sanitize(parsed, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target"],
  });
}
