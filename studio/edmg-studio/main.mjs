import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import fs from "node:fs";
import { promises as fsp } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createBackendRuntime } from "./main-process/backend-runtime.mjs";
import { createDirectorRuntime } from "./main-process/director-runtime.mjs";
import { createWindowRuntime } from "./main-process/window-runtime.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const APP_NAME = "EDMG NVIDIA Workshop";
const IS_DEV = !app.isPackaged;
const IS_WINDOWS = process.platform === "win32";
const BOOTSTRAP_CONFIG_BASENAME = "bootstrap.json";
const IGNORABLE_WRITE_ERROR_CODES = new Set(["EPIPE", "ERR_STREAM_DESTROYED"]);

function readRuntimeDefaults() {
  const candidates = [];
  if (process.resourcesPath) {
    candidates.push(path.join(process.resourcesPath, "runtime-defaults.json"));
  }
  candidates.push(path.join(__dirname, "electron-resources", "runtime-defaults.json"));

  for (const candidate of candidates) {
    try {
      if (!fs.existsSync(candidate)) continue;
      const parsed = JSON.parse(fs.readFileSync(candidate, "utf8"));
      if (parsed && typeof parsed === "object") {
        return parsed;
      }
    } catch (error) {
      console.warn("[runtime-defaults] failed to read", candidate, error);
    }
  }

  return {};
}

const RUNTIME_DEFAULTS = readRuntimeDefaults();
const BACKEND_RUNTIME_DEFAULTS =
  RUNTIME_DEFAULTS.backend && typeof RUNTIME_DEFAULTS.backend === "object"
    ? RUNTIME_DEFAULTS.backend
    : {};
const DEFAULT_LOCAL_BACKEND_HOST = "127.0.0.1";
const DEFAULT_LOCAL_BACKEND_PORT = "7863";
const BACKEND_DEFAULT_MODE =
  typeof BACKEND_RUNTIME_DEFAULTS.spawnBackend === "boolean" && BACKEND_RUNTIME_DEFAULTS.spawnBackend === false
    ? "external"
    : "managed";
const BACKEND_SETTINGS_DEFAULTS = Object.freeze({
  mode: BACKEND_DEFAULT_MODE,
  host:
    BACKEND_DEFAULT_MODE !== "external" &&
    typeof BACKEND_RUNTIME_DEFAULTS.host === "string" && BACKEND_RUNTIME_DEFAULTS.host.trim()
      ? BACKEND_RUNTIME_DEFAULTS.host.trim()
      : DEFAULT_LOCAL_BACKEND_HOST,
  port:
    BACKEND_DEFAULT_MODE !== "external" &&
    BACKEND_RUNTIME_DEFAULTS.port != null && String(BACKEND_RUNTIME_DEFAULTS.port).trim()
      ? String(BACKEND_RUNTIME_DEFAULTS.port).trim()
      : DEFAULT_LOCAL_BACKEND_PORT,
  url:
    typeof BACKEND_RUNTIME_DEFAULTS.url === "string" && BACKEND_RUNTIME_DEFAULTS.url.trim()
      ? BACKEND_RUNTIME_DEFAULTS.url.trim()
      : "",
});

const BACKEND_SETTINGS_ENV_KEYS = Object.freeze({
  mode: "EDMG_STUDIO_BACKEND_MODE",
  host: "EDMG_STUDIO_BACKEND_HOST",
  port: "EDMG_STUDIO_BACKEND_PORT",
  url: "EDMG_STUDIO_BACKEND_URL",
  spawnBackend: "EDMG_STUDIO_SPAWN_BACKEND",
});

const STARTUP_BACKEND_SETTINGS = syncBackendSettingsToProcessEnv(getConfiguredBackendSettings());
const BACKEND_HOST = STARTUP_BACKEND_SETTINGS.host;
let BACKEND_PORT = Number(STARTUP_BACKEND_SETTINGS.port || BACKEND_SETTINGS_DEFAULTS.port);
const BACKEND_URL = STARTUP_BACKEND_SETTINGS.url || `http://${BACKEND_HOST}:${BACKEND_PORT}`;
const BACKEND_READY_TIMEOUT_MS = Number(
  process.env.EDMG_STUDIO_BACKEND_READY_TIMEOUT_MS ??
  (app.isPackaged && IS_WINDOWS ? "120000" : "15000"),
);

const DIRECTOR_RUNTIME_DEFAULTS =
  RUNTIME_DEFAULTS.director && typeof RUNTIME_DEFAULTS.director === "object"
    ? RUNTIME_DEFAULTS.director
    : {};
const DIRECTOR_HOST =
  String(process.env.EDMG_DIRECTOR_HOST ?? DIRECTOR_RUNTIME_DEFAULTS.host ?? "127.0.0.1").trim() ||
  "127.0.0.1";
const DIRECTOR_PORT_RAW = Number.parseInt(
  String(process.env.EDMG_DIRECTOR_PORT ?? DIRECTOR_RUNTIME_DEFAULTS.port ?? "3001"),
  10,
);
const DIRECTOR_PORT = Number.isFinite(DIRECTOR_PORT_RAW) && DIRECTOR_PORT_RAW > 0 ? DIRECTOR_PORT_RAW : 3001;
const DIRECTOR_SETTINGS_ENV_KEYS = Object.freeze({
  baseUrl: "EDMG_DIRECTOR_BASE_URL",
});
const DIRECTOR_SETTINGS_DEFAULTS = Object.freeze({
  baseUrl:
    normalizeBackendUrl(DIRECTOR_RUNTIME_DEFAULTS.baseUrl, "") ||
    `http://${DIRECTOR_HOST}:${DIRECTOR_PORT}`,
});
const STARTUP_DIRECTOR_SETTINGS = syncDirectorSettingsToProcessEnv(getConfiguredDirectorSettings());
const DIRECTOR_PUBLIC_BASE_URL = STARTUP_DIRECTOR_SETTINGS.baseUrl;
const DIRECTOR_READY_TIMEOUT_MS = Number(
  process.env.EDMG_DIRECTOR_READY_TIMEOUT_MS ??
  (app.isPackaged && IS_WINDOWS ? "45000" : "30000"),
);
const DIRECTOR_SPAWN =
  String(process.env.EDMG_DIRECTOR_SPAWN ?? "").trim() ||
  (DIRECTOR_RUNTIME_DEFAULTS.spawnDirector === false ? "0" : "1");
const SHOULD_SPAWN_DIRECTOR = DIRECTOR_SPAWN !== "0";

const TEST_MODE = (process.env.EDMG_STUDIO_TEST_MODE ?? "0") === "1";
const TEST_SKIP_MIGRATION = (process.env.EDMG_STUDIO_TEST_SKIP_MIGRATION ?? "0") === "1";
const TEST_PAGE = process.env.EDMG_STUDIO_TEST_PAGE
  ? path.resolve(process.env.EDMG_STUDIO_TEST_PAGE)
  : "";
const TEST_REPORT_PATH = process.env.EDMG_STUDIO_TEST_REPORT_PATH
  ? path.resolve(process.env.EDMG_STUDIO_TEST_REPORT_PATH)
  : "";
