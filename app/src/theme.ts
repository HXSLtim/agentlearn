export type ThemeName = "auto" | "light" | "dark" | "custom";

const THEME_STORAGE_KEY = "agentlearn.theme";
const THEME_TOKEN_STORAGE_KEY = "agentlearn.theme.tokens";
const THEME_NAMES = new Set<ThemeName>(["auto", "light", "dark", "custom"]);
const COLOR_TOKENS = new Set([
  "--void",
  "--black",
  "--panel",
  "--panel-raised",
  "--line",
  "--line-strong",
  "--text",
  "--text-soft",
  "--muted",
  "--dim",
  "--accent",
  "--accent-strong",
  "--accent-hover",
  "--on-accent",
  "--accent-dim",
  "--accent-soft",
  "--red",
  "--red-soft",
  "--red-line",
  "--success",
  "--success-soft",
  "--stage-grid-line",
  "--inverse-line",
  "--segment-track-bg",
  "--segment-active-bg",
  "--thread-scrim",
  "--overlay-scrim",
  "--pill-bg",
]);

declare global {
  interface Window {
    AgentLearnTheme: {
      preset: (theme: ThemeName) => void;
      set: (token: string, value: string | null) => void;
      reset: () => void;
    };
  }
}

function normalizeTheme(value: string | null): ThemeName {
  return value && THEME_NAMES.has(value as ThemeName) ? (value as ThemeName) : "auto";
}

function isColor(value: string): boolean {
  return CSS.supports("color", value);
}

function readTokens(): Record<string, string> {
  try {
    const raw = localStorage.getItem(THEME_TOKEN_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, string>;
    return Object.fromEntries(
      Object.entries(parsed).filter(([token, value]) => COLOR_TOKENS.has(token) && isColor(value)),
    );
  } catch {
    return {};
  }
}

function writeTokens(tokens: Record<string, string>): void {
  try {
    localStorage.setItem(THEME_TOKEN_STORAGE_KEY, JSON.stringify(tokens));
  } catch {
    // 自定义主题是增强能力；存储被禁用时仍保留当前页面内的覆盖。
  }
}

function applyTokens(tokens: Record<string, string>): void {
  for (const token of COLOR_TOKENS) {
    document.documentElement.style.removeProperty(token);
  }
  for (const [token, value] of Object.entries(tokens)) {
    document.documentElement.style.setProperty(token, value);
  }
}

function setTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // 无 localStorage 时仍然应用本次主题。
  }
}

function setToken(token: string, value: string | null): void {
  if (!COLOR_TOKENS.has(token) || (value !== null && !isColor(value))) {
    throw new Error(`invalid theme token: ${token}`);
  }
  const tokens = readTokens();
  if (value === null) {
    delete tokens[token];
  } else {
    tokens[token] = value;
  }
  writeTokens(tokens);
  applyTokens(tokens);
  setTheme("custom");
}

export function initTheme(): void {
  const queryTheme = new URLSearchParams(window.location.search).get("theme");
  let storedTheme: string | null = null;
  try {
    storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // 无 localStorage 时继续用查询参数或 auto。
  }
  const theme = normalizeTheme(queryTheme ?? storedTheme);
  setTheme(theme);

  const tokens = readTokens();
  for (const [key, value] of new URLSearchParams(window.location.search).entries()) {
    if (COLOR_TOKENS.has(key) && isColor(value)) {
      tokens[key] = value;
      setTheme("custom");
    }
  }
  if (queryTheme === "custom") {
    writeTokens(tokens);
  }
  applyTokens(tokens);

  window.AgentLearnTheme = {
    preset: setTheme,
    set: setToken,
    reset: () => {
      writeTokens({});
      applyTokens({});
      setTheme("auto");
    },
  };
}
