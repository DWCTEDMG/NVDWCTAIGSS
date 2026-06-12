import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function log(msg) {
  console.log(`[packaged-desktop-smoke] ${msg}`);
}

function canLaunchElectron() {
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    const xvfb = "/usr/bin/xvfb-run";
    if (!fs.existsSync(xvfb)) {
      return { ok: false, reason: "No DISPLAY/WAYLAND session and xvfb-run not available for unpacked desktop launch." };
    }
  }
  return { ok: true, reason: "Unpacked desktop launch supported" };
}

import { assertDesktopArtifacts, stageDesktopRelease } from './release-stage-lib.mjs';

function bundledResourcePaths(appDir) {
  return {
    backendExe: path.join(appDir, "electron-resources", "backend", process.platform === "win32" ? "edmg-studio-backend.exe" : "edmg-studio-backend"),
    backendManifest: path.join(appDir, "electron-resources", "backend", "backend-bundle-manifest.json"),
    ffmpegExe: path.join(appDir, "electron-resources", "bin", process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg"),
  };
}

function resolveUnpackedApp(outputDir) {
  const candidates = [
    path.join(outputDir, "win-unpacked", process.platform === "win32" ? "EDMG NVIDIA Workshop.exe" : "EDMG NVIDIA Workshop"),
    path.join(outputDir, "linux-unpacked", "edmg-nvidia-workshop"),
    path.join(outputDir, "mac", "EDMG NVIDIA Workshop.app", "Contents", "MacOS", "EDMG NVIDIA Workshop"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return "";
}

async function buildUnpackedDesktopApp(appDir, outputDir) {
  await fsp.rm(outputDir, { recursive: true, force: true });
  const builderScript = path.join(root, "scripts", "run-electron-builder.mjs");
  const result = spawnSync(
    process.execPath,
    [
      builderScript,
      "--dir",
      `-c.directories.app=${appDir}`,
      `-c.directories.output=${outputDir}`,
    ],
    {
      cwd: root,
      stdio: "inherit",
      env: process.env,
    },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`electron-builder --dir failed with exit code ${result.status}`);
  }

  const unpackedApp = resolveUnpackedApp(outputDir);
  assert.ok(unpackedApp, `Unpacked app not found under ${outputDir}`);
  return unpackedApp;
}

async function startMockBackend() {
  const server = http.createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, service: "packaged-desktop-smoke-mock" }));
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: { message: "Not found" } }));
  });
  await new Promise((resolve, reject) => {
    server.listen(0, "127.0.0.1", () => resolve());
    server.on("error", reject);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Unable to bind mock backend");
  return { server, port: address.port };
}

function buildIsolatedDesktopEnv(fixtureRoot) {
  const appDataDir = path.join(fixtureRoot, "appdata", "Roaming");
  const localAppDataDir = path.join(fixtureRoot, "appdata", "Local");
  const studioHome = path.join(fixtureRoot, "studio-home");
  return {
    APPDATA: appDataDir,
    LOCALAPPDATA: localAppDataDir,
    EDMG_STUDIO_HOME: studioHome,
  };
}

async function waitForFile(filePath, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (fs.existsSync(filePath) && fs.statSync(filePath).size > 0) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for ${filePath}`);
}

async function runStagedAppProbe() {
  const support = canLaunchElectron();
  const smokeStageDir = path.join(root, "release", "staged-app-smoke");
  const smokeOutputDir = path.join(root, "release", "desktop-smoke-dist");
  const stageManifest = await stageDesktopRelease({ outDir: smokeStageDir, clean: true });
  const appDir = stageManifest.stageDir;
  const unpackedApp = await buildUnpackedDesktopApp(appDir, smokeOutputDir);
  const summary = {
    ok: true,
    skipped: !support.ok,
    reason: support.reason,
    appDir,
    smokeOutputDir,
    unpackedApp,
    stageManifestPath: path.join(appDir, '.edmg-stage', 'manifest.json'),
  };
  const resources = bundledResourcePaths(appDir);

  const distIndex = path.join(appDir, "dist-web", "index.html");
  assert.ok(fs.existsSync(distIndex), "staged app dist-web/index.html missing");
  assert.ok(fs.existsSync(path.join(appDir, 'electron-builder.yml')), 'staged app electron-builder.yml missing');
  assert.ok(fs.existsSync(resources.backendExe), `staged app missing bundled backend: ${resources.backendExe}`);
  assert.ok(fs.existsSync(resources.backendManifest), `staged app missing backend bundle manifest: ${resources.backendManifest}`);
  assert.ok(fs.existsSync(resources.ffmpegExe), `staged app missing bundled ffmpeg: ${resources.ffmpegExe}`);
  const backendManifest = JSON.parse(await fsp.readFile(resources.backendManifest, "utf8"));
  assert.equal(typeof backendManifest.sourceHash, "string", "backend bundle manifest must include sourceHash");
  assert.equal(typeof backendManifest.binarySha256, "string", "backend bundle manifest must include binarySha256");
  summary.resources = resources;
  summary.backendManifest = backendManifest;

  if (!support.ok) {
    return summary;
  }

  const fixtureRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "edmg-packaged-probe-"));
  const fixtureDir = path.join(fixtureRoot, "frames");
  await fsp.mkdir(fixtureDir, { recursive: true });
  const fixtureFile = path.join(fixtureRoot, "demo.txt");
  await fsp.writeFile(fixtureFile, "packaged desktop smoke probe\n");
  const reportPath = path.join(fixtureRoot, "report.json");
  const isolatedEnv = buildIsolatedDesktopEnv(fixtureRoot);

  const { server, port } = await startMockBackend();
  const expectedBackendUrl = `http://127.0.0.1:${port}`;

  const args = [];
  let cmd = unpackedApp;
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY && fs.existsSync("/usr/bin/xvfb-run")) {
    cmd = "/usr/bin/xvfb-run";
    args.push("-a", unpackedApp);
  }

  log(`launching staged app probe using ${cmd}`);
  const child = spawn(cmd, args, {
    cwd: path.dirname(unpackedApp),
    env: {
      ...process.env,
      EDMG_STUDIO_TEST_MODE: "1",
      EDMG_STUDIO_TEST_SKIP_MIGRATION: "1",
      EDMG_STUDIO_TEST_REPORT_PATH: reportPath,
      EDMG_STUDIO_TEST_PROBE_REVEAL_PATH: fixtureFile,
      EDMG_STUDIO_TEST_PROBE_OPEN_PATH: fixtureDir,
      EDMG_STUDIO_TEST_EXPECT_BACKEND_URL: expectedBackendUrl,
      EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS: "1",
      EDMG_STUDIO_SPAWN_BACKEND: "0",
      EDMG_STUDIO_BACKEND_PORT: String(port),
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
      ...isolatedEnv,
    },
    stdio: "inherit",
  });

  let exitCode = null;
  child.on("exit", (code) => {
    exitCode = code;
  });

  try {
    await waitForFile(reportPath, 25000);
    const report = JSON.parse(await fsp.readFile(reportPath, "utf8"));
    assert.equal(report.ok, true, `Staged app probe failed: ${JSON.stringify(report)}`);
    assert.equal(report.backendUrlSync, expectedBackendUrl);
    assert.equal(report.backendUrlAsync, expectedBackendUrl);
    assert.equal(report.reveal?.ok, true);
    assert.equal(report.open?.ok, true);
    return { ...summary, skipped: false, report };
  } finally {
    server.close();
    if (exitCode === null) child.kill("SIGTERM");
  }
}

async function main() {
  assertDesktopArtifacts();
  log("build artifact checks passed");

  const stagedProbe = await runStagedAppProbe();
  if (stagedProbe.skipped) {
    log(`staged app probe skipped: ${stagedProbe.reason}`);
  } else {
    log("staged app probe passed");
  }

  console.log(JSON.stringify({ ok: true, buildArtifacts: true, stagedProbe }, null, 2));
}

main().catch((error) => {
  console.error("[packaged-desktop-smoke] FAILED", error);
  process.exit(1);
});
