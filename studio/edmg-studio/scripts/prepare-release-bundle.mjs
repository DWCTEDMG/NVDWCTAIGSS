import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const pythonBackendDir = path.join(root, "python_backend");
const electronBackendDir = path.join(root, "electron-resources", "backend");
const directorAppDir = path.resolve(root, "..", "..", "chatgpt-apps", "edmg-director");
const electronDirectorDir = path.join(root, "electron-resources", "director");
const directorBundleManifestPath = path.join(electronDirectorDir, "director-bundle-manifest.json");
const backendBinaryName = process.platform === "win32" ? "edmg-studio-backend.exe" : "edmg-studio-backend";
const bundledBackendPath = path.join(electronBackendDir, backendBinaryName);
const bundleManifestPath = path.join(electronBackendDir, "backend-bundle-manifest.json");
const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const SUPPORTED_PYTHON_MIN = [3, 10];
const SUPPORTED_PYTHON_MAX_EXCLUSIVE = [3, 14];
// nvidia-riva-client 2.19.0 pins setuptools exactly.
const backendSetuptoolsConstraint = "setuptools==70.0.0";

const ignoredDirNames = new Set([
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  "__pycache__",
  "build",
  "dist",
  "venv",
]);

const trackedRootFiles = new Set([
  "backend_entry.py",
  "pyinstaller.spec",
  "pyproject.toml",
  "README.md",
]);

