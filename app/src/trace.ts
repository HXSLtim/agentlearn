// trace 抽屉：只读 span 树、耗时与回答配对，自己接线自己的按钮。
import { API_BASE, readError } from "./api";
import { escapeHtml, renderMarkdown, shortSession } from "./format";
import { promptInput, requireElement } from "./dom";
import { activeThread } from "./main";
import type { TraceSpan, TraceResponse } from "./types";

const traceModal = requireElement<HTMLElement>("#trace-modal");
const traceCloseButton = requireElement<HTMLButtonElement>("#trace-close");
const traceRefreshButton = requireElement<HTMLButtonElement>("#trace-refresh");
const traceSummary = requireElement<HTMLElement>("#trace-summary");
const traceTree = requireElement<HTMLElement>("#trace-tree");
const traceCount = requireElement<HTMLElement>("#trace-count");
const traceDetail = requireElement<HTMLElement>("#trace-detail");
const traceSelectedState = requireElement<HTMLElement>("#trace-selected-state");

let traceSpans: TraceSpan[] = [];

let traceAnswers: Record<string, string> = {};

let traceSelectedSpanId = "";

let traceSessionId = "";

let traceLoadSequence = 0;

function traceDuration(span: TraceSpan): string {
  return span.duration_ms === null ? "open" : `${span.duration_ms}ms`;
}

function traceStatusClass(status: TraceSpan["status"]): string {
  return `is-${status}`;
}

function traceSpanHint(span: TraceSpan): string {
  if (span.name === "llm_request") {
    const ttft = span.data.ttft_ms;
    const model = span.data.model;
    return [model, ttft !== undefined && ttft !== null ? `ttft ${ttft}ms` : ""]
      .filter(Boolean)
      .join(" · ");
  }
  if (span.name === "tool_call") {
    return String(span.data.tool ?? "tool");
  }
  return "";
}

function traceTreeMarkup(): string {
  const ids = new Set(traceSpans.map((span) => span.span_id));
  const children = new Map<string, TraceSpan[]>();
  for (const span of traceSpans) {
    if (!span.parent_id || !ids.has(span.parent_id)) {
      continue;
    }
    const bucket = children.get(span.parent_id) ?? [];
    bucket.push(span);
    children.set(span.parent_id, bucket);
  }
  const roots = traceSpans.filter(
    (span) => !span.parent_id || !ids.has(span.parent_id),
  );
  const visited = new Set<string>();

  const renderSpan = (span: TraceSpan, depth: number): string => {
    if (visited.has(span.span_id)) {
      return "";
    }
    visited.add(span.span_id);
    const selected = span.span_id === traceSelectedSpanId;
    const hint = traceSpanHint(span);
    return `
      <button
        class="trace-node ${selected ? "is-selected" : ""}"
        type="button"
        role="treeitem"
        aria-selected="${selected}"
        data-span-id="${escapeHtml(span.span_id)}"
        style="--trace-depth: ${depth}"
      >
        <span class="trace-node-guide" aria-hidden="true"></span>
        <span class="trace-node-dot ${traceStatusClass(span.status)}" aria-hidden="true"></span>
        <span class="trace-node-main">
          <strong>${escapeHtml(span.name)}</strong>
          <small>${escapeHtml(span.status)} · ${traceDuration(span)}${hint ? ` · ${escapeHtml(hint)}` : ""}</small>
        </span>
      </button>
      ${(children.get(span.span_id) ?? [])
        .map((child) => renderSpan(child, depth + 1))
        .join("")}
    `;
  };

  return roots.map((span) => renderSpan(span, 0)).join("");
}

function traceField(label: string, value: unknown): string {
  const rendered =
    value === undefined || value === null || value === ""
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return `
    <div class="trace-field">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(rendered)}</code>
    </div>
  `;
}

