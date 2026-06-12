import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const DEFAULT_REQUEST_TIMEOUT_MS = Number(process.env.EDMG_STUDIO_PROOF_REQUEST_TIMEOUT_MS || (10 * 60 * 1000));

function log(message) {
  console.log(`[packaged-upgrade-proof] ${message}`);
}

function resolvePackagedApp() {
  const envPath = process.env.EDMG_STUDIO_PACKAGED_APP;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const exeName = process.platform === "win32" ? "EDMG NVIDIA Workshop.exe" : "EDMG NVIDIA Workshop";
  const candidates = [
    path.join(root, "dist", "win-unpacked", exeName),
    path.join(root, "release", "desktop-smoke-dist", "win-unpacked", exeName),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function chooseHomeRoot() {
  const preferred = process.env.EDMG_STUDIO_PROOF_ROOT;
  if (preferred) return preferred;
  if (process.platform === "win32" && fs.existsSync("D:\\")) return "D:\\";
  return os.tmpdir();
}

function chooseLegacyRoot(stamp) {
  if (process.platform === "win32" && process.env.LOCALAPPDATA) {
    return path.join(process.env.LOCALAPPDATA, `EDMG-Legacy-Proof-${stamp}`);
  }
  return path.join(chooseHomeRoot(), `EDMG-Legacy-Proof-${stamp}`);
}

function buildPathSet(studioHome) {
  const electronDir = path.join(studioHome, "electron");
  return {
    studioHome,
    dataDir: path.join(studioHome, "data"),
    modelsDir: path.join(studioHome, "models"),
    cacheRoot: path.join(studioHome, "cache"),
    logsDir: path.join(studioHome, "logs"),
    externalDir: path.join(studioHome, "external"),
    electronUserData: electronDir,
    sessionData: path.join(electronDir, "session"),
  };
}

function resolveBootstrapPaths() {
  const appDataDir = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  const localAppDataDir = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  const bootstrapDir = path.join(appDataDir, "EDMG NVIDIA Workshop");
  return {
    appDataDir,
    localAppDataDir,
    bootstrapDir,
    bootstrapPath: path.join(bootstrapDir, "bootstrap.json"),
  };
}

async function backupBootstrap(bootstrapPath, stamp) {
  if (!fs.existsSync(bootstrapPath)) {
    return { existed: false, backupPath: "" };
  }
  const backupPath = `${bootstrapPath}.codex-backup-${stamp}`;
  await fsp.copyFile(bootstrapPath, backupPath);
  return { existed: true, backupPath };
}

async function restoreBootstrap(bootstrapPath, backup) {
  if (backup?.existed && backup.backupPath && fs.existsSync(backup.backupPath)) {
    await fsp.mkdir(path.dirname(bootstrapPath), { recursive: true });
    await fsp.copyFile(backup.backupPath, bootstrapPath);
    await fsp.rm(backup.backupPath, { force: true });
    return;
  }
  await fsp.rm(bootstrapPath, { force: true });
}

async function allocatePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => resolve());
    server.on("error", reject);
  });
  const address = server.address();
  server.close();
  if (!address || typeof address === "string") throw new Error("Unable to allocate backend port");
  return address.port;
}

async function stopExistingPackagedProcesses(appExe) {
  if (process.platform !== "win32") return;
  const appDir = path.dirname(appExe || path.join(root, "dist", "win-unpacked")).replace(/'/g, "''");
  const command = `$ErrorActionPreference='SilentlyContinue'; $appDir='${appDir}'; Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($appDir, [System.StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`;
  await new Promise((resolve, reject) => {
    const child = spawn("powershell", ["-NoProfile", "-Command", command], { stdio: "ignore" });
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`Failed to stop stale packaged processes: ${code}`))));
    child.on("error", reject);
  });
  await new Promise((resolve) => setTimeout(resolve, 1500));
}

async function waitForHealth(baseUrl, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return await requestJson(`${baseUrl}/health`);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }
  throw new Error(`Backend never became healthy at ${baseUrl}`);
}

async function requestJson(url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    throw new Error(`${init.method || "GET"} ${url} failed: ${response.status} ${text}`);
  }
  return payload;
}

async function killProcessTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    await new Promise((resolve) => {
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      killer.on("exit", () => resolve());
      killer.on("error", () => resolve());
    });
    return;
  }
  child.kill("SIGTERM");
}

async function seedLegacyArtifacts(paths) {
  const fixtures = [
    { root: paths.dataDir, relative: path.join("projects", "legacy-proof.txt"), content: "legacy project data\n" },
    { root: paths.modelsDir, relative: path.join("checkpoints", "legacy-model.txt"), content: "legacy model placeholder\n" },
    { root: paths.cacheRoot, relative: path.join("tmp", "legacy-cache.txt"), content: "legacy cache entry\n" },
    { root: paths.logsDir, relative: "legacy.log", content: "legacy log entry\n" },
    { root: paths.externalDir, relative: path.join("bin", "legacy-tool.txt"), content: "legacy external tool marker\n" },
    { root: paths.electronUserData, relative: path.join("Default", "legacy-electron.txt"), content: "legacy electron data\n" },
  ];

  for (const fixture of fixtures) {
    const target = path.join(fixture.root, fixture.relative);
    await fsp.mkdir(path.dirname(target), { recursive: true });
    await fsp.writeFile(target, fixture.content, "utf8");
  }

  return fixtures.map((fixture) => ({
    label: path.relative(paths.studioHome, path.join(fixture.root, fixture.relative)),
    relative: fixture.relative,
    rootKey:
      fixture.root === paths.dataDir ? "dataDir"
        : fixture.root === paths.modelsDir ? "modelsDir"
          : fixture.root === paths.cacheRoot ? "cacheRoot"
            : fixture.root === paths.logsDir ? "logsDir"
              : fixture.root === paths.externalDir ? "externalDir"
                : "electronUserData",
    content: fixture.content,
  }));
}