const TEST_TRACE_PATH = TEST_REPORT_PATH ? `${TEST_REPORT_PATH}.trace.log` : "";
const TEST_PROBE_REVEAL_PATH = process.env.EDMG_STUDIO_TEST_PROBE_REVEAL_PATH
  ? path.resolve(process.env.EDMG_STUDIO_TEST_PROBE_REVEAL_PATH)
  : "";
const TEST_PROBE_OPEN_PATH = process.env.EDMG_STUDIO_TEST_PROBE_OPEN_PATH
  ? path.resolve(process.env.EDMG_STUDIO_TEST_PROBE_OPEN_PATH)
  : "";
const TEST_EXPECT_BACKEND_URL = String(process.env.EDMG_STUDIO_TEST_EXPECT_BACKEND_URL ?? "").trim();
const FAKE_PATH_ACTIONS = (process.env.EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS ?? "0") === "1";

const UI_PORT = process.env.EDMG_STUDIO_UI_PORT ?? "5173";
const DEV_SERVER_URL =
  process.env.VITE_DEV_SERVER_URL ??
  process.env.EDMG_STUDIO_DEV_SERVER_URL ??
  `http://127.0.0.1:${UI_PORT}`;

const AI_SETTINGS_DEFAULTS = Object.freeze({
  mode: "local",
  provider: "openai_compat",
  aiBaseUrl: "http://127.0.0.1:7862",
  openaiCompatBaseUrl: "http://127.0.0.1:8001/v1",
  openaiCompatModel: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
});

const AI_SETTINGS_ENV_KEYS = Object.freeze({
  mode: "EDMG_AI_MODE",
  provider: "EDMG_AI_PROVIDER",
  aiBaseUrl: "EDMG_AI_BASE_URL",
  openaiCompatBaseUrl: "EDMG_AI_OPENAI_COMPAT_BASE_URL",
  openaiCompatModel: "EDMG_AI_OPENAI_COMPAT_MODEL",
});

const AI_LOCAL_PROVIDER_ALIASES = Object.freeze({
  openai: "openai_compat",
  "openai-compatible": "openai_compat",
  openai_compat: "openai_compat",
});

const NVIDIA_ONLY_REMOVED_ENV_KEYS = Object.freeze([
  "EDMG_AI_OLLAMA_URL",
  "EDMG_AI_OLLAMA_MODEL",
  "OLLAMA_MODELS",
]);

const STORAGE_SETTINGS_DEFAULT_DIRS = Object.freeze({
  dataDir: "data",
  modelsDir: "models",
  cacheRoot: "cache",
  logsDir: "logs",
  externalDir: "external",
});

const STORAGE_SETTINGS_ENV_KEYS = Object.freeze({
  dataDir: "EDMG_STUDIO_DATA_DIR",
  modelsDir: "EDMG_STUDIO_MODELS_DIR",
  cacheRoot: "EDMG_STUDIO_CACHE_DIR",
  logsDir: "EDMG_STUDIO_LOGS_DIR",
  externalDir: "EDMG_STUDIO_EXTERNAL_DIR",
});

function isIgnorableWriteError(error) {
  if (!error || typeof error !== "object") return false;
  const code = typeof error.code === "string" ? error.code : "";
  if (IGNORABLE_WRITE_ERROR_CODES.has(code)) return true;
  const message = typeof error.message === "string" ? error.message.toLowerCase() : "";
  return message.includes("broken pipe");
}

function suppressPipeError(error) {
  if (isIgnorableWriteError(error)) return true;
  throw error;
}

function installSafeProcessLogging() {
  for (const name of ["log", "info", "warn", "error", "debug"]) {
    const original = console[name];
    if (typeof original !== "function") continue;
    console[name] = (...args) => {
      try {
        return original.apply(console, args);
      } catch (error) {
        if (suppressPipeError(error)) return undefined;
        return undefined;
      }
    };
  }

  for (const stream of [process.stdout, process.stderr]) {
    if (!stream || typeof stream.on !== "function") continue;
    stream.on("error", (error) => {
      suppressPipeError(error);
    });
  }
}

function safeStreamWrite(stream, chunk) {
  if (!stream || typeof stream.write !== "function") return;
  try {
    stream.write(chunk);
  } catch (error) {
    suppressPipeError(error);
  }
}

function appendTestTrace(message) {
  if (!TEST_MODE || !TEST_TRACE_PATH) return;
  try {
    ensureDirSync(path.dirname(TEST_TRACE_PATH));
    fs.appendFileSync(TEST_TRACE_PATH, `[${new Date().toISOString()}] ${message}\n`, "utf8");
  } catch {}
}

installSafeProcessLogging();

app.setName(APP_NAME);

function ensureDirSync(targetPath) {
  try {
    fs.mkdirSync(targetPath, { recursive: true });
  } catch (error) {
    if (error?.code === "ELOOP" && repairMutualJunctionLoopSync(targetPath)) {
      return;
    }
    throw error;
  }
}

function pathExistsSync(targetPath) {
  try {
    return fs.existsSync(targetPath);
  } catch {
    return false;
  }
}

function configuredPathHasAvailableRoot(resolvedPath) {
  if (!resolvedPath || !IS_WINDOWS) return true;
  const root = path.parse(resolvedPath).root;
  if (!root) return true;
  return pathExistsSync(root);
}

function resolveConfiguredPath(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value) return "";
  const resolved = path.resolve(value);
  if (!configuredPathHasAvailableRoot(resolved)) return "";
  return resolved.toLowerCase().includes("app.asar") ? "" : resolved;
}

function getBootstrapConfigPath() {
  return path.join(app.getPath("appData"), APP_NAME, BOOTSTRAP_CONFIG_BASENAME);
}

function readBootstrapConfig() {
  const filePath = getBootstrapConfigPath();
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function writeBootstrapConfig(nextConfig) {
  const filePath = getBootstrapConfigPath();
  ensureDirSync(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(nextConfig, null, 2), "utf8");
}

function getLauncherEnvPath() {
  if (!IS_DEV) return "";
  const filePath = path.join(__dirname, "launcher_env.json");
  return filePath.toLowerCase().includes("app.asar") ? "" : filePath;
}

function readLauncherEnv() {
  const filePath = getLauncherEnvPath();
  if (!filePath) return {};
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function writeLauncherEnv(nextConfig) {
  const filePath = getLauncherEnvPath();
  if (!filePath) return false;
  ensureDirSync(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(nextConfig, null, 2), "utf8");
  return true;
}

function pickConfiguredString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function getDefaultStoragePaths(studioHome) {
  const resolvedHome = resolveConfiguredPath(studioHome);
  if (!resolvedHome) {
    const fallbackDataDir = path.join(app.getPath("userData"), "data");
    const fallbackHome = path.dirname(fallbackDataDir);
    return getDefaultStoragePaths(fallbackHome);
  }

  const electronUserData = path.join(resolvedHome, "electron");
  return {
    studioHome: resolvedHome,
    dataDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.dataDir),
    modelsDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.modelsDir),
    cacheRoot: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.cacheRoot),
    logsDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.logsDir),
    externalDir: path.join(resolvedHome, STORAGE_SETTINGS_DEFAULT_DIRS.externalDir),
    electronUserData,
    sessionData: path.join(electronUserData, "session"),
  };
}

function getRawStorageSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  return {
    dataDir: env[STORAGE_SETTINGS_ENV_KEYS.dataDir],
    modelsDir: env[STORAGE_SETTINGS_ENV_KEYS.modelsDir],
    cacheRoot: env[STORAGE_SETTINGS_ENV_KEYS.cacheRoot],
    logsDir: env[STORAGE_SETTINGS_ENV_KEYS.logsDir],
    externalDir: env[STORAGE_SETTINGS_ENV_KEYS.externalDir],
  };
}

function readBootstrapStorageSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.storageSettings && typeof bootstrapConfig.storageSettings === "object") {
    return bootstrapConfig.storageSettings;
  }
  return {};
}

function readLauncherStorageSettingsRaw() {
  const launcherEnv = readLauncherEnv();
  const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
  const raw = getRawStorageSettingsFromEnv(launcherEnv);
  return launcherHome ? trimStorageOverrides(raw, launcherHome) : normalizeStorageOverrides(raw);
}

function hasAnyStorageSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function normalizeStorageOverrides(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  return {
    dataDir: resolveConfiguredPath(current.dataDir),
    modelsDir: resolveConfiguredPath(current.modelsDir),
    cacheRoot: resolveConfiguredPath(current.cacheRoot),
    logsDir: resolveConfiguredPath(current.logsDir),
    externalDir: resolveConfiguredPath(current.externalDir),
  };
}

function buildResolvedStudioPaths(studioHome, rawSettings = {}) {
  const defaults = getDefaultStoragePaths(studioHome);
  const overrides = normalizeStorageOverrides(rawSettings);
  return {
    studioHome: defaults.studioHome,
    dataDir: overrides.dataDir || defaults.dataDir,
    modelsDir: overrides.modelsDir || defaults.modelsDir,
    cacheRoot: overrides.cacheRoot || defaults.cacheRoot,
    logsDir: overrides.logsDir || defaults.logsDir,
    externalDir: overrides.externalDir || defaults.externalDir,
    electronUserData: defaults.electronUserData,
    sessionData: defaults.sessionData,
  };
}

function trimStorageOverrides(rawSettings = {}, studioHome = "") {
  const defaults = getDefaultStoragePaths(studioHome || getConfiguredStudioHome() || path.dirname(getDefaultDataDir()));
  const normalized = normalizeStorageOverrides(rawSettings);
  const trimmed = {};
  for (const [key, value] of Object.entries(normalized)) {
    if (value && !samePath(value, defaults[key])) {
      trimmed[key] = value;
    }
  }
  return trimmed;
}

function getRawAiSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  return {
    mode: env[AI_SETTINGS_ENV_KEYS.mode],
    provider: env[AI_SETTINGS_ENV_KEYS.provider],
    aiBaseUrl: env[AI_SETTINGS_ENV_KEYS.aiBaseUrl],
    openaiCompatBaseUrl: env[AI_SETTINGS_ENV_KEYS.openaiCompatBaseUrl],
    openaiCompatModel: env[AI_SETTINGS_ENV_KEYS.openaiCompatModel],
  };
}

function getRawBackendSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  let mode = env[BACKEND_SETTINGS_ENV_KEYS.mode];
  if (!mode && typeof env[BACKEND_SETTINGS_ENV_KEYS.spawnBackend] === "string") {
    mode = String(env[BACKEND_SETTINGS_ENV_KEYS.spawnBackend]).trim() === "0" ? "external" : "managed";
  }
  return {
    mode,
    host: env[BACKEND_SETTINGS_ENV_KEYS.host],
    port: env[BACKEND_SETTINGS_ENV_KEYS.port],
    url: env[BACKEND_SETTINGS_ENV_KEYS.url],
  };
}

function readBootstrapAiSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.aiSettings && typeof bootstrapConfig.aiSettings === "object") {
    return bootstrapConfig.aiSettings;
  }
  return {};
}

function readBootstrapBackendSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.backendSettings && typeof bootstrapConfig.backendSettings === "object") {
    return bootstrapConfig.backendSettings;
  }
  return {};
}

function readBootstrapDirectorSettingsRaw() {
  const bootstrapConfig = readBootstrapConfig();
  if (bootstrapConfig?.directorSettings && typeof bootstrapConfig.directorSettings === "object") {
    return bootstrapConfig.directorSettings;
  }
  return {};
}

function hasAnyAiSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function hasAnyBackendSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function hasAnyDirectorSetting(rawSettings) {
  return Object.values(rawSettings ?? {}).some((value) => typeof value === "string" && value.trim());
}

function normalizeAiMode(rawValue) {
  const mode = String(rawValue ?? "").trim().toLowerCase();
  return mode === "http" || mode === "remote" ? "http" : "local";
}

function normalizeBackendMode(rawValue) {
  const mode = String(rawValue ?? "").trim().toLowerCase();
  return mode === "external" || mode === "remote" || mode === "connect" ? "external" : "managed";
}

function normalizeAiProvider(rawValue) {
  const provider = String(rawValue ?? "").trim().toLowerCase();
  return AI_LOCAL_PROVIDER_ALIASES[provider] ?? AI_SETTINGS_DEFAULTS.provider;
}

function stripNvidiaOnlyRemovedEnv(rawEnv = {}) {
  const next = { ...(rawEnv && typeof rawEnv === "object" ? rawEnv : {}) };
  for (const key of NVIDIA_ONLY_REMOVED_ENV_KEYS) {
    delete next[key];
  }
  return next;
}

function normalizeBackendPort(rawValue) {
  const raw = String(rawValue ?? "").trim();
  const value = Number(raw);
  if (Number.isInteger(value) && value >= 1 && value <= 65535) {
    return String(value);
  }
  return BACKEND_SETTINGS_DEFAULTS.port;
}

function buildManagedBackendUrl(host, port) {
  return `http://${host}:${port}`;
}

function normalizeBackendUrl(rawValue, fallbackUrl = "") {
  const candidate = pickConfiguredString(rawValue, fallbackUrl);
  if (!candidate) return "";
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "";
    }
    const normalizedPath =
      parsed.pathname && parsed.pathname !== "/"
        ? parsed.pathname.replace(/\/+$/, "")
        : "";
    return `${parsed.origin}${normalizedPath}`;
  } catch {
    return "";
  }
}

function normalizeAiSettings(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  return {
    mode: normalizeAiMode(current.mode),
    provider: normalizeAiProvider(current.provider),
    aiBaseUrl: pickConfiguredString(current.aiBaseUrl, AI_SETTINGS_DEFAULTS.aiBaseUrl),
    openaiCompatBaseUrl: pickConfiguredString(
      current.openaiCompatBaseUrl,
      AI_SETTINGS_DEFAULTS.openaiCompatBaseUrl
    ),
    openaiCompatModel: pickConfiguredString(
      current.openaiCompatModel,
      AI_SETTINGS_DEFAULTS.openaiCompatModel
    ),
  };
}

