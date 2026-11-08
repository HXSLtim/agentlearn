// 对 server 的 HTTP 基址与 SSE/错误读取的公共小件。
import type { ChatEvent } from "./types";

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_BASE_STORAGE_KEY = "agentlearn.apiBase";

function normalizeApiBase(value: string, allowRemote = false): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null;
    }
    const loopback =
      url.hostname === "localhost" ||
      url.hostname === "127.0.0.1" ||
      url.hostname === "[::1]" ||
      url.hostname === "::1";
    if (!loopback && !allowRemote) {
      return null;
    }
    return url.origin;
  } catch {
    return null;
  }
}

function resolveApiBase(): string {
  const queryBase = new URLSearchParams(window.location.search).get("apiBase");
  if (queryBase) {
    const normalized = normalizeApiBase(queryBase);
    if (normalized) {
      try {
        localStorage.setItem(API_BASE_STORAGE_KEY, normalized);
      } catch {
        // 查询参数本身就是有效覆盖；存不进 localStorage 不影响本次会话。
      }
      return normalized;
    }
  }
  const envBase = import.meta.env.VITE_AGENTLEARN_API_BASE;
  if (envBase) {
    const normalized = normalizeApiBase(envBase, true);
    if (normalized) {
      return normalized;
    }
  }
  try {
    const storedBase = localStorage.getItem(API_BASE_STORAGE_KEY);
    if (storedBase) {
      return normalizeApiBase(storedBase) ?? DEFAULT_API_BASE;
    }
  } catch {
    // 无 localStorage（或被禁用）时退回默认本机地址。
  }
  return DEFAULT_API_BASE;
}

export const API_BASE = resolveApiBase();

export async function consumeSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const payload = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (payload) {
        onEvent(JSON.parse(payload) as ChatEvent);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export async function readError(response: Response): Promise<string> {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail || raw;
  } catch {
    return raw || `HTTP ${response.status}`;
  }
}
