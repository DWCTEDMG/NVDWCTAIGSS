import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { useStudioPageLayout } from "../components/studioLayout";
import { useUiMode } from "../components/uiMode";
import type { PageProps } from "../types/pageProps";

type CatalogEntry = {
  id: string;
  name: string;
  kind: string;
  source: string;
  recommended?: string;
  notes?: string;
  license_id?: string;
  license_url?: string;
  installable?: boolean;
  hf_repo_id?: string;
  hf_url?: string;
  filename?: string;
  tags?: string[];
  hardware_targets?: string[];
  collections?: string[];
};

type CatalogPayload = {
  catalog: CatalogEntry[];
  user: CatalogEntry[];
  packs: any[];
  accepted: Record<string, any>;
  installed: Record<string, boolean>;
  cloud?: Record<string, any>;
  storage_mode?: string;
  model_cache?: string | null;
};

type HubResult = {
  id: string;
  downloads?: number;
  likes?: number;
  lastModified?: string;
  pipeline_tag?: string;
  tags?: string[];
};

type HubCollection = {
  id: string;
  label: string;
  search: string;
  note: string;
  url: string;
};

type ModelsPanelId =
  | "packs"
  | "internal"
  | "runtime"
  | "discovery"
  | "imports"
  | "defaults"
  | "advanced";

const HUB_COLLECTIONS: HubCollection[] = [
  {
    id: "nvidia",
    label: "NVIDIA models",
    search: "diffusion",
    note: "NVIDIA-published models and assets for CUDA, TensorRT, and NIM-backed workflows.",
    url: "https://huggingface.co/nvidia",
  },
  {
    id: "nvidia-tensorrt",
    label: "TensorRT",
    search: "tensorrt diffusion",
    note: "TensorRT-oriented model assets for NVIDIA GPU acceleration.",
    url: "https://huggingface.co/models?search=nvidia%20tensorrt%20diffusion",
  },
  {
    id: "nvidia-nim",
    label: "NIM",
    search: "nvidia nim vision",
    note: "NVIDIA service-backed discovery for NIM and GPU-first inference assets.",
    url: "https://build.nvidia.com/explore/discover",
  },
];

const NVIDIA_LINKS = [
  { label: "NVIDIA models", url: "https://huggingface.co/nvidia" },
  { label: "NVIDIA NIM", url: "https://build.nvidia.com/explore/discover" },
  { label: "NIM docs", url: "https://docs.nvidia.com/nim/" },
  { label: "TensorRT", url: "https://github.com/NVIDIA/TensorRT" },
  { label: "TensorRT Model Optimizer", url: "https://github.com/NVIDIA/TensorRT-Model-Optimizer" },
];

