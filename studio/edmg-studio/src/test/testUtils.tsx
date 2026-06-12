import React from "react";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { StudioAppearanceProvider } from "../components/studioAppearance";
import { UiModeProvider } from "../components/uiMode";
import { StudioSessionProvider } from "../components/studioSession";

type MockRouteHandler =
  | unknown
  | ((path: string, init?: RequestInit) => unknown | Promise<unknown>);

type MockRouteMap = Record<string, MockRouteHandler>;

function normalizePath(input: string | URL) {
  const raw = String(input);
  return raw.replace(/^https?:\/\/[^/]+/i, "");
}

function findRouteKey(routes: MockRouteMap, method: string, path: string) {
  const exactKeys = [`${method} ${path}`, path];
  for (const key of exactKeys) {
    if (key in routes) return key;
  }
  for (const key of Object.keys(routes)) {
    if (!key.endsWith("*")) continue;
    const prefix = key.slice(0, -1);
    if (path.startsWith(prefix)) return key;
    if (`${method} ${path}`.startsWith(prefix)) return key;
  }
  return "";
}

export function installEdmgBridge(overrides: Partial<NonNullable<Window["edmg"]>> = {}) {
  window.__EDMG_BACKEND_URL__ = "http://127.0.0.1:7863";
  window.edmg = {
    backendUrl: () => "http://127.0.0.1:7863",
    getBackendUrl: async () => "http://127.0.0.1:7863",
    getBackendSettings: async () => ({
      ok: true,
      mode: "managed",
      host: "127.0.0.1",
      port: "7863",
      url: "",
      source: "test",
      currentBackendUrl: "http://127.0.0.1:7863",
    }),
    getDirectorStatus: async () => ({
      ok: true,
      available: true,
      managed: true,
      serviceUrl: "http://127.0.0.1:3001",
      mcpUrl: "http://127.0.0.1:3001/mcp",
      advertisedBaseUrl: "http://127.0.0.1:3001",
      backendUrl: "http://127.0.0.1:7863",
      pid: 12345,
      lastError: "",
      startedAt: "2026-05-02T00:00:00.000Z",
      packaged: false,
    }),
    setDirectorSettings: async (settings) => ({
      ok: true,
      restartRequired: false,
      available: true,
      managed: true,
      serviceUrl: "http://127.0.0.1:3001",
      mcpUrl: `${String(settings?.baseUrl || "http://127.0.0.1:3001").replace(/\/+$/, "")}/mcp`,
      advertisedBaseUrl: String(settings?.baseUrl || "http://127.0.0.1:3001"),
      backendUrl: "http://127.0.0.1:7863",
      pid: 12345,
      lastError: "",
      startedAt: "2026-05-02T00:00:00.000Z",
      packaged: false,
    }),
    setBackendSettings: async (settings) => ({
      ok: true,
      restartRequired: true,
      mode: String(settings?.mode || "managed"),
      host: String(settings?.host || "127.0.0.1"),
      port: String(settings?.port || "7863"),
      url: String(settings?.url || ""),
      currentBackendUrl:
        String(settings?.url || "").trim() ||
        `http://${String(settings?.host || "127.0.0.1")}:${String(settings?.port || "7863")}`,
    }),
    getAiSettings: async () => ({
      ok: true,
      mode: "local",
      provider: "openai_compat",
      aiBaseUrl: "http://127.0.0.1:7862",
      openaiCompatBaseUrl: "http://127.0.0.1:8001/v1",
      openaiCompatModel: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
      source: "test",
    }),
    setAiSettings: async (settings) => ({
      ok: true,
      restartRequired: true,
      mode: String(settings?.mode || "local"),
      provider: String(settings?.provider || "openai_compat"),
      aiBaseUrl: String(settings?.aiBaseUrl || "http://127.0.0.1:7862"),
      openaiCompatBaseUrl: String(settings?.openaiCompatBaseUrl || "http://127.0.0.1:8001/v1"),
      openaiCompatModel: String(settings?.openaiCompatModel || "nvidia/llama-3.3-nemotron-super-49b-v1.5"),
    }),
    revealPath: async (value: string) => ({ ok: true, action: "reveal_path", path: value }),
    showItemInFolder: async (value: string) => ({ ok: true, action: "show_item", path: value }),
    openPath: async (value: string) => ({ ok: true, action: "open_path", path: value }),
    openExternal: async () => {},
    getStudioPaths: async () => ({
      ok: true,
      platform: "win32",
      studioHome: "D:\\EDMG-NVIDIA-Workshop",
      dataDir: "D:\\EDMG-NVIDIA-Workshop\\data",
      modelsDir: "D:\\EDMG-NVIDIA-Workshop\\models",
      cacheRoot: "D:\\EDMG-NVIDIA-Workshop\\cache",
      logsDir: "D:\\EDMG-NVIDIA-Workshop\\logs",
      externalDir: "D:\\EDMG-NVIDIA-Workshop\\external",
      electronUserData: "D:\\EDMG-NVIDIA-Workshop\\electron",
      sessionData: "D:\\EDMG-NVIDIA-Workshop\\electron\\session",
      bootstrapConfigPath: "C:\\Users\\Tyler\\AppData\\Roaming\\EDMG NVIDIA Workshop\\bootstrap.json",
      source: "test",
    }),
    relaunch: async () => ({ ok: true }),
    ...overrides,
  };
}

export function installFetchMock(routes: MockRouteMap) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const method = String(init?.method || "GET").toUpperCase();
    const path = normalizePath(input instanceof Request ? input.url : input);
    const key = findRouteKey(routes, method, path);
    if (!key) {
      throw new Error(`Unhandled fetch route: ${method} ${path}`);
    }
    const handler = routes[key];
    const payload = typeof handler === "function"
      ? await handler(path, init)
      : handler;
    return {
      ok: true,
      status: 200,
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    } as Response;
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function renderWithStudio(ui: React.ReactElement) {
  return render(
    <StudioSessionProvider>
      <StudioAppearanceProvider>
        <UiModeProvider>{ui}</UiModeProvider>
      </StudioAppearanceProvider>
    </StudioSessionProvider>,
  );
}
