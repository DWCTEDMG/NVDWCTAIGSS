import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(__dirname, '..');
const builderConfigPath = path.join(root, 'electron-builder.yml');
const retryableFsErrorCodes = new Set(['EACCES', 'EBUSY', 'EMFILE', 'ENFILE', 'EPERM']);

function normalizeToPosix(p) {
  return p.split(path.sep).join('/');
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function defaultStageDir() {
  return path.join(root, 'release', 'staged-app');
}

export function loadPackageJson() {
  const pkgPath = path.join(root, 'package.json');
  return JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
}

export function sanitizePackageJsonForStage(pkg) {
  const current = pkg && typeof pkg === 'object' ? pkg : {};
  const {
    build,
    scripts,
    devDependencies,
    private: _private,
    packageManager,
    ...rest
  } = current;

  return {
    name: rest.name,
    version: rest.version,
    description: rest.description || 'EDMG NVIDIA Workshop desktop app',
    author: rest.author || 'Dwct',
    type: rest.type,
    main: rest.main || 'main.mjs',
    dependencies: rest.dependencies || {},
  };
}

export function assertDesktopArtifacts() {
  const distIndex = path.join(root, 'dist-web', 'index.html');
  const distAssets = path.join(root, 'dist-web', 'assets');
  const mainPath = path.join(root, 'main.mjs');
  const preloadPath = path.join(root, 'preload.cjs');
  const pkgPath = path.join(root, 'package.json');
  if (!fs.existsSync(distIndex)) throw new Error('dist-web/index.html must exist. Run pnpm run build first.');
  if (!fs.existsSync(distAssets)) throw new Error('dist-web/assets must exist. Run pnpm run build first.');
  if (!fs.existsSync(mainPath)) throw new Error('main.mjs must exist');
  if (!fs.existsSync(preloadPath)) throw new Error('preload.cjs must exist');
  if (!fs.existsSync(pkgPath)) throw new Error('package.json must exist');
  const assets = fs.readdirSync(distAssets);
  if (!assets.length) throw new Error('dist-web/assets must contain built assets');
  const pkg = loadPackageJson();
  if (pkg.main !== 'main.mjs') throw new Error('package.json main must point to main.mjs');
}

function isRetryableFsError(error) {
  if (!error || typeof error !== 'object') return false;
  const code = typeof error.code === 'string' ? error.code : '';
  return retryableFsErrorCodes.has(code);
}

async function withFsRetries(action, fn, { attempts = 8, delayMs = 80 } = {}) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (attempt >= attempts || !isRetryableFsError(error)) {
        throw error;
      }
      const waitMs = delayMs * attempt;
      await delay(waitMs);
    }
  }
  throw lastError ?? new Error(`${action} failed`);
}

function buildElectronBuilderConfig() {
  const pkg = loadPackageJson();
  if (!pkg.build || typeof pkg.build !== 'object') {
    throw new Error('package.json must contain a build object for electron-builder');
  }
  return pkg.build;
}

export function renderElectronBuilderConfig() {
  return [
    '# Auto-generated from package.json build. Do not edit by hand.',
    JSON.stringify(buildElectronBuilderConfig(), null, 2),
    '',
  ].join('\n');
}

export async function syncElectronBuilderConfig() {
  const nextContent = renderElectronBuilderConfig();
  const currentContent = fs.existsSync(builderConfigPath)
    ? fs.readFileSync(builderConfigPath, 'utf8')
    : '';
  if (currentContent === nextContent) {
    return { path: builderConfigPath, changed: false };
  }
  await withFsRetries('write electron-builder config', async () => {
    await fsp.writeFile(builderConfigPath, nextContent, 'utf8');
  });
  return { path: builderConfigPath, changed: true };
}

async function rmrf(target) {
  await withFsRetries(`remove ${target}`, async () => {
    await fsp.rm(target, { recursive: true, force: true });
  });
}

async function copyFileWithRetry(from, to) {
  await fsp.mkdir(path.dirname(to), { recursive: true });
  await withFsRetries(`copy ${from} -> ${to}`, async () => {
    await fsp.copyFile(from, to);
  });
}

async function copyDir(src, dst) {
  await fsp.mkdir(dst, { recursive: true });
  for (const entry of await fsp.readdir(src, { withFileTypes: true })) {
    const from = path.join(src, entry.name);
    const to = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      await copyDir(from, to);
    } else if (entry.isSymbolicLink()) {
      const target = await fsp.readlink(from);
      await fsp.symlink(target, to);
    } else {
      await copyFileWithRetry(from, to);
    }
  }
}

async function copyPattern(relativePattern, stageDir, copied) {
  const normalized = normalizeToPosix(relativePattern);
  if (normalized.endsWith('/**')) {
    const base = normalized.slice(0, -3);
    const src = path.join(root, base);
    const dst = path.join(stageDir, base);
    if (!fs.existsSync(src)) throw new Error(`Missing required directory for staging: ${src}`);
    await copyDir(src, dst);
    copied.push(base + '/**');
    return;
  }
  const src = path.join(root, normalized);
  const dst = path.join(stageDir, normalized);
  if (!fs.existsSync(src)) throw new Error(`Missing required file for staging: ${src}`);
  await copyFileWithRetry(src, dst);
  copied.push(normalized);
}

export async function stageDesktopRelease({ outDir = defaultStageDir(), clean = true } = {}) {
  assertDesktopArtifacts();
  await syncElectronBuilderConfig();
  const pkg = loadPackageJson();
  const copied = [];
  if (clean) await rmrf(outDir);
  await fsp.mkdir(outDir, { recursive: true });

  const buildFiles = Array.isArray(pkg.build?.files) ? pkg.build.files : [];
  for (const pattern of buildFiles) {
    await copyPattern(pattern, outDir, copied);
  }

  const extraResources = Array.isArray(pkg.build?.extraResources) ? pkg.build.extraResources : [];
  for (const entry of extraResources) {
    if (!entry || typeof entry !== 'object' || !entry.from) continue;
    const src = path.join(root, entry.from);
    const dst = path.join(outDir, entry.from);
    if (!fs.existsSync(src)) continue;
    await copyDir(src, dst);
    copied.push(normalizeToPosix(entry.from) + '/**');
  }

  if (fs.existsSync(builderConfigPath)) {
    await copyFileWithRetry(builderConfigPath, path.join(outDir, 'electron-builder.yml'));
    copied.push('electron-builder.yml');
  }

  const stagedPackageJson = sanitizePackageJsonForStage(pkg);
  await withFsRetries('write staged package.json', async () => {
    await fsp.writeFile(path.join(outDir, 'package.json'), JSON.stringify(stagedPackageJson, null, 2) + '\n', 'utf8');
  });

  const manifest = {
    ok: true,
    stageDir: outDir,
    createdAt: new Date().toISOString(),
    main: pkg.main,
    copied,
    extraResources: extraResources.map((entry) => entry?.from).filter(Boolean),
    sanitizedPackageJson: true,
  };
  await fsp.mkdir(path.join(outDir, '.edmg-stage'), { recursive: true });
  await fsp.writeFile(path.join(outDir, '.edmg-stage', 'manifest.json'), JSON.stringify(manifest, null, 2));
  return manifest;
}