function formatDate(value?: string) {
  if (!value) return "unknown";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

function repoIdFromEntry(model: CatalogEntry) {
  if (model.hf_repo_id) return model.hf_repo_id;
  if (model.hf_url) {
    const match = model.hf_url.match(/huggingface\.co\/([^/]+\/[^/]+)\/resolve\//i);
    if (match) return match[1];
  }
  return "";
}

function ModelCard({
  m,
  installed,
  cloudRecord,
  storageMode,
  accepted,
  onAccept,
  onInstall,
  onRestore,
  onOpen
}: {
  m: CatalogEntry;
  installed: boolean;
  cloudRecord?: any;
  storageMode?: string;
  accepted: boolean;
  onAccept: () => void;
  onInstall: () => void;
  onRestore: () => void;
  onOpen: (u: string) => void;
}) {
  const installable = m.installable !== false;
  const needsAccept = installable && !accepted;
  const canInstall = installable && !needsAccept;
  const cloudStored = !!cloudRecord;
  const cloudOnly = storageMode === "cloud_only";
  const statusLabel = installed ? "Installed locally" : cloudStored ? "Stored in S3" : installable ? "Not installed" : "Browser only";
  const installLabel = installed
    ? "Installed"
    : cloudStored
      ? "Stored in S3"
      : !installable
        ? "Install unavailable"
        : cloudOnly
        ? "Store in S3"
        : "Install";

  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 900 }}>{m.name}</div>
          <div className="small" style={{ marginTop: 4 }}>
            <span style={{ opacity: 0.85 }}>
              {m.kind} • {m.source}
              {m.recommended ? ` • ${m.recommended}` : ""}
            </span>
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            License: <b>{m.license_id ?? "unknown"}</b>
          </div>
          {m.tags?.length ? (
            <div className="small" style={{ marginTop: 6, opacity: 0.88 }}>
              Tags: <b>{m.tags.join(", ")}</b>
            </div>
          ) : null}
          {m.hardware_targets?.length ? (
            <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>
              Best on: <b>{m.hardware_targets.join(", ")}</b>
            </div>
          ) : null}
          {m.notes ? <div className="small" style={{ marginTop: 6 }}>{m.notes}</div> : null}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
          <div className="small" style={{ fontWeight: 800 }}>
            {statusLabel}
          </div>
          {cloudStored ? (
            <div className="small" style={{ maxWidth: 260, textAlign: "right", opacity: 0.82 }}>
              {String(cloudRecord.object || cloudRecord.key || "")}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {m.license_url ? (
              <button className="secondary" onClick={() => onOpen(m.license_url || "")}>View license</button>
            ) : null}
            {(m.hf_repo_id || m.hf_url) ? (
              <button className="secondary" onClick={() => onOpen(`https://huggingface.co/${repoIdFromEntry(m) || ""}`)}>
                Open model page
              </button>
            ) : null}
            {needsAccept ? (
              <button onClick={onAccept}>Accept license</button>
            ) : null}
            {cloudStored && !installed ? (
              <button className="secondary" onClick={onRestore}>Restore local</button>
            ) : null}
            <button disabled={!canInstall || installed || cloudStored} onClick={onInstall}>
              {installLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function HubResultCard({
  result,
  matchedCatalog,
  installed,
  cloudStored,
  accepted,
  onAccept,
  onInstall,
  onOpen,
}: {
  result: HubResult;
  matchedCatalog: CatalogEntry | null;
  installed: boolean;
  cloudStored?: boolean;
  accepted: boolean;
  onAccept: (model: CatalogEntry) => void;
  onInstall: (model: CatalogEntry) => void;
  onOpen: (url: string) => void;
}) {
  const modelUrl = `https://huggingface.co/${result.id}`;
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 900 }}>{result.id}</div>
          <div className="small" style={{ marginTop: 4, opacity: 0.85 }}>
            {(result.pipeline_tag || "model")} • {result.downloads ?? 0} downloads • {result.likes ?? 0} likes
          </div>
          <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>
            Updated: <b>{formatDate(result.lastModified)}</b>
          </div>
          {result.tags?.length ? (
            <div className="small" style={{ marginTop: 6, opacity: 0.82 }}>
              {result.tags.slice(0, 6).join(", ")}
            </div>
          ) : null}
          {matchedCatalog ? (
            <div className="small" style={{ marginTop: 8 }}>
              Studio match: <b>{matchedCatalog.name}</b>
            </div>
          ) : (
            <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>
              No direct Studio install mapping yet.
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
          <button className="secondary" onClick={() => onOpen(modelUrl)}>Open on Hugging Face</button>
          {matchedCatalog && matchedCatalog.installable !== false && !accepted ? (
            <button onClick={() => onAccept(matchedCatalog)}>Accept license</button>
          ) : null}
          {matchedCatalog ? (
            <button
              disabled={matchedCatalog.installable === false || installed || cloudStored || !accepted}
              onClick={() => onInstall(matchedCatalog)}
            >
              {installed ? "Installed locally" : cloudStored ? "Stored in S3" : matchedCatalog.installable === false ? "Browser only" : "Install in Studio"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function Models(props: PageProps) {
  const { mode } = useUiMode();
  const [data, setData] = useState<CatalogPayload | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [err, setErr] = useState<string>("");

  const [civitaiUrl, setCivitaiUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [localFolder, setLocalFolder] = useState("checkpoints");

  const [hubCollectionId, setHubCollectionId] = useState<string>(HUB_COLLECTIONS[0].id);
  const [hubQuery, setHubQuery] = useState<string>("");
  const [hubLoading, setHubLoading] = useState<boolean>(false);
  const [hubResults, setHubResults] = useState<HubResult[]>([]);
  const [hubError, setHubError] = useState<string>("");

  async function refresh() {
    setErr("");
    try {
      const d = await apiGet("/v1/models/catalog");
      setData(d as any);
      const t = await apiGet("/v1/models/tasks");
      setTasks((t as any)?.tasks ?? []);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, []);

  const merged = useMemo(() => {
    const built = data?.catalog ?? [];
    const user = data?.user ?? [];
    return { built, user };
  }, [data]);

  const hubCollection = useMemo(
    () => HUB_COLLECTIONS.find((item) => item.id === hubCollectionId) ?? HUB_COLLECTIONS[0],
    [hubCollectionId]
  );

  const builtByRepoId = useMemo(() => {
    const mapping = new Map<string, CatalogEntry>();
    for (const item of merged.built ?? []) {
      const repoId = repoIdFromEntry(item);
      if (repoId) mapping.set(repoId, item);
    }
    return mapping;
  }, [merged.built]);

  useEffect(() => {
    let cancelled = false;
    const loadHub = async () => {
      setHubLoading(true);
      setHubError("");
      try {
        const query = (hubQuery || hubCollection.search).trim() || hubCollection.search;
        const { listModels } = await import("@huggingface/hub");
        const nextResults: HubResult[] = [];
        for await (const item of listModels({
          search: query,
          author: "nvidia",
          limit: 8,
        } as any)) {
          nextResults.push({
            id: String((item as any).id || ""),
            downloads: Number((item as any).downloads || 0),
            likes: Number((item as any).likes || 0),
            lastModified: String((item as any).lastModified || ""),
            pipeline_tag: String((item as any).pipeline_tag || ""),
            tags: Array.isArray((item as any).tags) ? (item as any).tags.slice(0, 8) : [],
          });
        }
        if (!cancelled) setHubResults(nextResults);
      } catch (e: any) {
        if (!cancelled) {
          setHubError(String(e?.message ?? e));
          setHubResults([]);
        }
      } finally {
        if (!cancelled) setHubLoading(false);
      }
    };
    void loadHub();
    return () => {
      cancelled = true;
    };
  }, [hubCollection, hubQuery]);

  async function accept(m: CatalogEntry) {
    await apiPost("/v1/models/accept", { model_id: m.id, license_id: m.license_id ?? "unknown" });
    await refresh();
  }

  async function install(m: CatalogEntry) {
    await apiPost("/v1/models/install", { model_id: m.id });
    await refresh();
  }

  async function restoreLocal(m: CatalogEntry) {
    await apiPost("/v1/models/restore_local", { model_id: m.id });
    await refresh();
  }

  async function installPack(packId: string) {
    await apiPost("/v1/models/install_pack", { pack_id: packId });
    await refresh();
  }

  async function importCivitai() {
    setImporting(true);
    setErr("");
    try {
      await apiPost("/v1/models/import/civitai", { url: civitaiUrl });
      setCivitaiUrl("");
      await refresh();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setImporting(false);
    }
  }

  async function importLocal() {
    setErr("");
    const picked = await window.edmg?.pickFile?.({
      title: "Select model file",
      filters: [{ name: "Model files", extensions: ["safetensors", "ckpt", "pt", "bin"] }]
    });
    if (!picked) return;
    await apiPost("/v1/models/import/local", { file_path: picked, folder: localFolder });
    await refresh();
  }

  const acceptedMap = data?.accepted ?? {};
  const installedMap = data?.installed ?? {};
  const cloudMap = data?.cloud ?? {};
  const storageMode = data?.storage_mode ?? "local_cache";

  const internalSummary = useMemo(() => {
    const built = merged.built ?? [];
    const sd15 = built.find((m) => m.id === "hf_sd15_internal");
    const sdxl = built.find((m) => m.id === "hf_sdxl_internal");
    const sd35 = built.find((m) => m.id === "hf_sd35_medium_internal");
    const installedInternal = {
      sd15: !!installedMap["hf_sd15_internal"],
      sdxl: !!installedMap["hf_sdxl_internal"],
      sd35: !!installedMap["hf_sd35_medium_internal"],
    };
    const preferred = installedInternal.sd35 ? "SD3.5 Medium" : installedInternal.sdxl ? "SDXL" : installedInternal.sd15 ? "SD 1.5" : "none";
    return { sd15, sdxl, sd35, installedInternal, preferred };
  }, [merged, installedMap]);

  const defaultModels = (merged.built ?? []).filter((m) => m.recommended === "default" && m.installable !== false);
  const advancedModels = (merged.built ?? []).filter((m) => m.recommended !== "default" && m.installable !== false);
  const browserOnlyModels = (merged.built ?? []).filter((m) => m.installable === false);

  const panelDefinitions = useMemo(
    () => [
      {
        id: "packs" as const,
        label: "First-run packs",
        description: "Studio-supported starter packs and quick install actions.",
      },
      {
        id: "internal" as const,
        label: "Internal render readiness",
        description: "Preferred internal diffusion models and readiness summary.",
      },
      {
        id: "runtime" as const,
        label: "NVIDIA runtime",
        description: "CUDA, TensorRT, and NVIDIA service readiness for the only supported runtime path.",
      },
      {
        id: "discovery" as const,
        label: "Discovery",
        description: "Hub browsing and source collections for supported models.",
      },
      {
        id: "imports" as const,
        label: "Imports",
        description: "Bring community and local models into the Studio-managed catalog.",
      },
      {
        id: "defaults" as const,
        label: "Recommended defaults",
        description: "Studio-ready recommended models surfaced for day-to-day use.",
      },
      {
        id: "advanced" as const,
        label: "Advanced inventory",
        description: "Optional models, user models, browser-only bundles, and install tasks.",
      },
    ],
    [],
  );
  const { profileOptions, activeProfile, setActiveProfile, layoutState, visibleOrder, movePanel, updateHidden, resetLayout } =
    useStudioPageLayout<ModelsPanelId>(
      "models",
      panelDefinitions.map((panel) => panel.id),
    );
  const panelDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        panelDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<ModelsPanelId, (typeof panelDefinitions)[number]>,
    [panelDefinitions],
  );
  const panelControlItems = layoutState.order.map((panelId, index) => ({
    id: panelId,
    label: panelDefinitionById[panelId].label,
    description: panelDefinitionById[panelId].description,
    hidden: layoutState.hidden.includes(panelId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const panelContent: Record<ModelsPanelId, React.ReactNode> = {
    packs: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>First-run packs</div>
        <div className="small" style={{ marginTop: 6 }}>
          Pick a pack to get started. Packs install Studio-supported defaults only; browser-only runtime bundles stay separate until their execution path is ready.
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap" }}>
          {(data?.packs ?? []).map((p: any) => (
            <button key={p.id} onClick={() => installPack(p.id)}>
              Install: {p.name}
            </button>
          ))}
        </div>
        {mode === "advanced" && (data?.packs ?? []).length ? (
          <div className="small" style={{ marginTop: 10, opacity: 0.85 }}>
            Packs only enqueue installs. Track progress in the tasks list below.
          </div>
        ) : null}
      </div>
    ),
    internal: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Internal render readiness</div>
        <div className="small" style={{ marginTop: 6 }}>
          Internal video rendering is ready when at least one diffusers model is installed. SD3.5 Medium is preferred on stronger GPUs; SDXL is the balanced path; SD 1.5 is the lowest-resource CUDA option.
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          SD 1.5: <b>{internalSummary.installedInternal.sd15 ? "installed" : "missing"}</b>
          {" "}• SDXL: <b>{internalSummary.installedInternal.sdxl ? "installed" : "missing"}</b>
          {" "}• SD3.5 Medium: <b>{internalSummary.installedInternal.sd35 ? "installed" : "missing"}</b>
          {" "}• Preferred: <b>{internalSummary.preferred}</b>
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!internalSummary.installedInternal.sd15 && internalSummary.sd15 ? (
            <button onClick={() => install(internalSummary.sd15)}>Install SD 1.5 internal</button>
          ) : null}
          {!internalSummary.installedInternal.sdxl && internalSummary.sdxl ? (
            <button onClick={() => install(internalSummary.sdxl)}>Install SDXL internal</button>
          ) : null}
          {!internalSummary.installedInternal.sd35 && internalSummary.sd35 ? (
            <button onClick={() => install(internalSummary.sd35)}>Install SD3.5 internal</button>
          ) : null}
          <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
        </div>
      </div>
    ),
    runtime: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>NVIDIA runtime only</div>
        <div className="small" style={{ marginTop: 6 }}>
          Model installs feed the CUDA internal renderer, NVIDIA NIM planning services, and the NVIDIA RTX/OpenUSD launch profile. Non-NVIDIA runtime bridges are not exposed by this profile.
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="secondary" onClick={() => props.onNavigate?.("render")}>Open Render</button>
          <button className="secondary" onClick={() => props.onNavigate?.("settings")}>Open Settings</button>
        </div>
      </div>
    ),
    discovery: (
      <>
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>NVIDIA quick links</div>
          <div className="small" style={{ marginTop: 6 }}>
            These are the NVIDIA source collections and runtime references Studio uses for curated model discovery.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {NVIDIA_LINKS.map((link) => (
              <button key={link.url} className="secondary" onClick={() => window.edmg?.openExternal?.(link.url)}>
                {link.label}
              </button>
            ))}
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>NVIDIA Hub browser</div>
          <div className="small" style={{ marginTop: 6 }}>
            Powered by Hugging Face Hub search for a richer in-app browse flow. Studio only enables one-click install when a result maps cleanly to a supported local runtime path.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={hubCollectionId} onChange={(e) => setHubCollectionId(e.target.value)}>
              {HUB_COLLECTIONS.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
            <input
              style={{ minWidth: 320 }}
              value={hubQuery}
              onChange={(e) => setHubQuery(e.target.value)}
              placeholder={hubCollection.search}
            />
            <button className="secondary" onClick={() => window.edmg?.openExternal?.(hubCollection.url)}>Open official collection</button>
          </div>
          <div className="small" style={{ marginTop: 8, opacity: 0.84 }}>{hubCollection.note}</div>
          {hubError ? <div className="small" style={{ marginTop: 8, color: "var(--danger)" }}>{hubError}</div> : null}
          {hubLoading ? <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>Loading Hub results…</div> : null}
          {!hubLoading && !hubResults.length && !hubError ? (
            <div className="small" style={{ marginTop: 8, opacity: 0.8 }}>No results yet.</div>
          ) : null}
          {hubResults.map((result) => {
            const matchedCatalog = builtByRepoId.get(result.id) ?? null;
            return (
              <HubResultCard
                key={result.id}
                result={result}
                matchedCatalog={matchedCatalog}
                installed={matchedCatalog ? !!installedMap[matchedCatalog.id] : false}
                cloudStored={matchedCatalog ? !!cloudMap[matchedCatalog.id] : false}
                accepted={matchedCatalog ? !!acceptedMap[matchedCatalog.id] : false}
                onAccept={accept}
                onInstall={install}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
              />
            );
          })}
        </div>
      </>
    ),
    imports: (
      <>
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Add community model (Civitai)</div>
          <div className="small" style={{ marginTop: 6 }}>
            Paste a Civitai model URL (optionally with <code>modelVersionId=…</code>) or a numeric model ID. You&apos;ll be prompted to review license/terms.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              style={{ minWidth: 420 }}
              value={civitaiUrl}
              onChange={(e) => setCivitaiUrl(e.target.value)}
              placeholder="https://civitai.com/models/12345/…?modelVersionId=67890"
            />
            <button disabled={!civitaiUrl || importing} onClick={importCivitai}>
              {importing ? "Importing…" : "Import"}
            </button>
            <button className="secondary" onClick={() => window.edmg?.openExternal?.("https://civitai.com/")}>
              Open Civitai
            </button>
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 900 }}>Bring your own</div>
          <div className="small" style={{ marginTop: 6 }}>
            Add a local checkpoint, LoRA, or ControlNet. EDMG copies it into the Studio-managed ComfyUI models folder.
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <label className="small" style={{ fontWeight: 800 }}>Type:</label>
            <select value={localFolder} onChange={(e) => setLocalFolder(e.target.value)}>
              <option value="checkpoints">Checkpoint</option>
              <option value="loras">LoRA</option>
              <option value="embeddings">Embedding</option>
              <option value="vae">VAE</option>
              <option value="controlnet">ControlNet</option>
            </select>
            <button onClick={importLocal}>Pick file…</button>
          </div>
        </div>
      </>
    ),
    defaults: (
      <>
        <h3 style={{ marginTop: 18 }}>Recommended defaults</h3>
        {defaultModels.map((m) => (
          <ModelCard
            key={m.id}
            m={m}
            installed={!!installedMap[m.id]}
            cloudRecord={cloudMap[m.id]}
            storageMode={storageMode}
            accepted={!!acceptedMap[m.id]}
            onAccept={() => accept(m)}
            onInstall={() => install(m)}
            onRestore={() => restoreLocal(m)}
            onOpen={(u) => window.edmg?.openExternal?.(u)}
          />
        ))}
      </>
    ),
    advanced: mode === "advanced" ? (
      <>
        <h3 style={{ marginTop: 18 }}>Advanced / optional</h3>
        {advancedModels.map((m) => (
          <ModelCard
            key={m.id}
            m={m}
            installed={!!installedMap[m.id]}
            cloudRecord={cloudMap[m.id]}
            storageMode={storageMode}
            accepted={!!acceptedMap[m.id]}
            onAccept={() => accept(m)}
            onInstall={() => install(m)}
            onRestore={() => restoreLocal(m)}
            onOpen={(u) => window.edmg?.openExternal?.(u)}
          />
        ))}

        {browserOnlyModels.length ? (
          <>
            <h3 style={{ marginTop: 18 }}>Discovery-only runtime bundles</h3>
            <div className="small" style={{ opacity: 0.82 }}>
              These are discovery-only packages without a Studio-managed NVIDIA runtime mapping yet.
            </div>
            {browserOnlyModels.map((m) => (
              <ModelCard
                key={m.id}
                m={m}
                installed={!!installedMap[m.id]}
                cloudRecord={cloudMap[m.id]}
                storageMode={storageMode}
                accepted={!!acceptedMap[m.id]}
                onAccept={() => accept(m)}
                onInstall={() => install(m)}
                onRestore={() => restoreLocal(m)}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
              />
            ))}
          </>
        ) : null}

        <h3 style={{ marginTop: 18 }}>User models</h3>
        {(merged.user ?? []).length ? (
          (merged.user ?? []).map((m) => (
            <div key={m.id}>
              <ModelCard
                m={m}
                installed={!!installedMap[m.id]}
                cloudRecord={cloudMap[m.id]}
                storageMode={storageMode}
                accepted={!!acceptedMap[m.id]}
                onAccept={() => accept(m)}
                onInstall={() => install(m)}
                onRestore={() => restoreLocal(m)}
                onOpen={(u) => window.edmg?.openExternal?.(u)}
              />
              <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
                <button className="secondary" onClick={() => apiPost("/v1/models/remove_user", { model_id: m.id }).then(refresh)}>
                  Remove from list
                </button>
              </div>
            </div>
          ))
        ) : (
          <div className="small" style={{ opacity: 0.8 }}>No user models yet.</div>
        )}

        <h3 style={{ marginTop: 18 }}>Install tasks</h3>
        {(tasks ?? []).length ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {(tasks ?? []).slice(0, 12).map((t: any) => (
              <div key={t.id} style={{ padding: 10, borderRadius: 12, background: "#121422", border: "1px solid #22263a" }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <div style={{ fontWeight: 800 }}>{t.name}</div>
                  <div className="small">{t.status}{t.progress != null ? ` • ${Math.round(t.progress * 100)}%` : ""}</div>
                </div>
                {t.last_log ? <div className="small" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{t.last_log}</div> : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="small" style={{ opacity: 0.8 }}>No active tasks.</div>
        )}
      </>
    ) : (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 900 }}>Advanced inventory</div>
        <div className="small" style={{ marginTop: 6 }}>
          Switch Settings to <b>Advanced</b> UI mode when you want optional models, user-model management, and install task details.
        </div>
      </div>
    ),
  };

  return (
    <div>
      <h2>Model Manager</h2>
      <div className="small" style={{ marginTop: 6 }}>
        EDMG ships with a curated model catalog, but does <b>not</b> bundle large weights in the installer. Use this page to install Studio-ready defaults, add community models, or browse NVIDIA-oriented model families.
      </div>
      <div className="small" style={{ marginTop: 8, opacity: 0.86 }}>
        Storage mode: <b>{storageMode === "cloud_only" ? "S3-only" : "local + cache"}</b>
        {data?.model_cache ? <> • Cache: <b>{data.model_cache}</b></> : null}
      </div>

      {err && (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 10, background: "#2a1b1b", border: "1px solid #5b2424" }}>
          <div style={{ fontWeight: 800 }}>Error</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{err}</pre>
        </div>
      )}
      <StudioLayoutCustomizer
        title="Model Manager layout"
        description="Reorder or hide major model-management sections without changing install queues, runtime choices, or the catalog itself."
        items={panelControlItems}
        profileOptions={profileOptions}
        activeProfile={activeProfile}
        onSelectProfile={setActiveProfile}
        onMove={movePanel}
        onToggleHidden={updateHidden}
        onReset={resetLayout}
      />

      {visibleOrder.map((panelId) => (
        <React.Fragment key={panelId}>{panelContent[panelId]}</React.Fragment>
      ))}
    </div>
  );
}
