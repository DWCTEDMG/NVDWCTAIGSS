import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const DEFAULT_REQUEST_TIMEOUT_MS = Number(process.env.EDMG_STUDIO_PROOF_REQUEST_TIMEOUT_MS || (10 * 60 * 1000));
const DEFAULT_HEAVY_REQUEST_TIMEOUT_MS = Number(process.env.EDMG_STUDIO_PROOF_HEAVY_TIMEOUT_MS || (20 * 60 * 1000));

function log(message) {
  console.log(`[packaged-customer-flow] ${message}`);
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

function resolveAudioFixture() {
  const envPath = process.env.EDMG_STUDIO_AUDIO_FIXTURE;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const candidate = path.resolve(root, "..", "..", "juce_example", "out", "build", "x64-Debug", "_deps", "juce-src", "examples", "Assets", "cassette_recorder.wav");
  return fs.existsSync(candidate) ? candidate : "";
}

async function createSyntheticAudioFixture(targetPath, {
  durationSeconds = 8,
  sampleRate = 44100,
  channels = 2,
  frequencyHz = 110,
  amplitude = 0.28,
} = {}) {
  const totalFrames = Math.max(1, Math.floor(durationSeconds * sampleRate));
  const bytesPerSample = 2;
  const blockAlign = channels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = totalFrames * blockAlign;
  const buffer = Buffer.alloc(44 + dataSize);

  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(channels, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(byteRate, 28);
  buffer.writeUInt16LE(blockAlign, 32);
  buffer.writeUInt16LE(bytesPerSample * 8, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataSize, 40);

  for (let frame = 0; frame < totalFrames; frame += 1) {
    const t = frame / sampleRate;
    const envelope = Math.min(1, frame / (sampleRate * 0.2), (totalFrames - frame) / (sampleRate * 0.2));
    const left = Math.sin(2 * Math.PI * frequencyHz * t) * amplitude * envelope;
    const right = Math.sin(2 * Math.PI * (frequencyHz * 1.5) * t) * amplitude * envelope;
    const samples = [left, right];
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = samples[channel % samples.length];
      const clamped = Math.max(-1, Math.min(1, sample));
      buffer.writeInt16LE(Math.round(clamped * 32767), 44 + (frame * channels + channel) * bytesPerSample);
    }
  }

  await fsp.mkdir(path.dirname(targetPath), { recursive: true });
  await fsp.writeFile(targetPath, buffer);
  return targetPath;
}

async function ensureAudioFixture() {
  const existing = resolveAudioFixture();
  if (existing) return { audioFixture: existing, generated: false };

  const fixturePath = path.join(os.tmpdir(), "edmg-packaged-customer-flow.wav");
  await createSyntheticAudioFixture(fixturePath);
  return { audioFixture: fixturePath, generated: true };
}

function chooseHomeRoot() {
  const preferred = process.env.EDMG_STUDIO_PROOF_ROOT;
  if (preferred) return preferred;
  if (process.platform === "win32" && fs.existsSync("D:\\")) return "D:\\";
  return os.tmpdir();
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

function resolveTimeoutMs(value, fallback = DEFAULT_REQUEST_TIMEOUT_MS) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function requestJsonViaNode(url, init, timeoutMs) {
  const target = new URL(url);
  const transport = target.protocol === "https:" ? https : http;
  const body = typeof init.body === "string" || Buffer.isBuffer(init.body) ? init.body : null;
  const headers = {
    accept: "application/json",
    ...(init.headers || {}),
  };
  if (body && headers["content-length"] == null) {
    headers["content-length"] = Buffer.byteLength(body);
  }

  return new Promise((resolve, reject) => {
    const request = transport.request(
      target,
      {
        method: init.method || "GET",
        headers,
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          clearTimeout(timer);
          const text = Buffer.concat(chunks).toString("utf8");
          let payload = null;
          if (text) {
            try {
              payload = JSON.parse(text);
            } catch {
              payload = { raw: text };
            }
          }
          if ((response.statusCode || 500) >= 400) {
            if (init.allowError) {
              resolve({ ok: false, statusCode: response.statusCode, payload, text });
              return;
            }
            reject(new Error(`${init.method || "GET"} ${url} failed: ${response.statusCode} ${text}`));
            return;
          }
          resolve(payload);
        });
      },
    );

    const timer = setTimeout(() => {
      request.destroy(new Error(`${init.method || "GET"} ${url} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    request.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });

    if (body) {
      request.write(body);
    }
    request.end();
  });
}

async function requestJson(url, init = {}) {
  const timeoutMs = resolveTimeoutMs(init.timeoutMs);
  if (!(init.body instanceof FormData)) {
    return requestJsonViaNode(url, init, timeoutMs);
  }
  const response = await fetch(url, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
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
    if (init.allowError) {
      return { ok: false, statusCode: response.status, payload, text };
    }
    throw new Error(`${init.method || "GET"} ${url} failed: ${response.status} ${text}`);
  }
  return payload;
}

async function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function uploadAudio(url, audioPath) {
  const form = new FormData();
  const bytes = await fsp.readFile(audioPath);
  form.set("file", new Blob([bytes]), path.basename(audioPath));
  return requestJson(url, { method: "POST", body: form });
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

async function main() {
  const appExe = resolvePackagedApp();
  assert.ok(appExe, "Packaged app not found. Run pnpm run dist:win first or set EDMG_STUDIO_PACKAGED_APP.");
  const { audioFixture, generated: generatedAudioFixture } = await ensureAudioFixture();
  assert.ok(audioFixture, "Audio fixture not found and synthetic fallback generation failed.");
  const audioBytes = (await fsp.stat(audioFixture)).size;
  const heavyRequestTimeoutMs = Math.max(
    DEFAULT_HEAVY_REQUEST_TIMEOUT_MS,
    Math.ceil(audioBytes / (1024 * 1024)) * 5000,
  );

  await stopExistingPackagedProcesses(appExe);

  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "_");
  const homeRoot = chooseHomeRoot();
  const studioHome = path.join(homeRoot, `EDMG-Packaged-Proof-${stamp}`);
  const { appDataDir, localAppDataDir, bootstrapPath } = resolveBootstrapPaths();
  const bootstrapBackup = await backupBootstrap(bootstrapPath, stamp);
  await fsp.mkdir(studioHome, { recursive: true });
  await fsp.mkdir(path.dirname(bootstrapPath), { recursive: true });
  await fsp.rm(bootstrapPath, { force: true });
  const testPage = path.join(studioHome, "blank.html");
  await fsp.writeFile(testPage, "<!doctype html><html><body>packaged customer flow</body></html>\n");
  const port = Number(process.env.EDMG_STUDIO_PROOF_PORT || (await allocatePort()));
  const baseUrl = `http://127.0.0.1:${port}`;

  log(`launching ${appExe}`);
  if (generatedAudioFixture) {
    log(`generated synthetic audio fixture at ${audioFixture}`);
  }
  log(`using heavy request timeout ${heavyRequestTimeoutMs}ms for ${path.basename(audioFixture)} (${audioBytes} bytes)`);
  const child = spawn(appExe, [], {
    cwd: path.dirname(appExe),
    env: {
      ...process.env,
      EDMG_STUDIO_HOME: studioHome,
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
    const status = await requestJson(`${baseUrl}/v1/setup/status`);
    const config = await requestJson(`${baseUrl}/v1/config`);
    const created = await postJson(`${baseUrl}/v1/projects`, { name: "Packaged Customer Proof" });
    const projectId = created?.project?.id;
    assert.ok(projectId, "Project creation did not return an id");

    const upload = await uploadAudio(`${baseUrl}/v1/projects/${projectId}/assets/audio`, audioFixture);
    const analyze = await requestJson(`${baseUrl}/v1/projects/${projectId}/analyze_audio`, {
      method: "POST",
      timeoutMs: heavyRequestTimeoutMs,
    });
    const plan = await postJson(`${baseUrl}/v1/projects/${projectId}/plan?mode=local`, {
      title: "Packaged Customer Proof",
      style_prefs: "audio reactive neon performance visuals",
      num_variants: 1,
      max_scenes: 4,
    });
    const apply = await postJson(`${baseUrl}/v1/projects/${projectId}/timeline/apply_plan`, { variant_index: 0, overwrite: true });
    const validate = await requestJson(
      `${baseUrl}/v1/projects/${projectId}/pipeline/validate?variant_index=0&preset=fast&mode=auto&engine=auto`,
      { allowError: true },
    );
    const failClosedCode = validate?.payload?.error?.code || validate?.error?.code || "";
    const validateFailedClosed = validate?.ok === false && failClosedCode === "NVIDIA_RENDER_MODEL_REQUIRED";
    let run = null;
    let targetJobId = "";
    if (!validateFailedClosed) {
      run = await requestJson(`${baseUrl}/v1/projects/${projectId}/pipeline/run?variant_index=0&preset=fast&mode=auto&engine=auto`, { method: "POST" });
      targetJobId = run?.job?.id || run?.assemble_job?.id;
      assert.ok(targetJobId, `Pipeline run did not return a target job payload: ${JSON.stringify(run)}`);
    }

    let job = null;
    const tickHistory = [];
    if (targetJobId) {
      const tickDeadline = Date.now() + 6 * 60 * 1000;
      while (Date.now() < tickDeadline) {
        const tick = await requestJson(`${baseUrl}/v1/jobs/tick`, { method: "POST" });
        if (tick?.job) {
          tickHistory.push({ id: tick.job.id, status: tick.job.status, type: tick.job.type });
          if (tick.job.id === targetJobId) {
            job = tick.job;
            if (["succeeded", "failed", "canceled"].includes(String(job.status))) break;
          }
        }
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    }
    const jobs = await requestJson(`${baseUrl}/v1/projects/${projectId}/jobs`);
    if (targetJobId && !job && Array.isArray(jobs?.jobs)) {
      job = jobs.jobs.find((entry) => entry.id === targetJobId) || null;
    }
    if (targetJobId) {
      assert.ok(job, `Did not observe the packaged target job ${targetJobId} in tick responses or job list`);
    }
    const outputs = await requestJson(`${baseUrl}/v1/projects/${projectId}/outputs`);
    const comfyOk = Boolean(status?.comfyui?.ok);
    const usesInternalPipeline = Boolean(run?.job?.id);
    const expectedDataDir = path.join(studioHome, "data");
    const expectedModelsDir = path.join(studioHome, "models");
    const expectedExternalDir = path.join(studioHome, "external");
    const expectedLogsDir = path.join(studioHome, "logs");
    const summary = {
      ok: validateFailedClosed || job?.status === "succeeded",
      studioHome,
      appDataDir,
      localAppDataDir,
      bootstrapPath,
      bootstrapCreated: fs.existsSync(bootstrapPath),
      baseUrl,
      health,
      paths: {
        studioHome: config?.studio_home ?? null,
        dataDir: config?.data_dir ?? null,
        modelsDir: config?.models_dir ?? null,
        logsDir: config?.logs_dir ?? null,
        externalDir: config?.external_dir ?? null,
        expectedDataDir,
        expectedModelsDir,
        expectedLogsDir,
        expectedExternalDir,
        dataDirExists: fs.existsSync(expectedDataDir),
        modelsDirExists: fs.existsSync(expectedModelsDir),
        logsDirExists: fs.existsSync(expectedLogsDir),
        externalDirExists: fs.existsSync(expectedExternalDir),
      },
      setupStatus: {
        backendBundleOk: status?.backend_bundle?.ok,
        ffmpegOk: status?.ffmpeg?.ok,
        edmgAvailable: status?.edmg?.available,
        edmgInstallable: status?.edmg?.installable,
        edmgRepoRoot: status?.edmg?.repo_root ?? null,
        sevenZipOk: status?.sevenzip?.ok,
        sevenZipPath: status?.sevenzip?.path ?? null,
        comfyOk: status?.comfyui?.ok,
      },
      projectId,
      uploadOk: Boolean(upload?.ok),
      analyzeKeys: Object.keys((analyze && typeof analyze === "object" ? analyze.analysis : {}) || {}),
      variantCount: Array.isArray(plan?.variants) ? plan.variants.length : 0,
      trackCount: Array.isArray(apply?.timeline?.tracks) ? apply.timeline.tracks.length : 0,
      validate: {
        ok: validate?.ok !== false,
        failClosed: validateFailedClosed,
        errorCode: failClosedCode || null,
        errorMessage: validate?.payload?.error?.message || validate?.error?.message || null,
        mode: validate?.recommended?.mode,
        engine: validate?.recommended?.engine,
        modelId: validate?.recommended?.model_id,
        reason: validate?.recommended?.reason,
        diagnostics: Array.isArray(validate?.recommended?.diagnostics) ? validate.recommended.diagnostics : [],
      },
      run: {
        pathType: usesInternalPipeline ? "internal_nvidia_pipeline" : "queued_comfy_pipeline",
        renderMode: run?.render_mode,
        selectedMode: run?.selected?.mode,
        selectedEngine: run?.selected?.engine,
        selectedModel: run?.selected?.model_id,
        preflightMode: run?.preflight?.mode,
        renderEnqueued: run?.render_enqueued ?? null,
        assembleJobId: run?.assemble_job?.id ?? null,
      },
      job: {
        id: job?.id || null,
        status: job?.status || null,
        type: job?.type || null,
        error: job?.error || null,
      },
      outputs: {
        videoCount: Array.isArray(outputs?.videos) ? outputs.videos.length : 0,
        latestMode: outputs?.latest_internal_render?.mode || null,
        latestVideo: outputs?.latest_internal_render?.video || null,
        activeInternalJobs: Array.isArray(outputs?.active_internal_jobs)
          ? outputs.active_internal_jobs.map((entry) => `${entry.id}:${entry.status}`)
          : [],
      },
      tickHistory,
      projectJobs: Array.isArray(jobs?.jobs)
        ? jobs.jobs.map((entry) => ({ id: entry.id, status: entry.status, type: entry.type, error: entry.error || null }))
        : [],
    };

    console.log(JSON.stringify(summary, null, 2));

    assert.equal(summary.setupStatus.backendBundleOk, true, "Packaged backend bundle should be available");
    assert.equal(summary.setupStatus.ffmpegOk, true, "Bundled FFmpeg should be available");
    assert.equal(summary.setupStatus.edmgAvailable, true, "Bundled EDMG Core should be available");
    assert.equal(summary.paths.studioHome, studioHome, "Packaged config should report the requested Workshop home");
    assert.equal(summary.paths.dataDir, expectedDataDir, "Packaged config data_dir should live under Workshop home");
    assert.equal(summary.paths.modelsDir, expectedModelsDir, "Packaged config models_dir should live under Workshop home");
    assert.equal(summary.paths.logsDir, expectedLogsDir, "Packaged config logs_dir should live under Workshop home");
    assert.equal(summary.paths.externalDir, expectedExternalDir, "Packaged config external_dir should live under Workshop home");
    assert.equal(summary.paths.dataDirExists, true, "Packaged run should create the Workshop data root");
    assert.equal(summary.paths.modelsDirExists, true, "Packaged run should create the Workshop models root");
    assert.equal(summary.paths.logsDirExists, true, "Packaged run should create the Workshop logs root");
    assert.equal(summary.paths.externalDirExists, true, "Packaged run should create the Workshop external root");
    assert.equal(summary.variantCount > 0, true, "Expected at least one planned variant");
    assert.equal(summary.trackCount > 0, true, "Expected timeline tracks after apply");
    if (summary.validate.failClosed) {
      assert.equal(summary.validate.errorCode, "NVIDIA_RENDER_MODEL_REQUIRED", "Packaged pipeline should fail closed when no NVIDIA render model is installed");
      assert.equal(summary.job.status, null, "Fail-closed validation should not enqueue a render job");
      assert.equal(summary.outputs.videoCount, 0, "Fail-closed validation should not produce fallback videos");
    } else if (!comfyOk) {
      assert.equal(summary.job.status, "succeeded", "Packaged NVIDIA render job should succeed when a CUDA internal model is installed");
      assert.equal(summary.outputs.videoCount > 0, true, "Expected rendered videos in outputs");
      assert.equal(String(summary.validate.mode), "internal", `Expected NVIDIA internal recommendation when ComfyUI is unavailable, got ${summary.validate.mode}`);
      if (summary.run.renderMode) {
        assert.equal(String(summary.run.renderMode), "internal", `Expected NVIDIA internal packaged render mode, got ${summary.run.renderMode}`);
      }
      if (summary.outputs.latestMode) {
        assert.equal(String(summary.outputs.latestMode), "internal", `Expected latest packaged NVIDIA mode, got ${summary.outputs.latestMode}`);
      }
    } else {
      assert.equal(summary.job.status, "succeeded", "Packaged ComfyUI render job should succeed when ComfyUI is available");
      assert.equal(summary.outputs.videoCount > 0, true, "Expected rendered videos in outputs");
      assert.equal(["stills", "motion"].includes(String(summary.validate.mode)), true, `Expected stills/motion recommendation when ComfyUI is available, got ${summary.validate.mode}`);
      assert.equal(Boolean(summary.run.assembleJobId), true, "ComfyUI path should return an assemble job id");
    }
  } finally {
    await killProcessTree(child);
    await stopExistingPackagedProcesses(appExe);
    await restoreBootstrap(bootstrapPath, bootstrapBackup);
  }
}

main().catch((error) => {
  console.error("[packaged-customer-flow] FAILED", error);
  process.exit(1);
});
