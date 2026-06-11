import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

export function createBackendRuntime({
  app,
  dialog,
  rootDir,
  isWindows,
  backendHost,
  backendPort,
  backendUrl,
  backendReadyTimeoutMs,
  testMode,
  pathExistsSync,
  ensureDirSync,
  safeStreamWrite,
  getStudioPaths,
  buildManagedStudioEnv,
  buildManagedAiEnv,
}) {
  let currentBackendUrl = backendUrl || `http://${backendHost}:${backendPort}`;
  let backendProc = null;

  function logBackendUrlMarker() {
    console.log(`EDMG_BACKEND_URL=${currentBackendUrl}`);
  }

  function getCurrentBackendUrl() {
    return currentBackendUrl;
  }

  function getDevVenvPythonPath() {
    if (isWindows) {
      return path.join(rootDir, "python_backend", "venv", "Scripts", "python.exe");
    }

    return path.join(rootDir, "python_backend", "venv", "bin", "python");
  }

  function getDevPythonPath() {
    const backendExplicit = String(process.env.EDMG_STUDIO_BACKEND_PYTHON ?? "").trim();
    if (backendExplicit) return backendExplicit;

    const venvPython = getDevVenvPythonPath();
    if (pathExistsSync(venvPython)) {
      const launcherPython = String(process.env.EDMG_STUDIO_PYTHON ?? "").trim();
      if (launcherPython && launcherPython !== venvPython) {
        console.log("[backend] using project venv for dev backend instead of EDMG_STUDIO_PYTHON", {
          launcherPython,
          venvPython,
        });
      }
      return venvPython;
    }

    const launcherPython = String(process.env.EDMG_STUDIO_PYTHON ?? "").trim();
    if (launcherPython) return launcherPython;

    return venvPython;
  }

  function getPackagedBackendPath() {
    const exeName = isWindows ? "edmg-studio-backend.exe" : "edmg-studio-backend";
    return path.join(process.resourcesPath, "backend", exeName);
  }

  function resolveManagedFfmpegPath() {
    const explicit = String(process.env.EDMG_FFMPEG_PATH ?? "").trim();
    if (explicit) {
      if (!path.isAbsolute(explicit) || pathExistsSync(explicit)) {
        return explicit;
      }
      console.warn("[ffmpeg] explicit EDMG_FFMPEG_PATH missing, falling back:", explicit);
    }

    const exeName = isWindows ? "ffmpeg.exe" : "ffmpeg";
    const candidates = app.isPackaged
      ? [
          path.join(process.resourcesPath, "bin", exeName),
          path.join(process.resourcesPath, "electron-resources", "bin", exeName),
        ]
      : [
          path.join(rootDir, "electron-resources", "bin", exeName),
        ];

    for (const candidate of candidates) {
      if (pathExistsSync(candidate)) {
        return candidate;
      }
    }

    return explicit || "ffmpeg";
  }

  function getBackendLaunchSpec() {
    if (app.isPackaged) {
      const command = getPackagedBackendPath();
      return {
        command,
        args: ["serve", "--host", backendHost, "--port", String(backendPort)],
        cwd: path.dirname(command),
        label: "packaged-backend",
      };
    }

    return {
      command: getDevPythonPath(),
      args: ["-m", "edmg_studio_backend", "serve", "--host", backendHost, "--port", String(backendPort)],
      cwd: path.join(rootDir, "python_backend"),
      label: "python-backend",
    };
  }

  function buildBackendChildEnv(managedStudioEnv, ffmpegPath) {
    const env = {
      ...process.env,
      ...managedStudioEnv,
      ...buildManagedAiEnv(),
      EDMG_STUDIO_BACKEND_HOST: backendHost,
      EDMG_STUDIO_BACKEND_PORT: String(backendPort),
      EDMG_FFMPEG_PATH: ffmpegPath,
      MPLBACKEND: process.env.MPLBACKEND || "Agg",
    };

    for (const removedKey of ["EDMG_AI_OLLAMA_URL", "EDMG_AI_OLLAMA_MODEL", "OLLAMA_MODELS"]) {
      delete env[removedKey];
    }

    for (const key of [
      "ELECTRON_RUN_AS_NODE",
      "ELECTRON_NO_ATTACH_CONSOLE",
      "ELECTRON_NO_ASAR",
      "ELECTRON_ENABLE_LOGGING",
      "CHROME_CRASHPAD_PIPE_NAME",
      "NODE_OPTIONS",
      "VITE_DEV_SERVER_URL",
      "EDMG_STUDIO_TEST_MODE",
      "EDMG_STUDIO_TEST_PAGE",
      "EDMG_STUDIO_TEST_REPORT_PATH",
      "EDMG_STUDIO_TEST_PROBE_REVEAL_PATH",
      "EDMG_STUDIO_TEST_PROBE_OPEN_PATH",
      "EDMG_STUDIO_TEST_EXPECT_BACKEND_URL",
      "EDMG_STUDIO_TEST_FAKE_PATH_ACTIONS",
    ]) {
      delete env[key];
    }

    return env;
  }

  function resolveBackendLogPaths(logsDir) {
    const backendDir = path.join(logsDir || getStudioPaths().logsDir || app.getPath("logs"), "backend");
    ensureDirSync(backendDir);
    return {
      stdoutPath: path.join(backendDir, "backend-stdout.log"),
      stderrPath: path.join(backendDir, "backend-stderr.log"),
    };
  }

  function tailFileSync(filePath, maxLines = 40) {
    if (!filePath || !pathExistsSync(filePath)) return "";
    try {
      const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/).filter(Boolean);
      return lines.slice(-maxLines).join("\n");
    } catch {
      return "";
    }
  }

  function terminateProcessTree(pid) {
    if (!pid) return;
    if (isWindows) {
      const result = spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
        shell: false,
      });
      if (result.status === 0) return;
    }
    try {
      process.kill(pid);
    } catch {}
  }

  function quotePowerShell(value) {
    return `'${String(value ?? "").replace(/'/g, "''")}'`;
  }

  function launchPackagedBackendWindows(spec, env, logPaths) {
    const argList = (spec.args || []).map((arg) => quotePowerShell(String(arg))).join(", ");
    const stdoutPath = logPaths?.stdoutPath || "";
    const stderrPath = logPaths?.stderrPath || "";
    if (stdoutPath) ensureDirSync(path.dirname(stdoutPath));
    if (stderrPath) ensureDirSync(path.dirname(stderrPath));
    const script = [
      `$proc = Start-Process -FilePath ${quotePowerShell(spec.command)} -ArgumentList @(${argList}) -WorkingDirectory ${quotePowerShell(spec.cwd || path.dirname(spec.command))} -WindowStyle Hidden -RedirectStandardOutput ${quotePowerShell(stdoutPath)} -RedirectStandardError ${quotePowerShell(stderrPath)} -PassThru`,
      "[Console]::Out.Write($proc.Id)",
    ].filter(Boolean).join("; ");

    return new Promise((resolve, reject) => {
      const launcher = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
        shell: false,
        env,
      });
      let stdout = "";
      let stderr = "";

      launcher.stdout?.on("data", (chunk) => {
        stdout += String(chunk || "");
      });
      launcher.stderr?.on("data", (chunk) => {
        stderr += String(chunk || "");
      });
      launcher.on("error", (error) => {
        reject(error);
      });
      launcher.on("exit", (code) => {
        if (code !== 0) {
          reject(new Error(String(stderr || stdout || `PowerShell Start-Process failed with code ${code ?? "unknown"}`).trim()));
          return;
        }
        const pid = Number(String(stdout || "").trim());
        if (!Number.isFinite(pid) || pid <= 0) {
          reject(new Error(`Could not resolve packaged backend pid from Start-Process output: ${String(stdout || "").trim()}`));
          return;
        }
        resolve({
          pid,
          kind: "windows_start_process",
          logPaths,
          kill() {
            terminateProcessTree(pid);
          },
        });
      });
    });
  }

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function probeBackend(url = currentBackendUrl) {
    return new Promise((resolve) => {
      const req = http.get(`${url}/health`, (res) => {
        res.resume();
        resolve(res.statusCode != null && res.statusCode >= 200 && res.statusCode < 500);
      });

      req.on("error", () => resolve(false));
      req.setTimeout(1500, () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  async function waitForBackendReady(timeoutMs = backendReadyTimeoutMs) {
    const started = Date.now();

    while (Date.now() - started < timeoutMs) {
      if (await probeBackend()) {
        return true;
      }
      await delay(300);
    }

    return false;
  }

  async function startBackendIfNeeded() {
    logBackendUrlMarker();
    if ((process.env.EDMG_STUDIO_SPAWN_BACKEND ?? "1") === "0") {
      console.log("[edmg] spawn backend=false");
      return false;
    }

    if (await probeBackend()) {
      console.log("[backend] already reachable:", currentBackendUrl);
      logBackendUrlMarker();
      return true;
    }

    const spec = getBackendLaunchSpec();
    const managedStudioEnv = buildManagedStudioEnv();
    const backendDataDir = managedStudioEnv.EDMG_STUDIO_DATA_DIR;
    const ffmpegPath = resolveManagedFfmpegPath();
    const childEnv = buildBackendChildEnv(managedStudioEnv, ffmpegPath);
    const logPaths = resolveBackendLogPaths(managedStudioEnv.EDMG_STUDIO_LOGS_DIR);

    if (app.isPackaged && !fs.existsSync(spec.command)) {
      console.error("[backend] packaged backend missing:", spec.command);

      if (!testMode) {
        dialog.showErrorBox(
          "EDMG Studio backend missing",
          `Could not find packaged backend:\n${spec.command}`
        );
      }

      return false;
    }

    console.log("[edmg] spawn backend=true");
    console.log("[backend] launching", {
      label: spec.label,
      command: spec.command,
      args: spec.args,
      cwd: spec.cwd,
    });
    console.log("[backend] EDMG_STUDIO_DATA_DIR=", backendDataDir);
    console.log("[backend] EDMG_STUDIO_MODELS_DIR=", managedStudioEnv.EDMG_STUDIO_MODELS_DIR);
    console.log("[backend] EDMG_STUDIO_EXTERNAL_DIR=", managedStudioEnv.EDMG_STUDIO_EXTERNAL_DIR);
    console.log("[backend] EDMG_FFMPEG_PATH=", ffmpegPath);
    console.log("[backend] log files=", logPaths);

    try {
      if (app.isPackaged && isWindows) {
        backendProc = await launchPackagedBackendWindows(spec, childEnv, logPaths);
        console.log("[backend] launched via Start-Process", { pid: backendProc.pid });
      } else {
        backendProc = spawn(spec.command, spec.args, {
          cwd: spec.cwd,
          windowsHide: true,
          stdio: ["ignore", "pipe", "pipe"],
          env: childEnv,
        });
      }
    } catch (error) {
      console.error("[backend] spawn threw:", error);

      if (!testMode) {
        dialog.showErrorBox(
          "EDMG Studio backend failed to start",
          String(error?.message ?? error)
        );
      }

      return false;
    }

    if (typeof backendProc?.stdout?.on === "function") {
      backendProc.stdout.on("data", (chunk) => {
        safeStreamWrite(process.stdout, `[backend] ${chunk}`);
      });
    }

    if (typeof backendProc?.stderr?.on === "function") {
      backendProc.stderr.on("data", (chunk) => {
        safeStreamWrite(process.stderr, `[backend] ${chunk}`);
      });
    }

    if (typeof backendProc?.on === "function") {
      backendProc.on("error", (error) => {
        console.error("[backend] child process error:", error);

        if (!testMode) {
          dialog.showErrorBox(
            "EDMG Studio backend failed to start",
            `${error?.message ?? error}`
          );
        }
      });

      backendProc.on("exit", (code, signal) => {
        console.log("[backend] exited", { code, signal });
        backendProc = null;
      });
    }

    const ready = await waitForBackendReady();
    if (!ready) {
      console.warn("[backend] not reachable:", currentBackendUrl);
      const stdoutTail = tailFileSync(logPaths.stdoutPath);
      const stderrTail = tailFileSync(logPaths.stderrPath);
      if (stdoutTail) console.warn("[backend] stdout tail:\n" + stdoutTail);
      if (stderrTail) console.warn("[backend] stderr tail:\n" + stderrTail);
    } else {
      logBackendUrlMarker();
    }

    return ready;
  }

  function stopBackend() {
    if (!backendProc) return;

    try {
      if (typeof backendProc.kill === "function") {
        backendProc.kill();
      } else if (backendProc.pid) {
        terminateProcessTree(backendProc.pid);
      }
    } catch (error) {
      console.warn("[backend] failed to stop cleanly:", error);
    }

    backendProc = null;
  }

  return {
    getCurrentBackendUrl,
    startBackendIfNeeded,
    stopBackend,
  };
}