function traceDetailMarkup(span: TraceSpan | undefined): string {
  if (!span) {
    return `
      <div class="trace-empty">
        <strong>未选择 span</strong>
        <p>从左侧选择一步，查看入参、耗时、状态和落盘回答。</p>
      </div>
    `;
  }
  const data = span.data;
  const answer = traceAnswers[span.span_id] ?? "";
  const known = new Set([
    "message",
    "model",
    "messages",
    "ttft_ms",
    "stop_reason",
    "effort",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_ratio",
    "tool",
    "input",
    "output",
  ]);
  const remaining = Object.fromEntries(
    Object.entries(data).filter(([key]) => !known.has(key)),
  );
  let body = "";
  if (span.name === "llm_request") {
    body = `
      <section class="trace-detail-section">
        <h3>模型请求</h3>
        <div class="trace-fields">
          ${traceField("model", data.model)}
          ${traceField("messages", data.messages)}
          ${traceField("ttft", data.ttft_ms === undefined ? "—" : `${data.ttft_ms}ms`)}
          ${traceField("stop", data.stop_reason)}
          ${traceField("effort", data.effort)}
          ${traceField("tokens", `in ${data.input_tokens ?? "—"} / out ${data.output_tokens ?? "—"}`)}
          ${traceField(
            "cache",
            `read ${data.cache_read_tokens ?? "—"} / write ${data.cache_write_tokens ?? "—"} / hit ${data.cache_hit_ratio ?? "—"}‰`,
          )}
        </div>
      </section>
    `;
  } else if (span.name === "tool_call") {
    body = `
      <section class="trace-detail-section">
        <h3>工具调用</h3>
        <div class="trace-fields">
          ${traceField("tool", data.tool)}
        </div>
      </section>
      <section class="trace-detail-section">
        <h3>输入</h3>
        <pre>${escapeHtml(JSON.stringify(data.input ?? {}, null, 2))}</pre>
      </section>
      <section class="trace-detail-section">
        <h3>输出</h3>
        <pre class="${span.status === "error" ? "is-error" : ""}">${escapeHtml(String(data.output ?? "—"))}</pre>
      </section>
    `;
  }
  if (Object.keys(remaining).length) {
    body += `
      <section class="trace-detail-section">
        <h3>Span 数据</h3>
        <pre>${escapeHtml(JSON.stringify(remaining, null, 2))}</pre>
      </section>
    `;
  }
  if (data.message) {
    body += `
      <section class="trace-detail-section">
        <h3>错误信息</h3>
        <pre class="is-error">${escapeHtml(String(data.message))}</pre>
      </section>
    `;
  }
  if (answer) {
    body += `
      <section class="trace-detail-section trace-answer">
        <h3>配对回答</h3>
        <div class="markdown">${renderMarkdown(answer)}</div>
      </section>
    `;
  }
  const localStart = new Date(span.start_time).toLocaleString("zh-CN");
  const localEnd = span.end_time
    ? new Date(span.end_time).toLocaleString("zh-CN")
    : "—";
  return `
    <article class="trace-span-card">
      <header>
        <div>
          <span class="eyebrow">SPAN / ${escapeHtml(span.span_id.slice(0, 12))}</span>
          <h2>${escapeHtml(span.name)}</h2>
        </div>
        <span class="trace-status ${traceStatusClass(span.status)}">${escapeHtml(span.status.toUpperCase())}</span>
      </header>
      <div class="trace-fields trace-span-meta">
        ${traceField("duration", traceDuration(span))}
        ${traceField("start", localStart)}
        ${traceField("end", localEnd)}
        ${traceField("parent", span.parent_id ?? "root")}
      </div>
      ${body || `<div class="trace-empty"><p>这段 span 没有附加数据。</p></div>`}
    </article>
  `;
}

function selectTraceSpan(spanId: string): void {
  traceSelectedSpanId = spanId;
  const span = traceSpans.find((candidate) => candidate.span_id === spanId);
  traceTree.innerHTML = traceTreeMarkup();
  traceDetail.innerHTML = traceDetailMarkup(span);
  traceSelectedState.textContent = span
    ? `${span.status.toUpperCase()} · ${traceDuration(span)}`
    : "等待选择";
}