function runChecked(label, command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    stdio: "inherit",
    shell: false,
  });
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${result.status ?? "unknown"}`);
  }
}

function canRun(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    stdio: "ignore",
    shell: false,
  });
  return result.status === 0;
}

function runPnpmChecked(label, args, options = {}) {
  const execPath = String(process.env.npm_execpath || "").trim();
  if (execPath && fs.existsSync(execPath)) {
    if (/\.(?:c?js|mjs)$/i.test(execPath)) {
      runChecked(label, process.execPath, [execPath, ...args], options);
      return;
    }
    runChecked(label, execPath, args, options);
    return;
  }
  runChecked(label, pnpmCommand, args, options);
}

function compareVersionTriples(left, right) {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const delta = (left[index] ?? 0) - (right[index] ?? 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

function parsePythonVersion(command, args, options = {}) {
  const result = spawnSync(command, [...args, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], {
    cwd: options.cwd ?? root,
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
    encoding: "utf8",
  });
  if (result.status !== 0) return null;
  const raw = String(result.stdout || "").trim();
  const match = raw.match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!match) return null;
  return {
    raw,
    tuple: [Number(match[1]), Number(match[2]), Number(match[3])],
  };
}

function isSupportedPythonVersion(versionTuple) {
  return compareVersionTriples(versionTuple, SUPPORTED_PYTHON_MIN) >= 0 &&
    compareVersionTriples(versionTuple, SUPPORTED_PYTHON_MAX_EXCLUSIVE) < 0;
}

function assertSupportedPython(command, args, label, options = {}) {
  const version = parsePythonVersion(command, args, options);
  if (!version) {
    throw new Error(`Could not determine ${label} version for ${command}`);
  }
  if (!isSupportedPythonVersion(version.tuple)) {
    throw new Error(
      `${label} ${version.raw} is unsupported for Studio release builds. Use Python >= ${SUPPORTED_PYTHON_MIN.join(".")} and < ${SUPPORTED_PYTHON_MAX_EXCLUSIVE.join(".")}.`,
    );
  }
  return version.raw;
}

function resolvePythonBootstrapCommand() {
  const envPython = process.env.EDMG_STUDIO_PYTHON;
  const candidates = [
    envPython ? { command: envPython, prefix: [] } : null,
    { command: "python", prefix: [] },
    process.platform === "win32" ? { command: "py", prefix: ["-3"] } : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const version = parsePythonVersion(candidate.command, candidate.prefix, { cwd: root });
    if (version && isSupportedPythonVersion(version.tuple)) {
      return candidate;
    }
  }

  throw new Error(
    `Could not find a supported Python command. Set EDMG_STUDIO_PYTHON or install Python >= ${SUPPORTED_PYTHON_MIN.join(".")} and < ${SUPPORTED_PYTHON_MAX_EXCLUSIVE.join(".")}.`,
  );
}

function venvPythonPath() {
  if (process.platform === "win32") {
    return path.join(pythonBackendDir, "venv", "Scripts", "python.exe");
  }
  return path.join(pythonBackendDir, "venv", "bin", "python");
}

function collectBackendFiles(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ignoredDirNames.has(entry.name)) continue;
    if (entry.name.endsWith(".egg-info")) continue;

    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collectBackendFiles(fullPath, acc);
      continue;
    }
    if (!entry.isFile()) continue;
    acc.push(fullPath);
  }
  return acc;
}

async function computeBackendSourceFingerprint() {
  const files = [];
  for (const name of trackedRootFiles) {
    const fullPath = path.join(pythonBackendDir, name);
    if (fs.existsSync(fullPath)) files.push(fullPath);
  }

  const packageDirs = collectBackendFiles(pythonBackendDir, []);
  for (const file of packageDirs) {
    if (!files.includes(file)) files.push(file);
  }

  files.sort((a, b) => a.localeCompare(b));
  const hash = crypto.createHash("sha256");
  let newestSourceMtimeMs = 0;

  for (const file of files) {
    const rel = path.relative(pythonBackendDir, file).split(path.sep).join("/");
    const stat = await fsp.stat(file);
    newestSourceMtimeMs = Math.max(newestSourceMtimeMs, stat.mtimeMs);
    hash.update(rel);
    hash.update("\n");
    hash.update(await fsp.readFile(file));
    hash.update("\n");
  }

  return {
    sourceHash: hash.digest("hex"),
    fileCount: files.length,
    newestSourceMtimeMs,
  };
}

async function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

function readBundleManifest() {
  if (!fs.existsSync(bundleManifestPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(bundleManifestPath, "utf8"));
  } catch {
    return null;
  }
}

function currentBundleMatches(sourceHash) {
  const manifest = readBundleManifest();
  return Boolean(manifest && manifest.sourceHash === sourceHash && fs.existsSync(bundledBackendPath));
}

function distBackendCandidates() {
  return [
    path.join(pythonBackendDir, "dist", "edmg-studio-backend", backendBinaryName),
    path.join(pythonBackendDir, "dist", backendBinaryName),
  ];
}

function findFreshDistBackend(newestSourceMtimeMs) {
  for (const candidate of distBackendCandidates()) {
    if (!fs.existsSync(candidate)) continue;
    const stat = fs.statSync(candidate);
    if (stat.mtimeMs >= newestSourceMtimeMs) {
      return candidate;
    }
  }
  return "";
}

function ensureBackendBuild() {
  const bootstrap = resolvePythonBootstrapCommand();
  assertSupportedPython(bootstrap.command, bootstrap.prefix, "Bootstrap Python", { cwd: root });
  const venvPython = venvPythonPath();

  if (fs.existsSync(venvPython)) {
    const venvVersion = parsePythonVersion(venvPython, [], { cwd: pythonBackendDir });
    if (!venvVersion || !isSupportedPythonVersion(venvVersion.tuple)) {
      fs.rmSync(path.join(pythonBackendDir, "venv"), { recursive: true, force: true });
    }
  }

  if (fs.existsSync(venvPython) && !canRun(venvPython, ["--version"], { cwd: pythonBackendDir })) {
    fs.rmSync(path.join(pythonBackendDir, "venv"), { recursive: true, force: true });
  }

  if (!fs.existsSync(venvPython)) {
    runChecked("create backend virtualenv", bootstrap.command, [...bootstrap.prefix, "-m", "venv", "venv"], {
      cwd: pythonBackendDir,
    });
  }

  assertSupportedPython(venvPython, [], "Backend venv Python", { cwd: pythonBackendDir });

  runChecked("upgrade backend packaging tools", venvPython, ["-m", "pip", "install", "-U", "pip", "wheel", backendSetuptoolsConstraint], {
    cwd: pythonBackendDir,
  });
  const backendBundleExtra = process.platform === "win32" ? "studio_bundle_directml" : "studio_bundle";
  runChecked("install backend studio bundle", venvPython, ["-m", "pip", "install", "-e", `.[${backendBundleExtra}]`], {
    cwd: pythonBackendDir,
  });
  runChecked("install pyinstaller", venvPython, ["-m", "pip", "install", "pyinstaller"], {
    cwd: pythonBackendDir,
  });
  runChecked("build backend bundle", venvPython, ["-m", "PyInstaller", ".\\pyinstaller.spec", "--clean", "--noconfirm"], {
    cwd: pythonBackendDir,
  });

  const built = distBackendCandidates().find((candidate) => fs.existsSync(candidate));
  if (!built) {
    throw new Error(`Backend build completed but ${backendBinaryName} was not found under python_backend/dist`);
  }
  return built;
}

async function stageBackendBundle(sourcePath, fingerprint, reusedExistingBuild) {
  await fsp.mkdir(electronBackendDir, { recursive: true });
  await fsp.copyFile(sourcePath, bundledBackendPath);

  const manifest = {
    ok: true,
    builder: "scripts/prepare-release-bundle.mjs",
    sourceHash: fingerprint.sourceHash,
    sourceFileCount: fingerprint.fileCount,
    newestSourceMtimeMs: fingerprint.newestSourceMtimeMs,
    bundledBackend: path.relative(root, bundledBackendPath).split(path.sep).join("/"),
    sourceArtifact: path.relative(root, sourcePath).split(path.sep).join("/"),
    binarySha256: await sha256File(bundledBackendPath),
    reusedExistingBuild,
    preparedAt: new Date().toISOString(),
  };
  await fsp.writeFile(bundleManifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  return manifest;
}

async function stageDirectorBundle() {
  if (!fs.existsSync(directorAppDir)) {
    throw new Error(`Director app directory is missing: ${directorAppDir}`);
  }

  runPnpmChecked("build director bundle", ["run", "build"], {
    cwd: directorAppDir,
  });

  const requiredEntries = [
    path.join(directorAppDir, "dist-server", "server.js"),
    path.join(directorAppDir, "assets"),
    path.join(directorAppDir, "node_modules"),
    path.join(directorAppDir, "package.json"),
  ];
  for (const entry of requiredEntries) {
    if (!fs.existsSync(entry)) {
      throw new Error(`Director bundle build is missing required artifact: ${entry}`);
    }
  }

  await fsp.rm(electronDirectorDir, { recursive: true, force: true });
  await fsp.mkdir(electronDirectorDir, { recursive: true });

  const copyEntries = ["assets", "dist-server", "node_modules", "package.json", "README.md"];
  for (const name of copyEntries) {
    const source = path.join(directorAppDir, name);
    if (!fs.existsSync(source)) continue;
    const target = path.join(electronDirectorDir, name);
    await fsp.cp(source, target, {
      recursive: true,
      force: true,
      dereference: false,
    });
  }

  const manifest = {
    ok: true,
    builder: "scripts/prepare-release-bundle.mjs",
    directorAppDir: path.relative(root, directorAppDir).split(path.sep).join("/"),
    bundledDirectorDir: path.relative(root, electronDirectorDir).split(path.sep).join("/"),
    included: copyEntries.filter((name) => fs.existsSync(path.join(electronDirectorDir, name))),
    preparedAt: new Date().toISOString(),
  };
  await fsp.writeFile(directorBundleManifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  return manifest;
}

async function main() {
  runChecked("prepare electron build assets", process.execPath, [path.join(__dirname, "prepare-electron-build.mjs")], {
    cwd: root,
  });

  const fingerprint = await computeBackendSourceFingerprint();
  if (currentBundleMatches(fingerprint.sourceHash)) {
    const directorManifest = await stageDirectorBundle();
    console.log(
      JSON.stringify(
        {
          ok: true,
          skippedRebuild: true,
          reason: "bundled backend already matches current backend sources",
          bundleManifestPath,
          directorBundleManifestPath,
          directorManifest,
          sourceHash: fingerprint.sourceHash,
        },
        null,
        2,
      ),
    );
    return;
  }

  let sourceArtifact = findFreshDistBackend(fingerprint.newestSourceMtimeMs);
  let reusedExistingBuild = Boolean(sourceArtifact);
  if (!sourceArtifact) {
    sourceArtifact = ensureBackendBuild();
    reusedExistingBuild = false;
  }

  const manifest = await stageBackendBundle(sourceArtifact, fingerprint, reusedExistingBuild);
  const directorManifest = await stageDirectorBundle();
  console.log(JSON.stringify({ ok: true, bundleManifestPath, manifest, directorBundleManifestPath, directorManifest }, null, 2));
}

main().catch((error) => {
  console.error("[prepare-release-bundle] FAILED", error);
  process.exit(1);
});