function defaultNvidiaNimUrl(openaiCompatBaseUrl = "") {
  const baseUrl = String(openaiCompatBaseUrl || AI_SETTINGS_DEFAULTS.openaiCompatBaseUrl).trim();
  return baseUrl.replace(/\/v1\/?$/i, "") || "http://127.0.0.1:8001";
}

function normalizeBackendSettings(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  const mode = normalizeBackendMode(current.mode);
  const host = pickConfiguredString(current.host, BACKEND_SETTINGS_DEFAULTS.host);
  const port = normalizeBackendPort(current.port);
  const fallbackUrl = buildManagedBackendUrl(host, port);
  const url = mode === "external" ? normalizeBackendUrl(current.url, fallbackUrl) : "";

  return {
    mode,
    host,
    port,
    url,
  };
}

function getRawDirectorSettingsFromEnv(envLike) {
  const env = envLike && typeof envLike === "object" ? envLike : {};
  return {
    baseUrl: env[DIRECTOR_SETTINGS_ENV_KEYS.baseUrl],
  };
}

function normalizeDirectorSettings(rawSettings = {}) {
  const current = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
  return {
    baseUrl:
      normalizeBackendUrl(current.baseUrl, DIRECTOR_SETTINGS_DEFAULTS.baseUrl) ||
      DIRECTOR_SETTINGS_DEFAULTS.baseUrl,
  };
}

function getConfiguredAiSettings() {
  const launcherRaw = getRawAiSettingsFromEnv(readLauncherEnv());
  const bootstrapRaw = readBootstrapAiSettingsRaw();
  const envRaw = getRawAiSettingsFromEnv(process.env);
  const configured = normalizeAiSettings({
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
  });

  let source = "default";
  if (hasAnyAiSetting(launcherRaw)) source = "launcher";
  if (hasAnyAiSetting(bootstrapRaw)) source = "bootstrap";
  if (hasAnyAiSetting(envRaw)) source = "env";

  return { ...configured, source };
}

function getConfiguredBackendSettings() {
  const launcherRaw = getRawBackendSettingsFromEnv(readLauncherEnv());
  const bootstrapRaw = readBootstrapBackendSettingsRaw();
  const envRaw = getRawBackendSettingsFromEnv(process.env);
  const configured = normalizeBackendSettings({
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
  });

  let source = "default";
  if (hasAnyBackendSetting(launcherRaw)) source = "launcher";
  if (hasAnyBackendSetting(bootstrapRaw)) source = "bootstrap";
  if (hasAnyBackendSetting(envRaw)) source = "env";

  return { ...configured, source };
}

function getConfiguredDirectorSettings() {
  const launcherRaw = getRawDirectorSettingsFromEnv(readLauncherEnv());
  const bootstrapRaw = readBootstrapDirectorSettingsRaw();
  const envRaw = getRawDirectorSettingsFromEnv(process.env);
  const configured = normalizeDirectorSettings({
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
  });

  let source = "default";
  if (hasAnyDirectorSetting(launcherRaw)) source = "launcher";
  if (hasAnyDirectorSetting(bootstrapRaw)) source = "bootstrap";
  if (hasAnyDirectorSetting(envRaw)) source = "env";

  return { ...configured, source };
}

function syncAiSettingsToProcessEnv(rawSettings) {
  const aiSettings = normalizeAiSettings(rawSettings);
  process.env.EDMG_AI_MODE = aiSettings.mode;
  process.env.EDMG_AI_PROVIDER = aiSettings.provider;
  process.env.EDMG_AI_BASE_URL = aiSettings.aiBaseUrl;
  delete process.env.EDMG_AI_OLLAMA_URL;
  delete process.env.EDMG_AI_OLLAMA_MODEL;
  process.env.EDMG_AI_OPENAI_COMPAT_BASE_URL = aiSettings.openaiCompatBaseUrl;
  process.env.EDMG_AI_OPENAI_COMPAT_MODEL = aiSettings.openaiCompatModel;
  if (!String(process.env.EDMG_NVIDIA_MODE ?? "").trim()) {
    process.env.EDMG_NVIDIA_MODE = "1";
  }
  if (!String(process.env.EDMG_NVIDIA_PROFILE ?? "").trim()) {
    process.env.EDMG_NVIDIA_PROFILE = "omniverse";
  }
  if (!String(process.env.EDMG_NVIDIA_NIM_URL ?? "").trim()) {
    process.env.EDMG_NVIDIA_NIM_URL = defaultNvidiaNimUrl(aiSettings.openaiCompatBaseUrl);
  }
  return aiSettings;
}

function syncBackendSettingsToProcessEnv(rawSettings) {
  const backendSettings = normalizeBackendSettings(rawSettings);
  process.env[BACKEND_SETTINGS_ENV_KEYS.mode] = backendSettings.mode;
  process.env[BACKEND_SETTINGS_ENV_KEYS.host] = backendSettings.host;
  process.env[BACKEND_SETTINGS_ENV_KEYS.port] = backendSettings.port;
  process.env[BACKEND_SETTINGS_ENV_KEYS.url] = backendSettings.url;
  process.env[BACKEND_SETTINGS_ENV_KEYS.spawnBackend] = backendSettings.mode === "external" ? "0" : "1";
  return backendSettings;
}

function syncDirectorSettingsToProcessEnv(rawSettings) {
  const directorSettings = normalizeDirectorSettings(rawSettings);
  process.env[DIRECTOR_SETTINGS_ENV_KEYS.baseUrl] = directorSettings.baseUrl;
  return directorSettings;
}

syncAiSettingsToProcessEnv(getConfiguredAiSettings());

function getConfiguredDataDir(includeLauncher = true) {
  const explicitDataDir = resolveConfiguredPath(process.env.EDMG_STUDIO_DATA_DIR);
  if (explicitDataDir) return explicitDataDir;

  const bootstrapDataDir = resolveConfiguredPath(readBootstrapStorageSettingsRaw()?.dataDir);
  if (bootstrapDataDir) return bootstrapDataDir;

  const bootstrapConfig = readBootstrapConfig();
  const bootstrapHome = resolveConfiguredPath(bootstrapConfig?.studioHome);
  if (bootstrapHome) return path.join(bootstrapHome, "data");

  if (includeLauncher) {
    const launcherStorage = readLauncherStorageSettingsRaw();
    const launcherStorageDataDir = resolveConfiguredPath(launcherStorage?.dataDir);
    if (launcherStorageDataDir) return launcherStorageDataDir;
    const launcherEnv = readLauncherEnv();
    const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
    if (launcherHome) return path.join(launcherHome, "data");
    const launcherDataDir = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_DATA_DIR);
    if (launcherDataDir) return launcherDataDir;
  }

  return "";
}

function getConfiguredStudioHome() {
  const explicitHome = resolveConfiguredPath(process.env.EDMG_STUDIO_HOME);
  if (explicitHome) return explicitHome;

  const explicitDataDir = getConfiguredDataDir(false);
  if (explicitDataDir) return path.dirname(explicitDataDir);

  const bootstrapConfig = readBootstrapConfig();
  const savedHome = resolveConfiguredPath(bootstrapConfig?.studioHome);
  if (savedHome) return savedHome;

  const launcherEnv = readLauncherEnv();
  const launcherHome = resolveConfiguredPath(launcherEnv?.EDMG_STUDIO_HOME);
  if (launcherHome) return launcherHome;

  return "";
}

