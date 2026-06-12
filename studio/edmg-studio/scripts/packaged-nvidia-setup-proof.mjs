import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function log(message) {
  console.log(`[packaged-nvidia-setup] ${message}`);
}

function packagedAppCandidate() {
  return path.join(
    root,
    "dist",
    "win-unpacked",
    process.platform === "win32" ? "EDMG NVIDIA Workshop.exe" : "EDMG NVIDIA Workshop",
  );
}

function appDataRoot() {
  if (process.env.APPDATA) return process.env.APPDATA;
  return path.join(os.homedir(), "AppData", "Roaming");
}

function main() {
  const candidate = packagedAppCandidate();
  const bootstrapDir = path.join(appDataRoot(), "EDMG NVIDIA Workshop");
  const packageJson = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));

  const summary = {
    packageName: packageJson.name,
    productName: packageJson.build?.productName,
    appId: packageJson.build?.appId,
    candidate,
    bootstrapDir,
    candidateExists: fs.existsSync(candidate),
    bootstrapUsesWorkshopDir: bootstrapDir.endsWith(`${path.sep}EDMG NVIDIA Workshop`),
  };

  assert.equal(summary.packageName, "edmg-nvidia-workshop");
  assert.equal(summary.productName, "EDMG NVIDIA Workshop");
  assert.equal(summary.appId, "com.dwct.edmgnvidiaworkshop");
  assert.equal(summary.bootstrapUsesWorkshopDir, true);

  log(JSON.stringify(summary, null, 2));
}

try {
  main();
} catch (error) {
  console.error("[packaged-nvidia-setup] FAILED", error);
  process.exit(1);
}
