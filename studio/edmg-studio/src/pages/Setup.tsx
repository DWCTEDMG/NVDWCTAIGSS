import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import { getDesktopPlatformKind, type DesktopPlatformKind } from "../components/desktopArtifacts";
import NvidiaReadinessCard from "../components/NvidiaReadinessCard";

type StorageDraft = {
  studioHome: string;
  dataDir: string;
  modelsDir: string;
  cacheRoot: string;
  logsDir: string;
  externalDir: string;
};

const EMPTY_STORAGE_DRAFT: StorageDraft = {
  studioHome: "",
  dataDir: "",
  modelsDir: "",
  cacheRoot: "",
  logsDir: "",
  externalDir: "",
};

type StorageFieldConfig = {
  key: keyof StorageDraft;
  label: string;
  placeholder: string;
};

function normalizePlatformKind(rawPlatform: unknown): DesktopPlatformKind {
  const value = String(rawPlatform ?? "").trim().toLowerCase();
  if (value.includes("win")) return "windows";
  if (value.includes("darwin") || value.includes("mac")) return "mac";
  if (value.includes("linux")) return "linux";
  return getDesktopPlatformKind();
}

function joinStoragePath(basePath: string, segment: string, platformKind: DesktopPlatformKind): string {
  const base = String(basePath || "").trim();
  if (!base) return "";
  const separator = platformKind === "windows" ? "\\" : "/";
  const normalizedBase = base.replace(/[\\/]+$/, "");
  const normalizedSegment = String(segment || "").trim().replace(/^[\\/]+|[\\/]+$/g, "");
  return normalizedSegment ? `${normalizedBase}${separator}${normalizedSegment}` : normalizedBase;
}

function getStorageFieldConfigs(platformKind: DesktopPlatformKind): StorageFieldConfig[] {
  const homePlaceholder = platformKind === "windows"
    ? "D:\\EDMG-NVIDIA-Workshop"
    : platformKind === "mac"
      ? "/Users/tyler/EDMG-NVIDIA-Workshop"
      : "/home/tyler/EDMG-NVIDIA-Workshop";
  return [
    { key: "studioHome", label: "Workshop home", placeholder: homePlaceholder },
    { key: "dataDir", label: "Project data", placeholder: joinStoragePath(homePlaceholder, "data", platformKind) },
    { key: "modelsDir", label: "Models", placeholder: joinStoragePath(homePlaceholder, "models", platformKind) },
    { key: "cacheRoot", label: "Shared cache", placeholder: joinStoragePath(homePlaceholder, "cache", platformKind) },
    { key: "logsDir", label: "Logs", placeholder: joinStoragePath(homePlaceholder, "logs", platformKind) },
    { key: "externalDir", label: "External tools", placeholder: joinStoragePath(homePlaceholder, "external", platformKind) },
  ];
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 700,
        background: ok ? "#163a1f" : "#3a1616",
        color: ok ? "#b7ffcb" : "#ffb7b7",
        border: ok ? "1px solid #245b32" : "1px solid #5b2424"
      }}
    >
      {label}
    </span>
  );
}


