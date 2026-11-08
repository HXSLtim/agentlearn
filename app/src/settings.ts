// 运行中的模型设置：读 /settings、写 config.ini、思考档位。
// 档位和计价被运行账本读，所以导出；账本本身还在 main，这里回头调它。
import type { ModelListResponse, SettingsPayload, SettingsSnapshot } from "./types";
import { API_BASE, readError } from "./api";
import { effortLabel } from "./format";
import {
  promptInput,
  runEffort,
  settingsApiKey,
  settingsBaseUrl,
  settingsCancelButton,
  settingsCloseButton,
  settingsEffort,
  settingsForm,
  settingsKeySource,
  settingsMaxTokens,
  settingsModal,
  settingsModel,
  settingsModelCount,
  settingsProvider,
  settingsSave,
  settingsSaveLabel,
  settingsStatus,
} from "./dom";
import { showToast } from "./toast";
import { shellBridge } from "./shellBridge";

export let currentEffort: SettingsSnapshot["effort"] = "";

export let currentPricing: SettingsSnapshot["pricing"] = null;

let currentModelOptions: string[] = [];

export function selectedModelId(): string {
  return settingsModel.value.trim();
}

export function setCurrentEffort(effort: SettingsSnapshot["effort"]): void {
  currentEffort = effort;
}

export function setSettingsStatus(
  message: string,
  tone: "idle" | "busy" | "ok" | "error" = "idle",
): void {
  settingsStatus.textContent = message;
  settingsStatus.className = `settings-status is-${tone}`;
}

export function renderModelOptions(models: string[], selectedModel: string): void {
  const options = [...models];
  if (selectedModel && !options.includes(selectedModel)) {
    options.unshift(selectedModel);
  }
  settingsModel.replaceChildren(
    ...options.map((modelId) => {
      const option = document.createElement("option");
      option.value = modelId;
      option.textContent = modelId;
      return option;
    }),
  );
  settingsModel.value = selectedModel;
  currentModelOptions = options;
  settingsModelCount.textContent = models.length
    ? `${models.length} 个模型`
    : "没有返回模型";
}

export function applySettings(snapshot: SettingsSnapshot): void {
  currentEffort = snapshot.effort;
  currentPricing = snapshot.pricing;
  settingsProvider.value = snapshot.provider;
  renderModelOptions(currentModelOptions, snapshot.model_id);
  settingsBaseUrl.value = snapshot.base_url;
  settingsMaxTokens.value = String(snapshot.max_tokens);
  settingsEffort.value = snapshot.effort;
  settingsApiKey.value = "";
  settingsApiKey.placeholder = snapshot.api_key_configured
    ? "留空保持当前密钥"
    : "输入 API 密钥，或写 ${ENV_VAR}";
  settingsKeySource.textContent = `当前密钥：${snapshot.api_key_source} · 留空不改`;
  runEffort.textContent = effortLabel(currentEffort);
}