const INITIAL_STUDIO_HOME = getConfiguredStudioHome();

function applyStudioStoragePaths(paths) {
  if (!paths?.studioHome) return;

  ensureDirSync(paths.electronUserData);
  app.setPath("userData", paths.electronUserData);

  try {
    ensureDirSync(paths.sessionData);
    app.setPath("sessionData", paths.sessionData);
  } catch {}

  try {
    ensureDirSync(paths.logsDir);
    app.setPath("logs", paths.logsDir);
  } catch {}
}

if (INITIAL_STUDIO_HOME) {
  applyStudioStoragePaths(getStudioPaths(INITIAL_STUDIO_HOME));
}

function getStudioPaths(studioHomeOverride = "", storageOverrideValues = null) {
  const overrideHome = resolveConfiguredPath(studioHomeOverride);
  const configuredDataDir = getConfiguredDataDir();
  const configuredStudioHome = getConfiguredStudioHome();
  const bootstrapConfig = readBootstrapConfig();
  const resolvedHome =
    overrideHome ||
    configuredStudioHome ||
    path.dirname(configuredDataDir || getDefaultDataDir());
  const launcherRaw = readLauncherStorageSettingsRaw();
  const bootstrapRaw = readBootstrapStorageSettingsRaw();
  const envRaw = trimStorageOverrides(getRawStorageSettingsFromEnv(process.env), resolvedHome);
  const overrideRaw =
    storageOverrideValues && typeof storageOverrideValues === "object" ? storageOverrideValues : {};
  const mergedRaw = {
    ...launcherRaw,
    ...bootstrapRaw,
    ...envRaw,
    ...overrideRaw,
  };
  const paths = buildResolvedStudioPaths(resolvedHome, mergedRaw);

  let storageSource = "default";
  if (hasAnyStorageSetting(launcherRaw)) storageSource = "launcher";
  if (hasAnyStorageSetting(bootstrapRaw)) storageSource = "bootstrap";
  if (hasAnyStorageSetting(envRaw)) storageSource = "env";
  if (hasAnyStorageSetting(overrideRaw)) storageSource = "override";

  return {
    ...paths,
    platform: process.platform,
    storageOverrides: trimStorageOverrides(mergedRaw, resolvedHome),
    bootstrapConfigPath: getBootstrapConfigPath(),
    pendingMigration: bootstrapConfig?.pendingMigration ?? null,
    lastMigration: bootstrapConfig?.lastMigration ?? null,
    source: (overrideHome || configuredStudioHome || configuredDataDir || storageSource !== "default") ? "configured" : "default",
    storageSource,
  };
}

function buildManagedStudioEnv(studioHomeOverride = "", storageOverrideValues = null) {
  const paths = getStudioPaths(studioHomeOverride, storageOverrideValues);
  const managed = {
    EDMG_STUDIO_HOME: paths.studioHome,
    EDMG_STUDIO_DATA_DIR: paths.dataDir,
    EDMG_STUDIO_MODELS_DIR: paths.modelsDir,
    EDMG_STUDIO_CACHE_DIR: paths.cacheRoot,
    EDMG_STUDIO_LOGS_DIR: paths.logsDir,
    EDMG_STUDIO_EXTERNAL_DIR: paths.externalDir,
    PIP_CACHE_DIR: path.join(paths.cacheRoot, "pip"),
    XDG_CACHE_HOME: path.join(paths.cacheRoot, "xdg"),
    HF_HOME: path.join(paths.cacheRoot, "huggingface"),
    HUGGINGFACE_HUB_CACHE: path.join(paths.cacheRoot, "huggingface", "hub"),
    TRANSFORMERS_CACHE: path.join(paths.cacheRoot, "transformers"),
    TORCH_HOME: path.join(paths.cacheRoot, "torch"),
    NLTK_DATA: path.join(paths.cacheRoot, "nltk_data"),
    WHISPER_CACHE_DIR: path.join(paths.cacheRoot, "whisper"),
    MPLCONFIGDIR: path.join(paths.cacheRoot, "matplotlib"),
    TMP: path.join(paths.cacheRoot, "tmp"),
    TEMP: path.join(paths.cacheRoot, "tmp"),
  };

  for (const targetPath of Object.values(managed)) {
    if (typeof targetPath === "string" && targetPath.trim()) {
      ensureDirSync(targetPath);
    }
  }
  delete process.env.OLLAMA_MODELS;

  return managed;
}

function syncStorageSettingsToProcessEnv(studioHome = "", storageOverrides = null) {
  const paths = buildResolvedStudioPaths(
    studioHome || getConfiguredStudioHome() || path.dirname(getDefaultDataDir()),
    storageOverrides || {}
  );
  const managed = {
    EDMG_STUDIO_HOME: paths.studioHome,
    EDMG_STUDIO_DATA_DIR: paths.dataDir,
    EDMG_STUDIO_MODELS_DIR: paths.modelsDir,
    EDMG_STUDIO_CACHE_DIR: paths.cacheRoot,
    EDMG_STUDIO_LOGS_DIR: paths.logsDir,
    EDMG_STUDIO_EXTERNAL_DIR: paths.externalDir,
    PIP_CACHE_DIR: path.join(paths.cacheRoot, "pip"),
    XDG_CACHE_HOME: path.join(paths.cacheRoot, "xdg"),
    HF_HOME: path.join(paths.cacheRoot, "huggingface"),
    HUGGINGFACE_HUB_CACHE: path.join(paths.cacheRoot, "huggingface", "hub"),
    TRANSFORMERS_CACHE: path.join(paths.cacheRoot, "transformers"),
    TORCH_HOME: path.join(paths.cacheRoot, "torch"),
    NLTK_DATA: path.join(paths.cacheRoot, "nltk_data"),
    WHISPER_CACHE_DIR: path.join(paths.cacheRoot, "whisper"),
    MPLCONFIGDIR: path.join(paths.cacheRoot, "matplotlib"),
    TMP: path.join(paths.cacheRoot, "tmp"),
    TEMP: path.join(paths.cacheRoot, "tmp"),
  };
  for (const [key, value] of Object.entries(managed)) {
    ensureDirSync(value);
    process.env[key] = value;
  }
  delete process.env.OLLAMA_MODELS;
  return {
    ...paths,
    storageOverrides: trimStorageOverrides(storageOverrides || {}, paths.studioHome),
    bootstrapConfigPath: getBootstrapConfigPath(),
    pendingMigration: readBootstrapConfig()?.pendingMigration ?? null,
    lastMigration: readBootstrapConfig()?.lastMigration ?? null,
    source: "configured",
    storageSource: "override",
  };
}