async function main() {
  const appExe = resolvePackagedApp();
  assert.ok(appExe, "Packaged app not found. Run pnpm run dist:win first or set EDMG_STUDIO_PACKAGED_APP.");

  await stopExistingPackagedProcesses(appExe);

  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "_");
  const homeRoot = chooseHomeRoot();
  const targetHome = path.join(homeRoot, `EDMG-Upgraded-Proof-${stamp}`);
  const legacyHome = chooseLegacyRoot(stamp);
  const { appDataDir, localAppDataDir, bootstrapDir, bootstrapPath } = resolveBootstrapPaths();
  const bootstrapBackup = await backupBootstrap(bootstrapPath, stamp);
  const testPage = path.join(targetHome, "blank.html");
  const port = Number(process.env.EDMG_STUDIO_PROOF_PORT || (await allocatePort()));
  const baseUrl = `http://127.0.0.1:${port}`;

  await fsp.mkdir(targetHome, { recursive: true });
  await fsp.mkdir(bootstrapDir, { recursive: true });
  await fsp.mkdir(localAppDataDir, { recursive: true });
  await fsp.writeFile(testPage, "<!doctype html><html><body>packaged upgrade proof</body></html>\n");

  const source = buildPathSet(legacyHome);
  const target = buildPathSet(targetHome);
  const seeded = await seedLegacyArtifacts(source);

  const bootstrap = {
    studioHome: targetHome,
    pendingMigration: {
      requestedAt: new Date().toISOString(),
      source,
      target,
    },
    updatedAt: new Date().toISOString(),
  };
  await fsp.writeFile(bootstrapPath, JSON.stringify(bootstrap, null, 2) + "\n", "utf8");

  log(`launching ${appExe}`);
  const child = spawn(appExe, [], {
    cwd: path.dirname(appExe),
    env: {
      ...process.env,
      EDMG_STUDIO_BACKEND_HOST: "127.0.0.1",
      EDMG_STUDIO_BACKEND_PORT: String(port),
      EDMG_STUDIO_TEST_MODE: "1",
      EDMG_STUDIO_TEST_PAGE: testPage,
      EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS: "1",
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
    stdio: "ignore",
  });

  try {
    const health = await waitForHealth(baseUrl);
    const config = await requestJson(`${baseUrl}/v1/config`);
    const bootstrapAfter = JSON.parse(await fsp.readFile(bootstrapPath, "utf8"));
    const migration = bootstrapAfter?.lastMigration ?? null;

    assert.equal(config?.studio_home, targetHome, "Packaged backend should resolve to the target Workshop home after migration");
    assert.equal(config?.data_dir, target.dataDir, "Packaged backend should resolve to the migrated data dir");
    assert.equal(Boolean(bootstrapAfter?.pendingMigration), false, "Pending migration should be cleared after launch");
    assert.equal(Boolean(migration?.ok), true, "Last migration summary should report success");

    for (const fixture of seeded) {
      const targetRoot = target[fixture.rootKey];
      const expectedPath = path.join(targetRoot, fixture.relative);
      assert.equal(fs.existsSync(expectedPath), true, `Migrated artifact missing: ${expectedPath}`);
      const content = await fsp.readFile(expectedPath, "utf8");
      assert.equal(content, fixture.content, `Migrated artifact content mismatch: ${expectedPath}`);
    }

    const failedResults = Array.isArray(migration?.results) ? migration.results.filter((entry) => entry?.status === "failed") : [];
    assert.equal(failedResults.length, 0, `Upgrade migration reported failures: ${JSON.stringify(failedResults)}`);

    const summary = {
      ok: true,
      baseUrl,
      health,
      legacyHome,
      targetHome,
      appDataDir,
      localAppDataDir,
      bootstrapPath,
      config: {
        studioHome: config?.studio_home ?? null,
        dataDir: config?.data_dir ?? null,
        modelsDir: config?.models_dir ?? null,
        logsDir: config?.logs_dir ?? null,
        externalDir: config?.external_dir ?? null,
      },
      migration: {
        ok: migration?.ok ?? false,
        requestedAt: migration?.requestedAt ?? null,
        completedAt: migration?.completedAt ?? null,
        resultLabels: Array.isArray(migration?.results) ? migration.results.map((entry) => `${entry.label}:${entry.status}`) : [],
      },
      seededArtifacts: seeded.map((fixture) => ({
        label: fixture.label,
        migratedTo: path.join(target[fixture.rootKey], fixture.relative),
      })),
    };

    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await killProcessTree(child);
    await stopExistingPackagedProcesses(appExe);
    await restoreBootstrap(bootstrapPath, bootstrapBackup);
  }
}

main().catch((error) => {
  console.error("[packaged-upgrade-proof] FAILED", error);
  process.exit(1);
});
