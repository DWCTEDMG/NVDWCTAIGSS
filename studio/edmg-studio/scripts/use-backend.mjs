import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const [modeArg, valueArg] = process.argv.slice(2);
const DEFAULT_LOCAL_HOST = "127.0.0.1";
const DEFAULT_LOCAL_PORT = 7863;

function fail(msg) {
  console.error(`[backend:use] ${msg}`);
  process.exit(1);
}

function writeUtf8NoBom(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, { encoding: "utf8" });
}

function upsertEnv(file, updates) {
  let lines = [];
  if (fs.existsSync(file)) {
    lines = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "").split(/\r?\n/);
  }

  const used = new Set();
  const next = lines
    .filter((line) => line.trim() !== "")
    .map((line) => {
      const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=/);
      if (!match) return line;
      const key = match[1];
      if (!(key in updates)) return line;
      used.add(key);
      return `${key}=${updates[key]}`;
    });

  for (const [key, val] of Object.entries(updates)) {
    if (!used.has(key)) next.push(`${key}=${val}`);
  }

  writeUtf8NoBom(file, `${next.join("\n")}\n`);
}

function readJson(file) {
  if (!fs.existsSync(file)) return {};
  const raw = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "").trim();
  if (!raw) return {};
  return JSON.parse(raw);
}

function writeJson(file, value) {
  writeUtf8NoBom(file, `${JSON.stringify(value, null, 2)}\n`);
}

function normalizeUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || "").trim());
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

function updateLauncherEnvJson(json, config) {
  return {
    ...json,
    EDMG_STUDIO_BACKEND_MODE: config.mode,
    EDMG_STUDIO_BACKEND_HOST: config.host,
    EDMG_STUDIO_BACKEND_PORT: String(config.port),
    EDMG_STUDIO_BACKEND_URL: config.url,
    EDMG_STUDIO_SPAWN_BACKEND: config.spawnBackend ? "1" : "0",
    EDMG_DIRECTOR_SPAWN: config.directorSpawn ? "1" : "0",
    backendMode: config.mode,
    backendUrl: config.activeUrl,
    currentBackendUrl: config.activeUrl,
    spawnBackend: config.spawnBackend,
    directorSpawn: config.directorSpawn,
    localBackendHost: DEFAULT_LOCAL_HOST,
    localBackendPort: config.localPort,
  };
}

function updateRuntimeDefaultsJson(json, config) {
  return {
    ...json,
    backend: {
      ...(json.backend && typeof json.backend === "object" ? json.backend : {}),
      host: config.host,
      port: config.port,
      url: config.url,
      spawnBackend: config.spawnBackend,
    },
  };
}

function updateBootstrapJson(json, config) {
  return {
    ...json,
    backendSettings: {
      mode: config.mode,
      host: config.host,
      port: String(config.port),
      url: config.url,
    },
    backendMode: config.mode,
    backendUrl: config.activeUrl,
    currentBackendUrl: config.activeUrl,
    spawnBackend: config.spawnBackend,
    directorSpawn: config.directorSpawn,
    localBackendHost: DEFAULT_LOCAL_HOST,
    localBackendPort: config.localPort,
  };
}

let config;

if (modeArg === "external") {
  const normalizedUrl = normalizeUrl(valueArg);
  if (!normalizedUrl) {
    fail("Usage: pnpm backend:use external http://IP:PORT");
  }

  config = {
    mode: "external",
    host: DEFAULT_LOCAL_HOST,
    port: DEFAULT_LOCAL_PORT,
    url: normalizedUrl,
    activeUrl: normalizedUrl,
    spawnBackend: false,
    directorSpawn: false,
    localPort: DEFAULT_LOCAL_PORT,
  };
} else if (modeArg === "local" || modeArg === "managed") {
  const port = Number(valueArg || DEFAULT_LOCAL_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    fail("Usage: pnpm backend:use managed 7863");
  }
  const localUrl = `http://${DEFAULT_LOCAL_HOST}:${port}`;

  config = {
    mode: "managed",
    host: DEFAULT_LOCAL_HOST,
    port,
    url: "",
    activeUrl: localUrl,
    spawnBackend: true,
    directorSpawn: false,
    localPort: port,
  };
} else {
  fail("Usage: pnpm backend:use external http://IP:PORT  OR  pnpm backend:use managed 7863");
}

const envUpdates = {
  EDMG_STUDIO_BACKEND_MODE: config.mode,
  EDMG_STUDIO_BACKEND_URL: config.url,
  EDMG_STUDIO_BACKEND_HOST: config.host,
  EDMG_STUDIO_BACKEND_PORT: String(config.port),
  EDMG_BACKEND_URL: config.activeUrl,
  VITE_EDMG_BACKEND_URL: config.activeUrl,
  EDMG_STUDIO_SPAWN_BACKEND: config.spawnBackend ? "1" : "0",
  EDMG_DIRECTOR_SPAWN: config.directorSpawn ? "1" : "0",
  EDMG_STUDIO_LOCAL_BACKEND_HOST: DEFAULT_LOCAL_HOST,
  EDMG_STUDIO_LOCAL_BACKEND_PORT: String(config.localPort)
};

upsertEnv(path.join(root, ".env"), envUpdates);
upsertEnv(path.join(root, ".env.local"), envUpdates);

const launcherEnvFile = path.join(root, "launcher_env.json");
writeJson(launcherEnvFile, updateLauncherEnvJson(readJson(launcherEnvFile), config));

const runtimeDefaultsFile = path.join(root, "electron-resources", "runtime-defaults.json");
writeJson(runtimeDefaultsFile, updateRuntimeDefaultsJson(readJson(runtimeDefaultsFile), config));

const appData = process.env.APPDATA;
if (appData) {
  const bootstrap = path.join(appData, "EDMG NVIDIA Workshop", "bootstrap.json");
  writeJson(bootstrap, updateBootstrapJson(readJson(bootstrap), config));
}

console.log(`[backend:use] mode=${config.mode}`);
console.log(`[backend:use] url=${config.activeUrl}`);
console.log(`[backend:use] spawnBackend=${config.spawnBackend}`);
console.log("[backend:use] done");