function buildManagedAiEnv() {
  const aiSettings = getConfiguredAiSettings();
  delete process.env.EDMG_AI_OLLAMA_URL;
  delete process.env.EDMG_AI_OLLAMA_MODEL;
  return {
    EDMG_AI_MODE: aiSettings.mode,
    EDMG_AI_PROVIDER: aiSettings.provider,
    EDMG_AI_BASE_URL: aiSettings.aiBaseUrl,
    EDMG_AI_OPENAI_COMPAT_BASE_URL: aiSettings.openaiCompatBaseUrl,
    EDMG_AI_OPENAI_COMPAT_MODEL: aiSettings.openaiCompatModel,
    EDMG_NVIDIA_MODE: String(process.env.EDMG_NVIDIA_MODE ?? "1").trim() || "1",
    EDMG_NVIDIA_PROFILE: String(process.env.EDMG_NVIDIA_PROFILE ?? "omniverse").trim() || "omniverse",
    EDMG_NVIDIA_NIM_URL:
      String(process.env.EDMG_NVIDIA_NIM_URL ?? "").trim() ||
      defaultNvidiaNimUrl(aiSettings.openaiCompatBaseUrl),
    EDMG_NVIDIA_RIVA_URL: String(process.env.EDMG_NVIDIA_RIVA_URL ?? "127.0.0.1:50051").trim(),
    EDMG_NVIDIA_RIVA_ASR: String(process.env.EDMG_NVIDIA_RIVA_ASR ?? "0").trim() || "0",
    EDMG_NVIDIA_RIVA_LANGUAGE_CODE:
      String(process.env.EDMG_NVIDIA_RIVA_LANGUAGE_CODE ?? "en-US").trim() || "en-US",
  };
}

function normalizePath(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value) return "";
  return path.resolve(value);
}

function samePath(left, right) {
  const a = normalizePath(left);
  const b = normalizePath(right);
  if (!a || !b) return false;
  return IS_WINDOWS ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function readJunctionTargetSync(targetPath) {
  try {
    const stat = fs.lstatSync(targetPath);
    if (!stat.isSymbolicLink()) return "";
    const rawTarget = fs.readlinkSync(targetPath);
    return normalizePath(path.resolve(path.dirname(targetPath), rawTarget));
  } catch {
    return "";
  }
}

async function readJunctionTarget(targetPath) {
  try {
    const stat = await fsp.lstat(targetPath);
    if (!stat.isSymbolicLink()) return "";
    const rawTarget = await fsp.readlink(targetPath);
    return normalizePath(path.resolve(path.dirname(targetPath), rawTarget));
  } catch {
    return "";
  }
}

function repairMutualJunctionLoopSync(targetPath) {
  const source = normalizePath(targetPath);
  if (!source) return false;
  const target = readJunctionTargetSync(source);
  if (!target) return false;
  const reverse = readJunctionTargetSync(target);
  if (!reverse || !samePath(reverse, source)) return false;

  try {
    fs.rmSync(source, { recursive: true, force: true });
    fs.mkdirSync(source, { recursive: true });
    return true;
  } catch {
    return false;
  }
}

async function sourceAlreadyRedirectsToTarget(sourcePath, targetPath) {
  const sourceTarget = await readJunctionTarget(sourcePath);
  return Boolean(sourceTarget && samePath(sourceTarget, targetPath));
}

async function targetAlreadyRedirectsToSource(sourcePath, targetPath) {
  const targetTarget = await readJunctionTarget(targetPath);
  return Boolean(targetTarget && samePath(targetTarget, sourcePath));
}

async function pathExists(targetPath) {
  try {
    await fsp.lstat(targetPath);
    return true;
  } catch {
    return false;
  }
}

function selectStudioPathSet(paths) {
  return {
    studioHome: paths?.studioHome ?? "",
    dataDir: paths?.dataDir ?? "",
    modelsDir: paths?.modelsDir ?? "",
    cacheRoot: paths?.cacheRoot ?? "",
    electronUserData: paths?.electronUserData ?? "",
    sessionData: paths?.sessionData ?? "",
    logsDir: paths?.logsDir ?? "",
    externalDir: paths?.externalDir ?? "",
  };
}

function buildPendingMigration(sourcePaths, targetPaths) {
  const keys = ["dataDir", "modelsDir", "cacheRoot", "logsDir", "externalDir", "electronUserData"];
  const changed = keys.some((key) => !samePath(sourcePaths?.[key], targetPaths?.[key]));
  if (!changed) return null;

  return {
    requestedAt: new Date().toISOString(),
    source: selectStudioPathSet(sourcePaths),
    target: selectStudioPathSet(targetPaths),
  };
}

function summarizePendingMigration(plan) {
  if (!plan?.source || !plan?.target) return "";
  const labels = [
    ["dataDir", "project data"],
    ["modelsDir", "models"],
    ["cacheRoot", "cache"],
    ["logsDir", "logs"],
    ["externalDir", "external tools"],
    ["electronUserData", "Electron data"],
  ].filter(([key]) => !samePath(plan.source?.[key], plan.target?.[key]));
  if (!labels.length) return "";
  return `Existing ${labels.map(([, label]) => label).join(", ")} will migrate into the new storage layout on restart.`;
}

async function safeMergeCopy(src, dst) {
  const info = await fsp.lstat(src);
  if (info.isDirectory()) {
    await fsp.mkdir(dst, { recursive: true });
    let filesCopied = 0;
    let filesRenamed = 0;
    for (const entry of await fsp.readdir(src)) {
      const child = await safeMergeCopy(path.join(src, entry), path.join(dst, entry));
      filesCopied += child.filesCopied;
      filesRenamed += child.filesRenamed;
    }
    return { filesCopied, filesRenamed };
  }

  await fsp.mkdir(path.dirname(dst), { recursive: true });
  let target = dst;
  let filesRenamed = 0;

  if (await pathExists(target)) {
    const parsed = path.parse(target);
    let counter = 1;
    do {
      target = path.join(parsed.dir, `${parsed.name}_dup${counter}${parsed.ext}`);
      counter += 1;
    } while (await pathExists(target));
    filesRenamed = 1;
  }

  await fsp.copyFile(src, target);
  return { filesCopied: 1, filesRenamed };
}

async function createMovedMarker(sourcePath, targetPath) {
  await fsp.mkdir(sourcePath, { recursive: true });
  await fsp.writeFile(
    path.join(sourcePath, "MOVED_TO.txt"),
    `This folder was migrated to:\n${targetPath}\n`,
    "utf8"
  );
}

async function createJunction(sourcePath, targetPath) {
  if (!IS_WINDOWS) return false;
  try {
    await fsp.symlink(targetPath, sourcePath, "junction");
    return true;
  } catch {
    return false;
  }
}

async function migrateDirectory({ sourcePath, targetPath, label, allowJunction = true }) {
  const source = normalizePath(sourcePath);
  const target = normalizePath(targetPath);

  if (!source || !target || samePath(source, target)) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "already_aligned" };
  }

  if (await sourceAlreadyRedirectsToTarget(source, target)) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "already_redirected" };
  }

  if (await targetAlreadyRedirectsToSource(source, target)) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "target_already_redirects_to_source" };
  }

  if (!(await pathExists(source))) {
    return { label, status: "skipped", sourcePath: source, targetPath: target, reason: "missing_source" };
  }

  try {
    const { filesCopied, filesRenamed } = await safeMergeCopy(source, target);
    let cleanup = "kept_source";
    let compatibilityPath = "none";

    try {
      await fsp.rm(source, { recursive: true, force: true });
      cleanup = "removed_source";
      if (allowJunction) {
        if (await createJunction(source, target)) {
          compatibilityPath = "junction";
        } else {
          await createMovedMarker(source, target);
          compatibilityPath = "marker";
        }
      }
    } catch (cleanupError) {
      cleanup = `kept_source:${String(cleanupError?.message ?? cleanupError)}`;
    }

    return {
      label,
      status: "migrated",
      sourcePath: source,
      targetPath: target,
      filesCopied,
      filesRenamed,
      cleanup,
      compatibilityPath,
    };
  } catch (error) {
    return {
      label,
      status: "failed",
      sourcePath: source,
      targetPath: target,
      error: String(error?.message ?? error),
    };
  }
}