export async function loadModelOptions(
  selectedModel: string,
  loadedLabel = "配置已读取",
): Promise<void> {
  settingsModel.disabled = true;
  settingsModelCount.textContent = "正在读取模型列表…";
  renderModelOptions([selectedModel], selectedModel);
  try {
    const response = await fetch(`${API_BASE}/models`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const parsed = (await response.json()) as ModelListResponse;
    renderModelOptions(parsed.models, selectedModel);
    setSettingsStatus(
      `${loadedLabel}；模型列表 ${parsed.models.length} 个可选项。`,
      "ok",
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : String(error);
    renderModelOptions([selectedModel], selectedModel);
    settingsModelCount.textContent = "模型列表不可用";
    setSettingsStatus(`${loadedLabel}；模型列表不可用：${message}`, "error");
  } finally {
    settingsModel.disabled = false;
  }
}

export function setSettingsBusy(busy: boolean): void {
  settingsSave.disabled = busy;
  settingsProvider.disabled = busy;
  settingsModel.disabled = busy;
  settingsBaseUrl.disabled = busy;
  settingsMaxTokens.disabled = busy;
  settingsEffort.disabled = busy;
  settingsApiKey.disabled = busy;
  settingsSaveLabel.textContent = busy ? "同步中…" : "保存并应用";
}

export async function loadSettings(): Promise<void> {
  setSettingsBusy(true);
  setSettingsStatus("正在读取本机配置…", "busy");
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    setSettingsStatus("配置已读取，修改后保存即可生效。", "ok");
    settingsModel.focus();
    await loadModelOptions(snapshot.model_id);
  } catch (error) {
    const message =
      error instanceof TypeError
        ? "连不上本地后端。先在 server/ 运行：uv run python main.py desktop"
        : error instanceof Error
          ? error.message
          : String(error);
    setSettingsStatus(message, "error");
    settingsProvider.focus();
  } finally {
    setSettingsBusy(false);
  }
}

export function openSettings(): void {
  settingsModal.hidden = false;
  settingsForm.reset();
  applySettings({
    provider: "anthropic",
    base_url: "",
    model_id: "",
    max_tokens: 64000,
    effort: "",
    api_key_configured: false,
    api_key_source: "正在读取",
    pricing: null,
  });
  void loadSettings();
}

export function closeSettings(): void {
  settingsModal.hidden = true;
  promptInput.focus();
}

export async function saveSettings(): Promise<void> {
  const maxTokens = Number.parseInt(settingsMaxTokens.value, 10);
  if (!Number.isFinite(maxTokens) || maxTokens <= 0) {
    setSettingsStatus("最大 token 必须是正整数。", "error");
    settingsMaxTokens.focus();
    return;
  }
  const apiKey = settingsApiKey.value.trim();
  let savedModelId = "";
  const payload: SettingsPayload = {
    provider: settingsProvider.value,
    base_url: settingsBaseUrl.value.trim(),
    model_id: settingsModel.value.trim(),
    max_tokens: maxTokens,
    effort: settingsEffort.value,
    api_key: apiKey || null,
  };
  setSettingsBusy(true);
  setSettingsStatus("正在写入 config.ini 并校验…", "busy");
  try {
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    savedModelId = snapshot.model_id;
    setSettingsStatus("已保存；下一次模型请求使用新配置。", "ok");
    showToast("模型设置已保存，下一轮生效");
  } catch (error) {
    setSettingsStatus(
      error instanceof Error ? error.message : String(error),
      "error",
    );
  } finally {
    setSettingsBusy(false);
  }
  if (savedModelId) {
    await loadModelOptions(savedModelId, "配置已保存");
  }
}

export async function quickSetModel(modelId: string): Promise<void> {
  try {
    const currentResponse = await fetch(`${API_BASE}/settings`);
    if (!currentResponse.ok) {
      throw new Error(await readError(currentResponse));
    }
    const current = (await currentResponse.json()) as SettingsSnapshot;
    const payload: SettingsPayload = {
      provider: current.provider,
      base_url: current.base_url,
      model_id: modelId,
      max_tokens: current.max_tokens,
      effort: current.effort,
      api_key: null,
    };
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    showToast(`模型已切换：${snapshot.model_id}，下一轮生效`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error));
  }
}

export async function refreshEffort(): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/settings`);
    if (!response.ok) {
      return;
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    currentEffort = snapshot.effort;
    currentPricing = snapshot.pricing;
    renderModelOptions(currentModelOptions, snapshot.model_id);
    runEffort.textContent = effortLabel(currentEffort);
    shellBridge().renderRunLedger();
  } catch {
    runEffort.textContent = "unavailable";
  }
}

export async function setEffort(effort: SettingsSnapshot["effort"]): Promise<void> {
  try {
    const currentResponse = await fetch(`${API_BASE}/settings`);
    if (!currentResponse.ok) {
      throw new Error(await readError(currentResponse));
    }
    const current = (await currentResponse.json()) as SettingsSnapshot;
    const payload: SettingsPayload = {
      provider: current.provider,
      base_url: current.base_url,
      model_id: current.model_id,
      max_tokens: current.max_tokens,
      effort,
      api_key: null,
    };
    const response = await fetch(`${API_BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const snapshot = (await response.json()) as SettingsSnapshot;
    applySettings(snapshot);
    showToast(`思考等级：${effortLabel(snapshot.effort)} · 本轮不变，下一轮生效`);
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error));
  }
}

export async function cycleEffort(): Promise<void> {
  const order: SettingsSnapshot["effort"][] = [
    "",
    "low",
    "medium",
    "high",
    "max",
  ];
  const index = order.indexOf(currentEffort);
  await setEffort(index < 0 ? "low" : order[(index + 1) % order.length]);
}

settingsCloseButton.addEventListener("click", closeSettings);
settingsCancelButton.addEventListener("click", closeSettings);
settingsModal.addEventListener("click", (event) => {
  const target = event.target as Element;
  if (target.closest("[data-settings-close]")) {
    closeSettings();
  }
});
settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveSettings();
});
