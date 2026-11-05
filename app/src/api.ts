// 对 server 的 HTTP 基址与 SSE/错误读取的公共小件。
import type { ChatEvent } from "./types";

export const API_BASE = "http://127.0.0.1:8000";

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