async function runPendingStudioMigrationIfNeeded() {
  const bootstrapConfig = readBootstrapConfig();
  const plan = bootstrapConfig?.pendingMigration;
  if (!plan?.source || !plan?.target) return null;

  const bootstrapRoot = path.dirname(getBootstrapConfigPath());
  const results = [];

  results.push(await migrateDirectory({
    sourcePath: plan.source.dataDir,
    targetPath: plan.target.dataDir,
    label: "project_data",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.modelsDir,
    targetPath: plan.target.modelsDir,
    label: "models",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.cacheRoot,
    targetPath: plan.target.cacheRoot,
    label: "cache",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.logsDir,
    targetPath: plan.target.logsDir,
    label: "logs",
  }));

  results.push(await migrateDirectory({
    sourcePath: plan.source.externalDir,
    targetPath: plan.target.externalDir,
    label: "external_tools",
  }));

  if (!samePath(plan.source.electronUserData, bootstrapRoot)) {
    results.push(await migrateDirectory({
      sourcePath: plan.source.electronUserData,
      targetPath: plan.target.electronUserData,
      label: "electron_data",
    }));
  } else {
    results.push({
      label: "electron_data",
      status: "skipped",
      sourcePath: normalizePath(plan.source.electronUserData),
      targetPath: normalizePath(plan.target.electronUserData),
      reason: "shares_bootstrap_root",
      message: "Left the old Electron root in place because it contains the bootstrap config.",
    });
  }

  const failed = results.filter((item) => item.status === "failed");
  const summary = {
    requestedAt: plan.requestedAt,
    completedAt: new Date().toISOString(),
    source: plan.source,
    target: plan.target,
    ok: failed.length === 0,
    results,
  };

  const nextConfig = {
    ...bootstrapConfig,
    lastMigration: summary,
  };
  delete nextConfig.pendingMigration;
  writeBootstrapConfig(nextConfig);

  console.log("[studio-migration]", JSON.stringify(summary));
  return summary;
}

function getDefaultDataDir() {
  const configuredDataDir = getConfiguredDataDir();
  if (configuredDataDir) return configuredDataDir;

  const configuredStudioHome = getConfiguredStudioHome();
  if (configuredStudioHome) {
    return getDefaultStoragePaths(configuredStudioHome).dataDir;
  }

  return path.join(app.getPath("userData"), "data");
}
function isExistingDirectory(targetPath) {
  try {
    return fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory();
  } catch {
    return false;
  }
}

const backendRuntime = createBackendRuntime({
  app,
  dialog,
  rootDir: __dirname,
  isWindows: IS_WINDOWS,
  backendHost: BACKEND_HOST,
  backendPort: BACKEND_PORT,
  backendUrl: BACKEND_URL,
  backendReadyTimeoutMs: BACKEND_READY_TIMEOUT_MS,
  testMode: TEST_MODE,
  pathExistsSync,
  ensureDirSync,
  safeStreamWrite,
  getStudioPaths,
  buildManagedStudioEnv,
  buildManagedAiEnv,
});

const directorRuntime = createDirectorRuntime({
  app,
  rootDir: __dirname,
  isWindows: IS_WINDOWS,
  directorHost: DIRECTOR_HOST,
  directorPort: DIRECTOR_PORT,
  directorPublicBaseUrl: DIRECTOR_PUBLIC_BASE_URL,
  directorReadyTimeoutMs: DIRECTOR_READY_TIMEOUT_MS,
  spawnDirector: SHOULD_SPAWN_DIRECTOR,
  pathExistsSync,
  ensureDirSync,
  safeStreamWrite,
  getStudioPaths,
  getBackendUrl: () => backendRuntime.getCurrentBackendUrl(),
});

const windowRuntime = createWindowRuntime({
  app,
  BrowserWindow,
  rootDir: __dirname,
  appName: APP_NAME,
  isDev: IS_DEV,
  devServerUrl: DEV_SERVER_URL,
  backendHost: BACKEND_HOST,
  backendPort: BACKEND_PORT,
  backendUrl: BACKEND_URL,
  testMode: TEST_MODE,
  testPage: TEST_PAGE,
  testReportPath: TEST_REPORT_PATH,
  testProbeRevealPath: TEST_PROBE_REVEAL_PATH,
  testProbeOpenPath: TEST_PROBE_OPEN_PATH,
  testExpectBackendUrl: TEST_EXPECT_BACKEND_URL,
  pathExistsSync,
  ensureDirSync,
  appendTestTrace,
});

console.log(`EDMG_currentBackendUrl=${backendRuntime.getCurrentBackendUrl()}`);