function renderTrace(): void {
  traceCount.textContent = String(traceSpans.length);
  const turns = traceSpans.filter((span) => span.name === "turn").length;
  const errors = traceSpans.filter((span) => span.status === "error").length;
  const running = traceSpans.filter(
    (span) => span.status === "running",
  ).length;
  const wall = traceSpans
    .filter((span) => span.name === "turn")
    .reduce((total, span) => total + (span.duration_ms ?? 0), 0);
  traceSummary.innerHTML = `
    <span>会话 ${escapeHtml(shortSession(traceSessionId))}</span>
    <span>轮次 ${turns}</span>
    <span>span ${traceSpans.length}</span>
    <span>错误 ${errors}</span>
    <span>运行中 ${running}</span>
    <span>总耗时 ${wall}ms</span>
  `;
  traceTree.innerHTML = traceTreeMarkup();
  selectTraceSpan(traceSelectedSpanId);
  requestAnimationFrame(() => {
    traceTree
      .querySelector<HTMLElement>(".trace-node.is-selected")
      ?.scrollIntoView({ block: "center" });
  });
}

export async function loadTrace(): Promise<void> {
  const thread = activeThread();
  traceSessionId = thread.sessionId;
  if (!traceSessionId) {
    traceSpans = [];
    traceAnswers = {};
    traceSelectedSpanId = "";
    traceSummary.innerHTML = "<span>会话待定</span><span>暂无轨迹</span>";
    traceTree.innerHTML = "";
    traceDetail.innerHTML = `
      <div class="trace-empty">
        <strong>会话尚未落盘</strong>
        <p>先发送一轮消息，span 从第一轮开始写入。</p>
      </div>
    `;
    traceCount.textContent = "0";
    traceSelectedState.textContent = "等待选择";
    return;
  }
  const sequence = ++traceLoadSequence;
  traceRefreshButton.disabled = true;
  traceSummary.innerHTML = `<span>会话 ${escapeHtml(shortSession(traceSessionId))}</span><span>正在读取 SQLite + JSONL…</span>`;
  traceTree.innerHTML = `<div class="trace-loading">正在读取 span 树</div>`;
  traceDetail.innerHTML = `<div class="trace-loading">正在读取轨迹</div>`;
  traceSelectedState.textContent = "读取中";
  try {
    const response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(traceSessionId)}/trace`,
    );
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const trace = (await response.json()) as TraceResponse;
    if (sequence !== traceLoadSequence || traceModal.hidden) {
      return;
    }
    traceSpans = trace.spans;
    traceAnswers = trace.answers;
    const preferred =
      [...traceSpans].reverse().find(
        (span) => span.name === "llm_request" || span.name === "tool_call",
      ) ??
      traceSpans[0];
    traceSelectedSpanId = preferred?.span_id ?? "";
    renderTrace();
  } catch (error) {
    if (sequence !== traceLoadSequence) {
      return;
    }
    traceSpans = [];
    traceAnswers = {};
    traceTree.innerHTML = "";
    traceCount.textContent = "0";
    traceSummary.innerHTML = `<span>会话 ${escapeHtml(shortSession(traceSessionId))}</span><span>轨迹不可用</span>`;
    traceSelectedState.textContent = "错误";
    traceDetail.innerHTML = `
      <div class="trace-empty is-error">
        <strong>轨迹读取失败</strong>
        <p>${escapeHtml(error instanceof Error ? error.message : String(error))}</p>
      </div>
    `;
  } finally {
    if (sequence === traceLoadSequence) {
      traceRefreshButton.disabled = false;
    }
  }
}

const traceToggleButton = requireElement<HTMLButtonElement>("#trace-toggle");

export function openTrace(): void {
  const thread = activeThread();
  traceSessionId = thread.sessionId;
  traceModal.hidden = false;
  traceToggleButton.setAttribute("aria-pressed", "true");
  document.title = `AgentLearn · 轨迹 ${shortSession(thread.sessionId)}`;
  void loadTrace();
}

export function closeTrace(): void {
  traceLoadSequence += 1;
  traceModal.hidden = true;
  traceToggleButton.setAttribute("aria-pressed", "false");
  document.title = "AgentLearn";
  promptInput.focus();
}

traceCloseButton.addEventListener("click", closeTrace);

traceRefreshButton.addEventListener("click", () => {
  void loadTrace();
});

traceModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-trace-close]")) {
    closeTrace();
  }
});

traceTree.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>(
    "[data-span-id]",
  );
  if (button?.dataset.spanId) {
    selectTraceSpan(button.dataset.spanId);
  }
});