export default function Setup({ onNavigate }: { onNavigate?: (p: any) => void }) {
  const [status, setStatus] = useState<any>(null);
  const [modelsCatalog, setModelsCatalog] = useState<any>(null);
  const [edmgVerifyResult, setEdmgVerifyResult] = useState<any>(null);
  const [nvidiaDiagnostics, setNvidiaDiagnostics] = useState<any>(null);
  const [nvidiaBusy, setNvidiaBusy] = useState(false);
  const [studioPaths, setStudioPaths] = useState<any>(null);
  const [storageDraft, setStorageDraft] = useState<StorageDraft>(EMPTY_STORAGE_DRAFT);
  const [packAccept, setPackAccept] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState<string>("");

  async function refresh() {
    setErr("");
    try {
      const s = await apiGet("/v1/setup/status");
      setStatus(s);
      const mc = await apiGet("/v1/models/catalog");
      setModelsCatalog(mc);
      if (window.edmg?.getStudioPaths) {
        const paths = await window.edmg.getStudioPaths();
        if (paths?.ok) {
          setStudioPaths(paths);
          setStorageDraft((current) => ({
            studioHome: current.studioHome || String(paths.studioHome ?? ""),
            dataDir: current.dataDir || String(paths.dataDir ?? ""),
            modelsDir: current.modelsDir || String(paths.modelsDir ?? ""),
            cacheRoot: current.cacheRoot || String(paths.cacheRoot ?? ""),
            logsDir: current.logsDir || String(paths.logsDir ?? ""),
            externalDir: current.externalDir || String(paths.externalDir ?? ""),
          }));
        }
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function refreshNvidiaDiagnostics(manual = false) {
    if (manual) setNvidiaBusy(true);
    try {
      const diagnostics = await apiGet("/v1/nvidia/diagnostics");
      setNvidiaDiagnostics(diagnostics);
    } catch {
      setNvidiaDiagnostics(null);
    } finally {
      if (manual) setNvidiaBusy(false);
    }
  }

  useEffect(() => {
    let mounted = true;
    let pollTimer: number | null = null;

    const initializeSetup = async () => {
      try {
        await refresh();
        await refreshNvidiaDiagnostics();
        if (mounted) {
          pollTimer = window.setInterval(() => {
            if (mounted) refresh();
          }, 2000);
        }
      } catch (e: any) {
        if (mounted) setErr(String(e?.message ?? e));
      }
    };

    initializeSetup();

    return () => {
      mounted = false;
      if (pollTimer !== null) clearInterval(pollTimer);
    };
  }, []);

  async function run(action: string, path: string, body: any = {}) {
    setBusy(action);
    setErr("");
    try {
      await apiPost(path, body);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  async function cancelTask(taskId: string) {
    setBusy(`cancel_${taskId}`);
    setErr("");
    try {
      await apiPost(`/v1/setup/tasks/${taskId}/cancel`, {});
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  const comfyOk = !!status?.comfyui?.ok;
  const ffOk = !!status?.ffmpeg?.ok;
  const backendBundleOk = !!status?.backend_bundle?.ok;
  const edmgOk = !!status?.edmg?.available;
  const sevenOk = !!status?.sevenzip?.ok;
  const aiConfig = status?.ai_config ?? null;
  const nvidiaProfile = status?.nvidia_profile ?? aiConfig?.nvidia_profile ?? null;
  const aiLabel = String(aiConfig?.label ?? "NVIDIA NIM / OpenAI-compatible provider");
  const nvidiaEnabled = !!nvidiaProfile?.enabled || !!status?.nvidia_mode;
  const nvidiaPlannerOk = !!status?.nvidia_planner?.ok || aiConfig?.provider === "openai_compat";
  const platformKind = normalizePlatformKind(studioPaths?.platform);
  const isWindows = platformKind === "windows";
  const comfyDocsUrl = isWindows
    ? "https://docs.comfy.org/installation/comfyui_portable_windows"
    : "https://docs.comfy.org/";
  const storageFieldConfigs = getStorageFieldConfigs(platformKind);
  const setupReady = backendBundleOk && ffOk && nvidiaEnabled && nvidiaPlannerOk;
  const fullSetupReady = isWindows && setupReady && sevenOk;

  function deriveStorageLayout(studioHome: string): StorageDraft {
    const home = String(studioHome || "").trim();
    if (!home) {
      return {
        ...EMPTY_STORAGE_DRAFT,
        studioHome: "",
      };
    }
    return {
      studioHome: home,
      dataDir: joinStoragePath(home, "data", platformKind),
      modelsDir: joinStoragePath(home, "models", platformKind),
      cacheRoot: joinStoragePath(home, "cache", platformKind),
      logsDir: joinStoragePath(home, "logs", platformKind),
      externalDir: joinStoragePath(home, "external", platformKind),
    };
  }

  function updateStorageDraft(patch: Partial<StorageDraft>) {
    setStorageDraft((current) => {
      const next = { ...current, ...patch };
      if (patch.studioHome != null) {
        const previousDefaults = deriveStorageLayout(current.studioHome || studioPaths?.studioHome || "");
        const nextDefaults = deriveStorageLayout(next.studioHome || "");
        const overrides = studioPaths?.storageOverrides ?? {};
        (["dataDir", "modelsDir", "cacheRoot", "logsDir", "externalDir"] as Array<keyof StorageDraft>).forEach((key) => {
          if (patch[key] != null) return;
          const currentValue = String(current[key] || "");
          const wasDefault = !overrides?.[key] || currentValue === previousDefaults[key];
          if (wasDefault) {
            next[key] = nextDefaults[key];
          }
        });
      }
      return next;
    });
  }

  async function browseStoragePath(field: keyof StorageDraft, label: string) {
    try {
      const picked = await window.edmg?.pickDirectory?.({
        title: `Select ${label}`,
        defaultPath: storageDraft[field] || studioPaths?.[field] || undefined,
      });
      if (picked?.ok && picked.path) {
        updateStorageDraft({ [field]: String(picked.path) } as Partial<StorageDraft>);
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function applyStorageSettings() {
    const target = (storageDraft.studioHome || "").trim();
    if (!target) {
      setErr("Pick a Workshop Home folder first.");
      return;
    }
    if (!window.edmg?.setStorageSettings && !window.edmg?.setStudioHome) {
      setErr("Storage layout changes are only available in the desktop app build.");
      return;
    }

    setBusy("storage_settings");
    setErr("");
    try {
      const payload = {
        studioHome: target,
        dataDir: (storageDraft.dataDir || "").trim(),
        modelsDir: (storageDraft.modelsDir || "").trim(),
        cacheRoot: (storageDraft.cacheRoot || "").trim(),
        logsDir: (storageDraft.logsDir || "").trim(),
        externalDir: (storageDraft.externalDir || "").trim(),
      };
      const result = window.edmg?.setStorageSettings
        ? await window.edmg.setStorageSettings(payload)
        : await window.edmg?.setStudioHome?.(target);
      if (!result?.ok) {
        throw new Error(result?.error || "Failed to save storage settings.");
      }
      setStudioPaths((prev: any) => ({ ...(prev || {}), ...result, source: "configured (restart pending)" }));
      setStorageDraft({
        studioHome: String(result.studioHome || target),
        dataDir: String(result.dataDir || payload.dataDir || ""),
        modelsDir: String(result.modelsDir || payload.modelsDir || ""),
        cacheRoot: String(result.cacheRoot || payload.cacheRoot || ""),
        logsDir: String(result.logsDir || payload.logsDir || ""),
        externalDir: String(result.externalDir || payload.externalDir || ""),
      });
      const confirmMsg = result?.migrationPlanned
        ? `Storage layout saved.\n\n${result?.migrationSummary || "Existing Workshop data will be moved into the new locations on restart."}\n\nRestart EDMG NVIDIA Workshop now?`
        : "Storage layout saved. Restart EDMG NVIDIA Workshop now so new downloads, projects, models, caches, and logs start using those folders?";
      if (window.confirm(confirmMsg)) {
        await window.edmg?.relaunch?.();
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  const storageOverrides = studioPaths?.storageOverrides ?? {};
  const storageCustomLabels = [
    storageOverrides?.dataDir ? "project data" : null,
    storageOverrides?.modelsDir ? "models" : null,
    storageOverrides?.cacheRoot ? "cache" : null,
    storageOverrides?.logsDir ? "logs" : null,
    storageOverrides?.externalDir ? "external tools" : null,
  ].filter(Boolean);

  async function verifyEdmgCore() {
    setBusy("verify_edmg");
    setErr("");
    try {
      const result = await apiPost("/v1/edmg/verify", {});
      setEdmgVerifyResult(result);
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }



  async function installPack(packId: string) {
    if (!packAccept[packId]) {
      setErr("Please accept the license terms for this pack.");
      return;
    }
    setBusy(`pack_${packId}`);
    setErr("");
    try {
      // Accept licenses for all catalog-backed models in the pack before install.
      const pack = (modelsCatalog?.packs ?? []).find((p: any) => p.id === packId);
      const all = [...(modelsCatalog?.catalog ?? []), ...(modelsCatalog?.user ?? [])];
      const accepted = modelsCatalog?.accepted ?? {};
      for (const mid of (pack?.models ?? [])) {
        const m = all.find((x: any) => x.id === mid);
        if (!m) continue;
        if (accepted[mid]) continue;
        await apiPost("/v1/models/accept", { model_id: mid, license_id: m.license_id ?? "unknown" });
      }
      await apiPost("/v1/models/install_pack", { pack_id: packId });
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div>
      <h2>NVIDIA Runtime Setup</h2>
      <div className="small" style={{ marginTop: 6 }}>
        NVIDIA Kit is the Workshop shell. This page checks NIM planning, OpenUSD/RTX handoff readiness, and backend dependencies for the unified NVIDIA runtime.
      </div>

      {err && (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 10, background: "#2a1b1b", border: "1px solid #5b2424" }}>
          <div style={{ fontWeight: 800 }}>Setup error</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{err}</pre>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12, marginTop: 14 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>Storage Location</div>
            <Badge
              ok={!!studioPaths?.studioHome}
              label={storageCustomLabels.length ? `Custom (${storageCustomLabels.length})` : (studioPaths?.studioHome ? "Configured" : "Default")}
            />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Set this before installing runtimes or model packs if you want Workshop projects, model downloads, caches, logs, and external tools to stay under one root instead of filling the default system drive.
          </div>
          {storageCustomLabels.length ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>
              Custom roots active for <b>{storageCustomLabels.join(", ")}</b>.
            </div>
          ) : null}
          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            {storageFieldConfigs.map((field) => (
              <div key={field.key} style={{ display: "grid", gridTemplateColumns: "140px 1fr auto", gap: 8, alignItems: "center" }}>
                <div className="small" style={{ fontWeight: 800 }}>{field.label}</div>
                <input
                  value={String(storageDraft[field.key as keyof StorageDraft] ?? "")}
                  onChange={(e) => updateStorageDraft({ [field.key]: e.target.value } as Partial<StorageDraft>)}
                  placeholder={field.placeholder}
                />
                <button className="secondary" onClick={() => browseStoragePath(field.key as keyof StorageDraft, field.label)}>
                  Browse…
                </button>
              </div>
            ))}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                className="secondary"
                onClick={() => setStorageDraft(deriveStorageLayout(storageDraft.studioHome || studioPaths?.studioHome || ""))}
              >
                Use Workshop Home Defaults
              </button>
              <button disabled={busy === "storage_settings"} onClick={applyStorageSettings}>
                {busy === "storage_settings" ? "Saving…" : "Save & Restart"}
              </button>
              <button
                className="secondary"
                disabled={!studioPaths?.studioHome}
                onClick={() => window.edmg?.openPath?.(studioPaths?.studioHome)}
              >
                Open Folder
              </button>
            </div>
            {studioPaths ? (
              <div className="small" style={{ display: "grid", gap: 4, opacity: 0.88 }}>
                <div>Workshop home: <code>{studioPaths.studioHome}</code></div>
                <div>Project data: <code>{studioPaths.dataDir}</code></div>
                <div>Models: <code>{studioPaths.modelsDir}</code></div>
                <div>Shared cache: <code>{studioPaths.cacheRoot}</code></div>
                <div>External tools: <code>{studioPaths.externalDir}</code></div>
                <div>Logs: <code>{studioPaths.logsDir}</code></div>
                <div>Legacy UI data: <code>{studioPaths.electronUserData}</code></div>
                {studioPaths?.pendingMigration?.source?.studioHome ? (
                  <div style={{ color: "#ffd78c" }}>
                    Pending migration: <code>{studioPaths.pendingMigration.source.studioHome}</code> to <code>{studioPaths.pendingMigration.target?.studioHome}</code>. Restart the Workshop to run it.
                  </div>
                ) : null}
                {studioPaths?.lastMigration ? (
                  <div style={{ color: studioPaths.lastMigration.ok ? "#b7ffcb" : "#ffd78c" }}>
                    Last migration: {studioPaths.lastMigration.ok ? "completed" : "completed with warnings"} at{" "}
                    <code>{studioPaths.lastMigration.completedAt || "unknown time"}</code>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

<div className="card">
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
    <div style={{ fontWeight: 800 }}>{isWindows ? "0) Full System Setup (One-Click)" : "0) System Setup"}</div>
    <Badge ok={isWindows ? fullSetupReady : setupReady} label={isWindows ? (fullSetupReady ? "Ready" : "Setup") : (setupReady ? "Ready" : "Manual")} />
  </div>
  <div className="small" style={{ marginTop: 6 }}>
    {isWindows
      ? "Runs the NVIDIA installer pipeline: backend runtime bundle -> NVIDIA profile checks -> 7-Zip (if needed) -> NVIDIA ComfyUI secondary workflow install."
      : `Linux and macOS use the manual NVIDIA path: run the backend, configure the NIM endpoint, and add ComfyUI only for secondary workflows. Planner path: ${aiLabel}.`}
  </div>
  <div data-testid="setup-active-ai-path" className="small" style={{ marginTop: 8, opacity: 0.9 }}>
    Active AI path: <b>{aiLabel}</b>
    {aiConfig?.base_url ? <> • endpoint <code>{aiConfig.base_url}</code></> : null}
    {aiConfig?.model ? <> • model <code>{aiConfig.model}</code></> : null}
    {aiConfig?.provider === "openai_compat" ? <> • API key <b>{aiConfig?.openai_compat_api_key_configured ? "saved" : "not saved"}</b></> : null}
    {nvidiaProfile ? <> • NVIDIA <b>{nvidiaProfile.enabled ? "enabled" : "disabled"}</b></> : null}
  </div>
  <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
    {isWindows ? (
      <>
        <button
          disabled={busy === "full_nvidia"}
          onClick={() => run("full_nvidia", "/v1/setup/full/install", { flavor: "nvidia" })}
        >
          {busy === "full_nvidia" ? "Running…" : "Prepare NVIDIA Runtime"}
        </button>
      </>
    ) : (
      <>
        <button className="secondary" onClick={() => window.edmg?.openExternal?.("https://docs.nvidia.com/nim/")}>
          Open NVIDIA NIM Docs
        </button>
        <button className="secondary" onClick={() => window.edmg?.openExternal?.("https://docs.omniverse.nvidia.com/")}>
          Open Omniverse Docs
        </button>
      </>
    )}
    {onNavigate ? (
      <button className="secondary" onClick={() => onNavigate("settings")}>
        Open NVIDIA Settings
      </button>
    ) : null}
  </div>
</div>

        <NvidiaReadinessCard
          diagnostics={nvidiaDiagnostics}
          status={{ nvidia: nvidiaProfile }}
          busy={nvidiaBusy}
          onRefresh={() => void refreshNvidiaDiagnostics(true)}
          onOpenSettings={onNavigate ? () => onNavigate("settings") : undefined}
        />

<div className="card">
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
    <div style={{ fontWeight: 800 }}>0.25) Backend Runtime Bundle</div>
    <Badge ok={backendBundleOk} label={backendBundleOk ? "OK" : "Missing"} />
  </div>
  <div className="small" style={{ marginTop: 6 }}>
    Installs the backend audio, ASR, and internal-render Python dependencies into the Workshop backend environment.
  </div>
  <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
    <button
      disabled={busy === "backend_bundle"}
      onClick={() => run("backend_bundle", "/v1/setup/backend/install", { bundle: "studio_bundle" })}
    >
      {busy === "backend_bundle" ? "Installing…" : "Install Backend Runtime"}
    </button>
  </div>
  {!backendBundleOk && status?.backend_bundle?.hint && (
    <div className="small" style={{ marginTop: 10 }}>
      Fix: {status.backend_bundle.hint}
    </div>
  )}
  {!!status?.backend_bundle?.missing?.length && (
    <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
      Missing modules: <code>{status.backend_bundle.missing.join(", ")}</code>
    </div>
  )}
</div>

<div className="card">
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
    <div style={{ fontWeight: 800 }}>0.5) 7-Zip (Extractor)</div>
    <Badge ok={sevenOk} label={isWindows ? (sevenOk ? "OK" : "Missing") : "Optional"} />
  </div>
  <div className="small" style={{ marginTop: 6 }}>
    {isWindows
      ? "Required to extract some .7z archives (BCJ2), including some ComfyUI Portable releases. Workshop now keeps the portable CLI under your External tools root."
      : "Only needed for the Windows ComfyUI Portable workflow. Linux and macOS users can skip this and install ComfyUI manually."}
  </div>
  {isWindows ? (
    <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button
        disabled={busy === "sevenzip"}
        onClick={() => run("sevenzip", "/v1/setup/7zip/install", {})}
      >
        {busy === "sevenzip" ? "Downloading…" : "Download Portable 7-Zip"}
      </button>
    </div>
  ) : null}
  {isWindows && !sevenOk && status?.sevenzip?.hint && (
    <div className="small" style={{ marginTop: 10 }}>
      Fix: {status.sevenzip.hint}
    </div>
  )}
</div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>1) NVIDIA Planner</div>
            <Badge ok={nvidiaPlannerOk} label={nvidiaPlannerOk ? "Ready" : "Check"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Planner path: <b>{aiLabel}</b>
            {aiConfig?.base_url ? <> • endpoint <code>{aiConfig.base_url}</code></> : null}
            {aiConfig?.model ? <> • model <code>{aiConfig.model}</code></> : null}
          </div>
          <div className="small" style={{ marginTop: 6, opacity: 0.9 }}>
            Planning must resolve through NVIDIA NIM or a NVIDIA-compatible OpenAI endpoint configured in Settings.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="secondary" onClick={refresh}>
              Refresh
            </button>
            <button className="secondary" onClick={() => window.edmg?.openExternal?.("https://docs.nvidia.com/nim/")}>
              Open NVIDIA NIM Docs
            </button>
          </div>
          {status?.tasks?.length ? (
            <div style={{ marginTop: 10 }}>
              <div className="small" style={{ fontWeight: 800 }}>Installer tasks</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                {status.tasks.slice(0, 5).map((t: any) => (
                  <div key={t.id} style={{ padding: 8, borderRadius: 10, background: "#121422", border: "1px solid #22263a" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                      <div style={{ fontWeight: 700 }}>{t.name}</div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
                        <div className="small">{t.status}{t.progress != null ? ` • ${Math.round(t.progress * 100)}%` : ""}</div>
                        {(t.status === "queued" || t.status === "running") ? (
                          <button
                            className="secondary"
                            disabled={busy === `cancel_${t.id}` || !!t.cancel_requested}
                            onClick={() => void cancelTask(t.id)}
                          >
                            {(busy === `cancel_${t.id}` || t.cancel_requested) ? "Canceling…" : "Cancel"}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {t.last_log && (
                      <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{t.last_log}</div>
                    )}
                    {t.error ? (
                      <div className="small" style={{ marginTop: 6, color: "#ffb7b7", whiteSpace: "pre-wrap" }}>{t.error}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>3) ComfyUI (Secondary still/motion engine)</div>
            <Badge ok={comfyOk} label={comfyOk ? "Available" : "Optional"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            NVIDIA/OpenUSD is the primary production handoff. EDMG can also talk to ComfyUI at <code>{status?.comfyui?.url ?? "http://127.0.0.1:8188"}</code> when you want alternate still or motion workflows.
          </div>
          <div className="small" style={{ marginTop: 10, opacity: 0.92 }}>
            To make ComfyUI show up inside the Workshop as a secondary engine, four things have to line up in order:
          </div>
          <ol className="small" style={{ marginTop: 8, paddingLeft: 18, display: "grid", gap: 6, opacity: 0.9 }}>
            <li>Setup must install or point the Workshop at a live ComfyUI server.</li>
            <li>Models must include a checkpoint or workflow family that uses the <code>ComfyUI</code> engine rather than the internal engine.</li>
            <li>Render or Timeline only exposes ComfyUI-specific paths when that ComfyUI model is selected for the job.</li>
            <li>If nothing changes in Render, the usual cause is that ComfyUI is not running, the selected model is still an internal model, or the required workflow or ControlNet assets are not installed yet.</li>
          </ol>
          <div className="small" style={{ marginTop: 8, opacity: 0.9 }}>
            In other words, installing ComfyUI does not replace the NVIDIA Workshop path. It adds an alternate engine lane that becomes available when the active render model is ComfyUI-backed.
          </div>
          {!isWindows ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.88 }}>
              Linux support uses a manually installed ComfyUI instance. Start it yourself only if you want those secondary workflows, and keep <code>EDMG_COMFYUI_URL</code> pointed at the running server.
            </div>
          ) : null}

          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="secondary"
              onClick={() => window.edmg?.openExternal?.(comfyDocsUrl)}
            >
              {isWindows ? "Open ComfyUI Portable Guide" : "Open ComfyUI Docs"}
            </button>
            <button
              disabled={!isWindows || busy === "comfyui_n"}
              onClick={() => run("comfyui_n", "/v1/setup/comfyui/portable/install", { flavor: "nvidia" })}
            >
              {busy === "comfyui_n" ? "Working…" : (isWindows ? "Install ComfyUI Portable (NVIDIA)" : "Windows-only Portable Install")}
            </button>
            <button
              disabled={!isWindows || busy === "start_comfy"}
              onClick={() => run("start_comfy", "/v1/setup/comfyui/portable/start", { flavor: "nvidia" })}
            >
              {busy === "start_comfy" ? "Starting…" : (isWindows ? "Start ComfyUI (NVIDIA)" : "Windows-only Portable Start")}
            </button>
          </div>

          {!comfyOk && status?.comfyui?.hint && (
            <div className="small" style={{ marginTop: 10 }}>
              Fix: {status.comfyui.hint}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>4) FFmpeg (Assembly)</div>
            <Badge ok={ffOk} label={ffOk ? "OK" : "Missing"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Used for stitching frames/clips into MP4 with audio.
          </div>
          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            Current path: {status?.ffmpeg?.path ?? "ffmpeg"}
            {status?.ffmpeg?.version ? ` • ${status.ffmpeg.version}` : ""}
          </div>
          {!ffOk && (
            <div className="small" style={{ marginTop: 10 }}>
              Fix: {status?.ffmpeg?.hint ?? "Packaged EDMG NVIDIA Workshop should include bundled FFmpeg. If this is a dev checkout, install FFmpeg and add it to PATH, or set EDMG_FFMPEG_PATH to the ffmpeg executable."}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>5) EDMG Core (Unified Engine)</div>
            <Badge ok={edmgOk} label={edmgOk ? "Ready" : "Optional"} />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            Workshop backend installs now target EDMG Core as part of the same product surface. Use this to repair or reinstall Core if the current backend environment is missing it.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              disabled={busy === "install_edmg"}
              onClick={() => run("install_edmg", "/v1/setup/edmg/install", { mode: "standard", backend: "nvidia" })}
            >
              {busy === "install_edmg" ? "Installing…" : (edmgOk ? "Repair EDMG Core" : "Install EDMG Core")}
            </button>
            <button
              className="secondary"
              disabled={busy === "verify_edmg"}
              onClick={verifyEdmgCore}
            >
              {busy === "verify_edmg" ? "Verifying…" : "Verify EDMG Core"}
            </button>
          </div>
          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            {edmgOk
              ? `Installed version: ${status?.edmg?.version || "unknown"}`
              : (status?.edmg?.hint || "The Workshop can run without EDMG Core, but richer analysis and full Deforum exports stay limited until it is installed.")}
          </div>
          {edmgVerifyResult ? (
            <div className="small" style={{ marginTop: 10, opacity: 0.88, whiteSpace: "pre-wrap" }}>
              Verify return code: <b>{edmgVerifyResult.returncode ?? "?"}</b>
              {edmgVerifyResult.raw ? <div style={{ marginTop: 6 }}>{String(edmgVerifyResult.raw)}</div> : null}
            </div>
          ) : null}
        </div>


        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 800 }}>6) Model Packs (Recommended)</div>
            <Badge
              ok={!!modelsCatalog?.installed?.hf_sdxl_base_1_0 || false}
              label={(modelsCatalog?.installed?.hf_sdxl_base_1_0 ? "SDXL ready" : "Optional")}
            />
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            EDMG NVIDIA Workshop does not bundle large weights in the installer. Pick a pack to download recommended models.
          </div>

          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {(modelsCatalog?.packs ?? []).map((p: any) => {
              const ids = p.models ?? [];
              const all = [...(modelsCatalog?.catalog ?? []), ...(modelsCatalog?.user ?? [])];
              const models = ids.map((id: string) => all.find((m: any) => m.id === id)).filter(Boolean);
              const accepted = !!packAccept[p.id];
              return (
                <div key={p.id} style={{ padding: 10, borderRadius: 12, background: "#121422", border: "1px solid #22263a", minWidth: 420 }}>
                  <div style={{ fontWeight: 900 }}>{p.name}</div>
                  <div className="small" style={{ marginTop: 4 }}>{p.description}</div>

                  <div className="small" style={{ marginTop: 8, fontWeight: 800 }}>Included models</div>
                  <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
                    {models.map((m: any) => (
                      <div key={m.id} className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <div>{m.name}</div>
                        <div style={{ display: "flex", gap: 8 }}>
                          <span style={{ opacity: 0.85 }}>{m.license_id ?? "unknown"}</span>
                          {m.license_url ? (
                            <button className="secondary" onClick={() => window.edmg?.openExternal?.(m.license_url)}>License</button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <label className="small" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={!!packAccept[p.id]}
                        onChange={(e) => setPackAccept((prev) => ({ ...prev, [p.id]: e.target.checked }))}
                      />
                      I accept the license terms for this pack
                    </label>
                    <button
                      disabled={!accepted || busy === `pack_${p.id}`}
                      onClick={() => installPack(p.id)}
                    >
                      {busy === `pack_${p.id}` ? "Installing…" : "Install"}
                    </button>
                  </div>
                </div>
              );
            })}
            <button className="secondary" onClick={() => onNavigate?.("models" as any)}>Open Model Manager</button>
          </div>

          <div className="small" style={{ marginTop: 10, opacity: 0.9 }}>
            Licenses: You will be asked to accept each model's license terms. SDXL uses an OpenRAIL++ license; community models vary.
          </div>
        </div>

        <div className="card">
          <div style={{ fontWeight: 800 }}>Ready check</div>
          <div className="small" style={{ marginTop: 6 }}>
            When the backend runtime bundle, NVIDIA profile, NVIDIA/NIM AI path, and FFmpeg are all OK, EDMG NVIDIA Workshop is ready for the NVIDIA workflow. ComfyUI and EDMG Core remain secondary support lanes.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              disabled={!setupReady}
              onClick={() => onNavigate?.("workspace")}
            >
              Go to Workspace
            </button>
            <button className="secondary" onClick={refresh}>
              Re-check
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