function registerIpcHandlers() {
  ipcMain.handle("edmg:getBackendUrl", async () => backendRuntime.getCurrentBackendUrl());
  ipcMain.handle("edmg:getBackendSettings", async () => ({
    ok: true,
    ...getConfiguredBackendSettings(),
    currentBackendUrl: backendRuntime.getCurrentBackendUrl(),
  }));
  ipcMain.handle("edmg:getDirectorStatus", async () => directorRuntime.getDirectorStatus());
  ipcMain.handle("edmg:getStudioPaths", async () => ({ ok: true, ...getStudioPaths() }));
  ipcMain.handle("edmg:getAiSettings", async () => ({ ok: true, ...getConfiguredAiSettings() }));

  const saveStorageSettings = async (nextSettings = {}) => {
    const requested = nextSettings && typeof nextSettings === "object" ? nextSettings : {};
    const studioHome = resolveConfiguredPath(requested.studioHome || requested.home || requested.path);
    if (!studioHome) {
      return { ok: false, error: "Pick a valid folder first." };
    }

    const currentPaths = selectStudioPathSet(getStudioPaths());
    const requestedOverrides = {
      dataDir: requested.dataDir,
      modelsDir: requested.modelsDir,
      cacheRoot: requested.cacheRoot,
      logsDir: requested.logsDir,
      externalDir: requested.externalDir,
    };
    const trimmedOverrides = trimStorageOverrides(requestedOverrides, studioHome);
    const targetPaths = syncStorageSettingsToProcessEnv(studioHome, trimmedOverrides);
    const pendingMigration = buildPendingMigration(currentPaths, targetPaths);

    const nextConfig = {
      ...readBootstrapConfig(),
      studioHome,
      storageSettings: trimmedOverrides,
      updatedAt: new Date().toISOString(),
    };
    if (pendingMigration) {
      nextConfig.pendingMigration = pendingMigration;
    } else {
      delete nextConfig.pendingMigration;
    }
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...stripNvidiaOnlyRemovedEnv(readLauncherEnv()),
      EDMG_STUDIO_HOME: studioHome,
      EDMG_STUDIO_DATA_DIR: targetPaths.dataDir,
      EDMG_STUDIO_MODELS_DIR: targetPaths.modelsDir,
      EDMG_STUDIO_CACHE_DIR: targetPaths.cacheRoot,
      EDMG_STUDIO_LOGS_DIR: targetPaths.logsDir,
      EDMG_STUDIO_EXTERNAL_DIR: targetPaths.externalDir,
    });

    return {
      ok: true,
      restartRequired: true,
      migrationPlanned: !!pendingMigration,
      migrationSummary: summarizePendingMigration(pendingMigration),
      ...targetPaths,
    };
  };

  ipcMain.handle("edmg:setStorageSettings", async (_event, nextSettings = {}) =>
    saveStorageSettings(nextSettings)
  );

  ipcMain.handle("edmg:setStudioHome", async (_event, targetPath) =>
    saveStorageSettings({ studioHome: targetPath })
  );

  ipcMain.handle("edmg:setAiSettings", async (_event, nextSettings = {}) => {
    const aiSettings = syncAiSettingsToProcessEnv(nextSettings);
    const nextConfig = {
      ...readBootstrapConfig(),
      aiSettings,
      updatedAt: new Date().toISOString(),
    };
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...stripNvidiaOnlyRemovedEnv(readLauncherEnv()),
      EDMG_AI_MODE: aiSettings.mode,
      EDMG_AI_PROVIDER: aiSettings.provider,
      EDMG_AI_BASE_URL: aiSettings.aiBaseUrl,
      EDMG_AI_OPENAI_COMPAT_BASE_URL: aiSettings.openaiCompatBaseUrl,
      EDMG_AI_OPENAI_COMPAT_MODEL: aiSettings.openaiCompatModel,
    });

    return {
      ok: true,
      restartRequired: true,
      ...aiSettings,
    };
  });

  ipcMain.handle("edmg:setBackendSettings", async (_event, nextSettings = {}) => {
    const backendSettings = syncBackendSettingsToProcessEnv(nextSettings);
    const nextConfig = {
      ...readBootstrapConfig(),
      backendSettings,
      updatedAt: new Date().toISOString(),
    };
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...readLauncherEnv(),
      EDMG_STUDIO_BACKEND_MODE: backendSettings.mode,
      EDMG_STUDIO_BACKEND_HOST: backendSettings.host,
      EDMG_STUDIO_BACKEND_PORT: backendSettings.port,
      EDMG_STUDIO_BACKEND_URL: backendSettings.url,
      EDMG_STUDIO_SPAWN_BACKEND: backendSettings.mode === "external" ? "0" : "1",
    });

    return {
      ok: true,
      restartRequired: true,
      currentBackendUrl: backendRuntime.getCurrentBackendUrl(),
      ...backendSettings,
    };
  });

  ipcMain.handle("edmg:setDirectorSettings", async (_event, nextSettings = {}) => {
    const directorSettings = syncDirectorSettingsToProcessEnv(nextSettings);
    const nextConfig = {
      ...readBootstrapConfig(),
      directorSettings,
      updatedAt: new Date().toISOString(),
    };
    writeBootstrapConfig(nextConfig);
    writeLauncherEnv({
      ...readLauncherEnv(),
      EDMG_DIRECTOR_BASE_URL: directorSettings.baseUrl,
    });
    await directorRuntime.restartDirector({
      directorPublicBaseUrl: directorSettings.baseUrl,
    });
    const status = await directorRuntime.getDirectorStatus();
    return {
      ...status,
      restartRequired: false,
    };
  });

  ipcMain.handle("edmg:openPath", async (_event, targetPath) => {
    const resolved = path.resolve(String(targetPath ?? ""));

    if (FAKE_PATH_ACTIONS) {
      return {
        ok: true,
        action: isExistingDirectory(resolved) ? "open_directory" : "open_file",
        path: resolved,
        fake: true,
      };
    }

    const error = await shell.openPath(resolved);
    if (error) {
      return { ok: false, error };
    }

    return {
      ok: true,
      action: isExistingDirectory(resolved) ? "open_directory" : "open_file",
      path: resolved,
      fake: false,
    };
  });

  ipcMain.handle("edmg:revealPath", async (_event, targetPath) => {
    const resolved = path.resolve(String(targetPath ?? ""));

    if (FAKE_PATH_ACTIONS) {
      return {
        ok: true,
        action: "reveal_file",
        path: resolved,
        fake: true,
      };
    }

    shell.showItemInFolder(resolved);
    return {
      ok: true,
      action: "reveal_file",
      path: resolved,
      fake: false,
    };
  });

  ipcMain.handle("edmg:pickFile", async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(windowRuntime.getMainWindow() ?? undefined, {
      title: options?.title ?? "Select file",
      defaultPath: options?.defaultPath,
      filters: Array.isArray(options?.filters) ? options.filters : undefined,
      properties: Array.isArray(options?.properties) && options.properties.length
        ? options.properties
        : ["openFile"],
    });

    if (result.canceled) {
      return { ok: false, canceled: true, paths: [] };
    }

    return { ok: true, canceled: false, paths: result.filePaths };
  });

  ipcMain.handle("edmg:pickDirectory", async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(windowRuntime.getMainWindow() ?? undefined, {
      title: options?.title ?? "Select folder",
      defaultPath: options?.defaultPath,
      properties: ["openDirectory", "createDirectory"],
    });

    if (result.canceled || !result.filePaths.length) {
      return { ok: false, canceled: true, path: "" };
    }

    return { ok: true, canceled: false, path: result.filePaths[0] };
  });

  ipcMain.handle("edmg:relaunch", async () => {
    app.relaunch();
    app.exit(0);
    return { ok: true };
  });

  ipcMain.handle("edmg:testWriteReport", async (_event, payload) => {
    return windowRuntime.writeTestReport(payload);
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  directorRuntime.stopDirector();
  backendRuntime.stopBackend();
});

app.on("activate", async () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    await windowRuntime.createMainWindow();
  }
});

app.whenReady().then(async () => {
  appendTestTrace("app.whenReady:start");
  if (TEST_SKIP_MIGRATION) {
    appendTestTrace("app.whenReady:skipMigration");
  } else {
    await runPendingStudioMigrationIfNeeded();
    appendTestTrace("app.whenReady:afterMigration");
  }
  registerIpcHandlers();
  appendTestTrace("app.whenReady:afterRegisterIpc");
  await backendRuntime.startBackendIfNeeded();
  appendTestTrace("app.whenReady:afterStartBackend");
  await directorRuntime.startDirectorIfNeeded();
  appendTestTrace("app.whenReady:afterStartDirector");
  await windowRuntime.createMainWindow();
  appendTestTrace("app.whenReady:afterCreateMainWindow");
}).catch((error) => {
  appendTestTrace(`app.whenReady:error ${String(error?.message ?? error)}`);
  console.error("[main] fatal startup error:", error);
  dialog.showErrorBox("EDMG NVIDIA Workshop failed to start", String(error?.message ?? error));
  app.quit();
});
