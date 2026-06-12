import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../components/api";
import NvidiaReadinessCard from "../components/NvidiaReadinessCard";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { STUDIO_THEME_OPTIONS, useStudioAppearance } from "../components/studioAppearance";
import { useStudioPageLayout } from "../components/studioLayout";
import { useUiMode } from "../components/uiMode";
import { clearRenderDefaults, readRenderDefaults, writeRenderDefaults } from "../components/renderDefaults";
import type { PageProps } from "../types/pageProps";

type SecretName = "hf_token" | "civitai_api_key" | "openai_compat_api_key";

type StudioAiSettings = {
  mode: string;
  provider: string;
  aiBaseUrl: string;
  openaiCompatBaseUrl: string;
  openaiCompatModel: string;
  source?: string;
};

type StudioBackendSettings = {
  mode: string;
  host: string;
  port: string;
  url: string;
  source?: string;
};

type SettingsPanelId =
  | "uiMode"
  | "appearance"
  | "renderDefaults"
  | "desktopBackend"
  | "aiProvider"
  | "backendConfig"
  | "liveAiStatus"
  | "tokens"
  | "renderRuntime"
  | "comfyui"
  | "deforum";

const DEFAULT_AI_SETTINGS: StudioAiSettings = {
  mode: "local",
  provider: "openai_compat",
  aiBaseUrl: "http://127.0.0.1:7862",
  openaiCompatBaseUrl: "http://127.0.0.1:8001/v1",
  openaiCompatModel: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  source: "default",
};

const DEFAULT_BACKEND_SETTINGS: StudioBackendSettings = {
  mode: "managed",
  host: "127.0.0.1",
  port: "7863",
  url: "",
  source: "default",
};

function normalizeAiSettings(payload?: Partial<StudioAiSettings> | null): StudioAiSettings {
  const current = payload ?? {};
  const mode = String(current.mode ?? DEFAULT_AI_SETTINGS.mode).trim().toLowerCase();
  const providerRaw = String(current.provider ?? DEFAULT_AI_SETTINGS.provider).trim().toLowerCase();
  const provider =
    providerRaw === "openai" || providerRaw === "openai-compatible" || providerRaw === "openai_compat"
      ? "openai_compat"
      : DEFAULT_AI_SETTINGS.provider;

  return {
    mode: mode === "http" || mode === "remote" ? "http" : "local",
    provider: provider || DEFAULT_AI_SETTINGS.provider,
    aiBaseUrl: String(current.aiBaseUrl ?? DEFAULT_AI_SETTINGS.aiBaseUrl).trim() || DEFAULT_AI_SETTINGS.aiBaseUrl,
    openaiCompatBaseUrl:
      String(current.openaiCompatBaseUrl ?? DEFAULT_AI_SETTINGS.openaiCompatBaseUrl).trim() ||
      DEFAULT_AI_SETTINGS.openaiCompatBaseUrl,
    openaiCompatModel:
      String(current.openaiCompatModel ?? DEFAULT_AI_SETTINGS.openaiCompatModel).trim() ||
      DEFAULT_AI_SETTINGS.openaiCompatModel,
    source: String(current.source ?? DEFAULT_AI_SETTINGS.source),
  };
}

function aiSettingsFingerprint(settings: Partial<StudioAiSettings> | null | undefined): string {
  const normalized = normalizeAiSettings(settings);
  return JSON.stringify({
    mode: normalized.mode,
    provider: normalized.provider,
    aiBaseUrl: normalized.aiBaseUrl,
    openaiCompatBaseUrl: normalized.openaiCompatBaseUrl,
    openaiCompatModel: normalized.openaiCompatModel,
  });
}

function normalizeBackendSettings(payload?: Partial<StudioBackendSettings> | null): StudioBackendSettings {
  const current = payload ?? {};
  const modeRaw = String(current.mode ?? DEFAULT_BACKEND_SETTINGS.mode).trim().toLowerCase();
  const mode = modeRaw === "external" || modeRaw === "remote" || modeRaw === "connect" ? "external" : "managed";
  const host = String(current.host ?? DEFAULT_BACKEND_SETTINGS.host).trim() || DEFAULT_BACKEND_SETTINGS.host;
  const portRaw = String(current.port ?? DEFAULT_BACKEND_SETTINGS.port).trim();
  const portNumber = Number(portRaw);
  const port =
    Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535
      ? String(portNumber)
      : DEFAULT_BACKEND_SETTINGS.port;
  const rawUrl = String(current.url ?? "").trim();
  const normalizedUrl = sanitizeBackendUrl(rawUrl);

  return {
    mode,
    host,
    port,
    url: mode === "external" ? (rawUrl ? normalizedUrl || rawUrl : "") : "",
    source: String(current.source ?? DEFAULT_BACKEND_SETTINGS.source),
  };
}

function backendSettingsFingerprint(settings: Partial<StudioBackendSettings> | null | undefined): string {
  const normalized = normalizeBackendSettings(settings);
  return JSON.stringify({
    mode: normalized.mode,
    host: normalized.host,
    port: normalized.port,
    url: normalized.mode === "external" ? normalized.url : "",
  });
}

function buildBackendUrl(settings: Partial<StudioBackendSettings> | null | undefined): string {
  const normalized = normalizeBackendSettings(settings);
  if (normalized.mode === "external") {
    return sanitizeBackendUrl(normalized.url) || normalized.url || buildManagedBackendUrl(normalized.host, normalized.port);
  }
  return buildManagedBackendUrl(normalized.host, normalized.port);
}

function buildManagedBackendUrl(host: string, port: string): string {
  return `http://${host}:${port}`;
}

function sanitizeBackendUrl(rawUrl: string): string {
  const candidate = String(rawUrl || "").trim();
  if (!candidate) return "";
  try {
    const parsed = new URL(candidate);
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

function parseBackendUrl(rawUrl: string): Partial<StudioBackendSettings> {
  const sanitized = sanitizeBackendUrl(rawUrl);
  if (!sanitized) return {};
  try {
    const parsed = new URL(sanitized);
    return {
      host: parsed.hostname || DEFAULT_BACKEND_SETTINGS.host,
      port: parsed.port || (parsed.protocol === "https:" ? "443" : "80"),
      url: sanitized,
    };
  } catch {
    return {};
  }
}

export default function Settings(props: PageProps) {
  const { mode, setMode } = useUiMode();
  const { theme, setTheme } = useStudioAppearance();
  const [cfg, setCfg] = useState<any>(null);
  const [aiStatus, setAiStatus] = useState<any>(null);
  const [nvidiaStatus, setNvidiaStatus] = useState<any>(null);
  const [nvidiaDiagnostics, setNvidiaDiagnostics] = useState<any>(null);
  const [edmgTemplate, setEdmgTemplate] = useState<any>(null);
  const [secrets, setSecrets] = useState<any>(null);
  const [hardware, setHardware] = useState<any>(null);
  const [renderProfiles, setRenderProfiles] = useState<any>(null);
  const [_renderProviders, setRenderProviders] = useState<any>(null);
  const [renderProviderDraft, setRenderProviderDraft] = useState<any>(null);
  const [savedRenderDefaults, setSavedRenderDefaults] = useState<any>(() => readRenderDefaults());
  const [studioBackendSettings, setStudioBackendSettings] = useState<StudioBackendSettings>(DEFAULT_BACKEND_SETTINGS);
  const [backendDraft, setBackendDraft] = useState<StudioBackendSettings>(DEFAULT_BACKEND_SETTINGS);
  const [backendSettingsLoaded, setBackendSettingsLoaded] = useState<boolean>(false);
  const [studioAiSettings, setStudioAiSettings] = useState<StudioAiSettings>(DEFAULT_AI_SETTINGS);
  const [aiDraft, setAiDraft] = useState<StudioAiSettings>(DEFAULT_AI_SETTINGS);
  const [aiSettingsLoaded, setAiSettingsLoaded] = useState<boolean>(false);
  const [hfToken, setHfToken] = useState<string>("");
  const [civitaiKey, setCivitaiKey] = useState<string>("");
  const [openaiCompatApiKey, setOpenaiCompatApiKey] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [savingBackend, setSavingBackend] = useState<boolean>(false);
  const [savingAi, setSavingAi] = useState<boolean>(false);
  const [_savingProviders, setSavingProviders] = useState<boolean>(false);
  const [backendRestartRequired, setBackendRestartRequired] = useState<boolean>(false);
  const [backendNotice, setBackendNotice] = useState<string | null>(null);
  const [aiRestartRequired, setAiRestartRequired] = useState<boolean>(false);
  const [aiNotice, setAiNotice] = useState<string | null>(null);
  const [liveBackendUrl, setLiveBackendUrl] = useState<string>(props.backendUrl || "");
  const [backendReachable, setBackendReachable] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void refreshPage();
  }, []);

  const aiSettingsDirty = useMemo(
    () => aiSettingsFingerprint(aiDraft) !== aiSettingsFingerprint(studioAiSettings),
    [aiDraft, studioAiSettings]
  );

  const backendSettingsDirty = useMemo(
    () => backendSettingsFingerprint(backendDraft) !== backendSettingsFingerprint(studioBackendSettings),
    [backendDraft, studioBackendSettings]
  );
  const optionalNvidiaServices = useMemo(() => {
    const diagnosticServices = nvidiaDiagnostics?.nvidia?.services || {};
    const profileServices = nvidiaStatus?.nvidia?.services || {};
    const services = { ...profileServices, ...diagnosticServices };
    return [
      ["omniverse", "Omniverse"],
      ["nemo", "NeMo"],
      ["triton", "Triton"],
      ["audio2face", "Audio2Face"],
      ["ace", "ACE"],
      ["cosmos", "Cosmos"],
    ].map(([id, label]) => {
      const service = services?.[id] || {};
      const probe = service?.probe || {};
      const configured = Boolean(service?.configured);
      const reachable = Boolean(probe?.reachable);
      const state = !configured
        ? "not configured"
        : probe?.skipped
          ? "configured"
          : reachable
            ? "reachable"
            : "not reachable";
      const detail = String(service?.model || service?.base_url || probe?.error || "").trim();
      return { id, label, state, configured, reachable, detail };
    });
  }, [nvidiaDiagnostics, nvidiaStatus]);
  const hardwareProfile = hardware?.hardware || {};
  const hardwareDisplayName = String(hardwareProfile.host_gpu_name || hardwareProfile.device_name || "unknown");
  const runtimeBackend = String(hardwareProfile.backend || hardwareProfile.device || hardwareProfile.backend_family || "cpu");
  const runtimeDeviceName = String(
    hardwareProfile.runtime_device_name ||
    (runtimeBackend.toLowerCase() === "cpu" ? "CPU" : hardwareProfile.device_name || runtimeBackend)
  );
  const cudaRuntimeLabel = hardwareProfile.cuda_runtime_ready
    ? "active"
    : hardwareProfile.host_gpu_visible
      ? "not active"
      : "";

  async function refreshPage() {
    try {
      const nextCfg = await apiGet("/v1/config");
      setCfg(nextCfg);
      await refreshBackendStartupSettings();
      await refreshAiStartupSettings(nextCfg);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
      await refreshBackendStartupSettings();
      await refreshAiStartupSettings(null);
    }

    apiGet("/v1/ai/status").then(setAiStatus).catch(() => {});
    apiGet("/v1/nvidia/status").then(setNvidiaStatus).catch(() => {});
    apiGet("/v1/nvidia/diagnostics").then(setNvidiaDiagnostics).catch(() => {});
    apiGet("/v1/edmg/deforum_template").then(setEdmgTemplate).catch(() => {});
    apiGet("/v1/settings/secrets/status").then(setSecrets).catch(() => {});
    apiGet("/v1/hardware").then(setHardware).catch(() => {});
    apiGet("/v1/settings/render_profiles").then(setRenderProfiles).catch(() => {});
    apiGet("/v1/settings/render_providers").then((d) => {
      setRenderProviders(d);
      setRenderProviderDraft(d?.settings ?? null);
    }).catch(() => {});
  }

  async function refreshAiStartupSettings(nextCfg: any) {
    try {
      if (window.edmg?.getAiSettings) {
        const saved = await window.edmg.getAiSettings();
        if (saved?.ok) {
          const normalized = normalizeAiSettings(saved);
          setStudioAiSettings(normalized);
          setAiDraft(normalized);
          setAiSettingsLoaded(true);
          return;
        }
      }
    } catch {
      // fall through to backend snapshot
    }

    const fallback = normalizeAiSettings({
      mode: nextCfg?.ai_mode,
      provider: nextCfg?.ai_provider,
      aiBaseUrl: nextCfg?.ai_base_url,
      openaiCompatBaseUrl: nextCfg?.ai_openai_compat_base_url,
      openaiCompatModel: nextCfg?.ai_openai_compat_model,
      source: nextCfg ? "backend" : "default",
    });
    setStudioAiSettings(fallback);
    setAiDraft(fallback);
    setAiSettingsLoaded(true);
  }

  async function refreshBackendHealth(urlCandidate: string) {
    const target = String(urlCandidate || "").trim();
    if (!target) {
      setBackendReachable(null);
      return;
    }

    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timeoutId = controller ? window.setTimeout(() => controller.abort(), 2500) : 0;
    try {
      const response = await fetch(`${target}/health`, {
        method: "GET",
        signal: controller?.signal,
      });
      setBackendReachable(!!response.ok);
    } catch {
      setBackendReachable(false);
    } finally {
      if (timeoutId) window.clearTimeout(timeoutId);
    }
  }

  async function refreshBackendStartupSettings() {
    try {
      if (window.edmg?.getBackendSettings) {
        const saved = await window.edmg.getBackendSettings();
        if (saved?.ok) {
          const normalized = normalizeBackendSettings(saved);
          const nextLiveUrl = String(saved.currentBackendUrl || props.backendUrl || buildBackendUrl(normalized)).trim();
          setStudioBackendSettings(normalized);
          setBackendDraft(normalized);
          setBackendSettingsLoaded(true);
          setLiveBackendUrl(nextLiveUrl);
          await refreshBackendHealth(nextLiveUrl);
          return;
        }
      }
    } catch {
      // fall through to runtime URL snapshot
    }

    const fallback = normalizeBackendSettings({
      ...parseBackendUrl(props.backendUrl),
      source: props.backendUrl ? "runtime" : "default",
    });
    const nextLiveUrl = String(props.backendUrl || buildBackendUrl(fallback)).trim();
    setStudioBackendSettings(fallback);
    setBackendDraft(fallback);
    setBackendSettingsLoaded(true);
    setLiveBackendUrl(nextLiveUrl);
    await refreshBackendHealth(nextLiveUrl);
  }

  async function refreshSecrets() {
    try {
      const s = await apiGet("/v1/settings/secrets/status");
      setSecrets(s);
    } catch {
      // ignore
    }
  }

  async function refreshBackendAiStatus() {
    try {
      const nextCfg = await apiGet("/v1/config");
      setCfg(nextCfg);
    } catch {
      // ignore
    }

    try {
      const nextStatus = await apiGet("/v1/ai/status");
      setAiStatus(nextStatus);
    } catch {
      // ignore
    }

    try {
      const nextNvidiaStatus = await apiGet("/v1/nvidia/status");
      setNvidiaStatus(nextNvidiaStatus);
    } catch {
      // ignore
    }

    try {
      const nextNvidiaDiagnostics = await apiGet("/v1/nvidia/diagnostics");
      setNvidiaDiagnostics(nextNvidiaDiagnostics);
    } catch {
      // ignore
    }
  }

  async function saveSecret(name: SecretName, value: string) {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/set", { name, value });
      if (name === "hf_token") setHfToken("");
      if (name === "civitai_api_key") setCivitaiKey("");
      if (name === "openai_compat_api_key") setOpenaiCompatApiKey("");
      await refreshSecrets();
      await refreshBackendAiStatus();
      const nextProviders = await apiGet("/v1/settings/render_providers");
      setRenderProviders(nextProviders);
      setRenderProviderDraft(nextProviders?.settings ?? null);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  function applyRenderProfile(profileId: "laptop_safe" | "balanced_auto" | "high_quality") {
    const p = renderProfiles?.profiles?.[profileId];
    if (!p) return;
    const next = {
      profileId,
      renderPreset: p.render_preset,
      internalRenderTier: p.internal_render_tier,
      internalResumeExisting: !!p.resume_existing_frames,
    };
    writeRenderDefaults(next);
    setSavedRenderDefaults(next);
  }

  function resetRenderProfile() {
    clearRenderDefaults();
    setSavedRenderDefaults({});
  }

  function updateBackendDraft(patch: Partial<StudioBackendSettings>) {
    setBackendDraft((current) => {
      const merged = { ...current, ...patch, source: current.source };
      const nextMode = normalizeBackendSettings({ mode: merged.mode }).mode;
      if (nextMode === "external" && !String(merged.url ?? "").trim()) {
        merged.url = buildManagedBackendUrl(current.host, current.port);
      }
      return normalizeBackendSettings(merged);
    });
    setBackendNotice(null);
  }

  function updateAiDraft(patch: Partial<StudioAiSettings>) {
    setAiDraft((current) => normalizeAiSettings({ ...current, ...patch, source: current.source }));
    setAiNotice(null);
  }

  async function saveBackendSettings() {
    setSavingBackend(true);
    setErr(null);
    setBackendNotice(null);
    try {
      if (!window.edmg?.setBackendSettings) {
        throw new Error("This Workshop build cannot persist desktop backend startup settings yet.");
      }
      const normalizedDraft = normalizeBackendSettings(backendDraft);
      const backendUrl =
        normalizedDraft.mode === "external"
          ? sanitizeBackendUrl(normalizedDraft.url)
          : "";
      if (normalizedDraft.mode === "external" && !backendUrl) {
        throw new Error("Enter a valid backend URL starting with http:// or https://.");
      }
      const response = await window.edmg.setBackendSettings({
        mode: normalizedDraft.mode,
        host: normalizedDraft.host,
        port: normalizedDraft.port,
        url: backendUrl,
      });
      if (!response?.ok) {
        throw new Error(response?.error || "Failed to save desktop backend settings.");
      }
      const normalized = normalizeBackendSettings({ ...response, source: "bootstrap" });
      const nextLiveUrl = String(response.currentBackendUrl || buildBackendUrl(normalized)).trim();
      setStudioBackendSettings(normalized);
      setBackendDraft(normalized);
      setBackendRestartRequired(!!response.restartRequired);
      setLiveBackendUrl(nextLiveUrl);
      setBackendNotice(
        response.restartRequired
          ? "Saved. Restart Workshop so it relaunches against the selected backend target."
          : "Saved."
      );
      await refreshBackendHealth(nextLiveUrl);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSavingBackend(false);
    }
  }

  async function saveAiSettings() {
    setSavingAi(true);
    setErr(null);
    setAiNotice(null);
    try {
      if (!window.edmg?.setAiSettings) {
        throw new Error("This Workshop build cannot persist AI startup settings yet.");
      }
      const response = await window.edmg.setAiSettings(normalizeAiSettings(aiDraft));
      if (!response?.ok) {
        throw new Error(response?.error || "Failed to save AI startup settings.");
      }
      const normalized = normalizeAiSettings({ ...response, source: "bootstrap" });
      setStudioAiSettings(normalized);
      setAiDraft(normalized);
      setAiRestartRequired(!!response.restartRequired);
      setAiNotice(
        response.restartRequired
          ? "Saved. Restart Workshop so the backend relaunches on the new AI provider."
          : "Saved."
      );
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSavingAi(false);
    }
  }

  async function clearSecret(name: SecretName) {
    setSaving(true);
    setErr(null);
    try {
      await apiPost("/v1/settings/secrets/clear", { name });
      await refreshSecrets();
      await refreshBackendAiStatus();
      const nextProviders = await apiGet("/v1/settings/render_providers");
      setRenderProviders(nextProviders);
      setRenderProviderDraft(nextProviders?.settings ?? null);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

   async function _saveRenderProviders() {
    setSavingProviders(true);
    setErr(null);
    try {
      const next = await apiPost("/v1/settings/render_providers", renderProviderDraft || {});
      setRenderProviders(next?.status ?? next);
      setRenderProviderDraft(next?.settings ?? renderProviderDraft);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setSavingProviders(false);
    }
  }

  const panelDefinitions = useMemo(
    () => [
      {
        id: "uiMode" as const,
        label: "UI Mode",
        description: "Simple vs advanced interface density and control exposure.",
      },
      {
        id: "appearance" as const,
        label: "Appearance",
        description: "Theme selection and future customization entrypoint.",
      },
      {
        id: "renderDefaults" as const,
        label: "Render defaults",
        description: "Saved profiles that the Render page can pick up automatically.",
      },
      {
        id: "desktopBackend" as const,
        label: "Desktop backend",
        description: "Startup mode and managed vs external backend target.",
      },
      {
        id: "aiProvider" as const,
        label: "AI Provider",
        description: "Saved startup provider, routing, and credential-adjacent controls.",
      },
      {
        id: "backendConfig" as const,
        label: "Backend config snapshot",
        description: "Raw `/v1/config` inspection card.",
      },
      {
        id: "liveAiStatus" as const,
        label: "Live AI / NVIDIA Status",
        description: "Read-only runtime provider and NVIDIA service status from the active backend.",
      },
      {
        id: "tokens" as const,
        label: "Tokens",
        description: "Saved gated-download and NVIDIA provider credentials status.",
      },
      {
        id: "renderRuntime" as const,
        label: "NVIDIA Render Runtime",
        description: "Read-only CUDA and NVIDIA service render routing status.",
      },
      {
        id: "comfyui" as const,
        label: "ComfyUI workflow",
        description: "Reference guidance for the secondary ComfyUI routing path.",
      },
      {
        id: "deforum" as const,
        label: "Deforum template",
        description: "EDMG Core-derived Deforum export reference.",
      },
    ],
    [],
  );
  const {
    profileOptions,
    activeProfile,
    setActiveProfile,
    layoutState,
    visibleOrder,
    movePanel,
    updateHidden,
    resetLayout,
  } = useStudioPageLayout<SettingsPanelId>(
    "settings",
    panelDefinitions.map((panel) => panel.id),
  );
  const panelDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        panelDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<SettingsPanelId, (typeof panelDefinitions)[number]>,
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

  const panelContent: Record<SettingsPanelId, React.ReactNode> = {
    uiMode: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>UI Mode</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Simple keeps the day-to-day workflow one-click. Advanced exposes every knob (routing, parameters, debugging).
        </div>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <button className={mode === "simple" ? "" : "secondary"} onClick={() => setMode("simple")}>Simple</button>
          <button className={mode === "advanced" ? "" : "secondary"} onClick={() => setMode("advanced")}>Advanced</button>
          <div className="small" style={{ opacity: 0.8 }}>current: <b>{mode}</b></div>
        </div>
      </div>
    ),
    appearance: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Appearance</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Theme changes affect this React control surface. They do not affect NVIDIA Kit panels, backend spawning, model installs, render settings, or package outputs.
        </div>
        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <div className="small" style={{ fontWeight: 800, marginBottom: 6 }}>Studio theme</div>
            <select value={theme} onChange={(event) => setTheme(event.target.value as any)}>
              {STUDIO_THEME_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
            <div className="studio-themeSwatches" aria-hidden="true">
              {STUDIO_THEME_OPTIONS.map((option) => (
                <div
                  key={option.id}
                  className="studio-themeSwatch"
                  data-theme={option.id}
                  title={`${option.label}: ${option.description}`}
                />
              ))}
            </div>
          </div>
          <div className="small" style={{ opacity: 0.86 }}>
            Current phase: Dashboard, Projects, Settings, Models, Workshop Forge, and the advanced workbench pages support modular section ordering and visibility. These controls only change local presentation, not NVIDIA runtime behavior.
          </div>
        </div>
      </div>
    ),
    renderDefaults: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Render defaults</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Save a hardware-aware quick profile here and the Render page will pick it up automatically. This is the safest place to tune laptop vs workstation behavior without changing project content.
        </div>
        <div className="small" style={{ marginBottom: 10, opacity: 0.9 }}>
          Hardware: <b>{hardwareDisplayName}</b> • runtime <b>{runtimeBackend}</b>
          {runtimeDeviceName && runtimeDeviceName !== hardwareDisplayName ? <> on <b>{runtimeDeviceName}</b></> : null}
          {" "}• backend family <b>{hardwareProfile.backend_family || "cpu_only"}</b>
          {cudaRuntimeLabel ? <> • CUDA runtime <b>{cudaRuntimeLabel}</b></> : null}
          {" "}• recommended tier <b>{hardwareProfile.recommended_tier || "draft"}</b>
        </div>
        {renderProfiles ? (
          <div style={{ display: "grid", gap: 10 }}>
            {Object.entries(renderProfiles.profiles || {}).map(([id, profile]: any) => (
              <div key={id} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontWeight: 800 }}>{profile.label} {renderProfiles.recommended_profile === id ? <span className="small" style={{ marginLeft: 6, opacity: 0.8 }}>(recommended)</span> : null}</div>
                    <div className="small" style={{ opacity: 0.85 }}>{profile.description}</div>
                    <div className="small" style={{ marginTop: 4, opacity: 0.82 }}>Preset <b>{profile.render_preset}</b> • internal tier <b>{profile.internal_render_tier}</b> • resume caches <b>{profile.resume_existing_frames ? "on" : "off"}</b></div>
                  </div>
                  <button className={savedRenderDefaults?.profileId === id ? "" : "secondary"} onClick={() => applyRenderProfile(id as any)}>Use on Render page</button>
                </div>
              </div>
            ))}
            <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button className="secondary" onClick={resetRenderProfile}>Clear saved defaults</button>
              <div className="small" style={{ opacity: 0.82 }}>Current saved profile: <b>{savedRenderDefaults?.profileId || "none"}</b></div>
            </div>
          </div>
        ) : (
          <div className="small" style={{ opacity: 0.75 }}>Loading render profiles…</div>
        )}
      </div>
    ),
    desktopBackend: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Desktop Backend</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Choose whether Workshop should launch and own its local backend or just connect the GUI to an already running backend. This keeps Windows-first packaging intact while making Ubuntu and Linux desktop installs first-class.
        </div>
        <div className="small" style={{ marginBottom: 12, opacity: 0.85 }}>
          Saved startup config: <b>{backendSettingsLoaded ? `${studioBackendSettings.mode} (${buildBackendUrl(studioBackendSettings)})` : "loading"}</b>
          {studioBackendSettings.source ? <span> • source <b>{studioBackendSettings.source}</b></span> : null}
        </div>
        <div className="small" style={{ marginBottom: 12, opacity: 0.82 }}>
          Live runtime target: <b>{liveBackendUrl || props.backendUrl || buildBackendUrl(backendDraft)}</b>
          {" "}• health{" "}
          <b>
            {backendReachable == null ? "checking" : backendReachable ? "reachable" : "unreachable"}
          </b>
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Mode</div>
            <select
              aria-label="Desktop backend mode"
              value={backendDraft.mode}
              onChange={(e) => updateBackendDraft({ mode: e.target.value })}
            >
              <option value="managed">Managed local backend</option>
              <option value="external">Connect to existing backend</option>
            </select>
          </div>

          {backendDraft.mode === "external" ? (
            <div>
              <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Desktop backend URL</div>
              <input
                aria-label="Desktop backend URL"
                value={backendDraft.url}
                onChange={(e) => updateBackendDraft({ url: e.target.value })}
                placeholder="https://edmg-backend.example.com"
              />
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
              <div>
                <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Backend host</div>
                <input
                  aria-label="Desktop backend host"
                  value={backendDraft.host}
                  onChange={(e) => updateBackendDraft({ host: e.target.value })}
                  placeholder="127.0.0.1"
                />
              </div>
              <div>
                <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Backend port</div>
                <input
                  aria-label="Desktop backend port"
                  inputMode="numeric"
                  value={backendDraft.port}
                  onChange={(e) => updateBackendDraft({ port: e.target.value })}
                  placeholder="7863"
                />
              </div>
            </div>
          )}

          <div className="small" style={{ opacity: 0.82 }}>
            Ubuntu/Linux desktop note: managed mode keeps the full GUI and bundled startup flow, while external mode is for cases where you want the desktop app to attach to a separately launched backend over `http://` or `https://`.
          </div>

          <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button disabled={savingBackend || !backendSettingsDirty || !window.edmg?.setBackendSettings} onClick={saveBackendSettings}>
              {savingBackend ? "Saving…" : "Save backend startup settings"}
            </button>
            {backendRestartRequired && window.edmg?.relaunch ? (
              <button className="secondary" disabled={savingBackend} onClick={() => { void window.edmg?.relaunch?.(); }}>
                Restart now
              </button>
            ) : null}
            {backendNotice ? <div className="small" style={{ opacity: 0.84 }}>{backendNotice}</div> : null}
          </div>
        </div>
      </div>
    ),
    aiProvider: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>NVIDIA AI Runtime</div>
        <div className="small" style={{ marginBottom: 10 }}>
          These are backend startup settings for NVIDIA Kit planning and prompt generation. NVIDIA NIM / OpenAI-compatible is the default path; save changes here, then restart the backend or NVIDIA Kit runtime so the selected provider is active.
        </div>
        <div className="small" style={{ marginBottom: 12, opacity: 0.85 }}>
          Saved NVIDIA startup config: <b>{aiSettingsLoaded ? `${aiDraft.mode === "http" ? "remote_ai_service" : aiDraft.provider}` : "loading"}</b>
          {studioAiSettings.source ? <span> • source <b>{studioAiSettings.source}</b></span> : null}
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Mode</div>
            <select value={aiDraft.mode} onChange={(e) => updateAiDraft({ mode: e.target.value })}>
              <option value="local">Local provider inside Workshop backend</option>
              <option value="http">Remote AI service over HTTP</option>
            </select>
          </div>

          {aiDraft.mode === "local" ? (
            <>
                <div>
                  <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Runtime provider</div>
                  <select value={aiDraft.provider} onChange={(e) => updateAiDraft({ provider: e.target.value })}>
                    <option value="openai_compat">NVIDIA NIM / OpenAI-compatible</option>
                  </select>
                </div>

              {aiDraft.provider === "openai_compat" ? (
                <>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>NVIDIA NIM / OpenAI-compatible base URL</div>
                    <input value={aiDraft.openaiCompatBaseUrl} onChange={(e) => updateAiDraft({ openaiCompatBaseUrl: e.target.value })} placeholder="http://127.0.0.1:8001/v1" />
                  </div>
                  <div>
                    <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Model</div>
                    <input value={aiDraft.openaiCompatModel} onChange={(e) => updateAiDraft({ openaiCompatModel: e.target.value })} placeholder="nvidia/llama-3.3-nemotron-super-49b-v1.5" />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
                    <div>
                      <div className="small" style={{ fontWeight: 800 }}>API key</div>
                      <div className="small" style={{ opacity: 0.8 }}>
                        Optional for local NIM-compatible tools. Required for hosted NVIDIA or gateway providers that expect bearer auth.
                      </div>
                      <input
                        value={openaiCompatApiKey}
                        onChange={(e) => setOpenaiCompatApiKey(e.target.value)}
                        placeholder={secrets?.has_openai_compat_api_key ? "(set) paste to replace" : "paste API key if needed"}
                      />
                    </div>
                    <button disabled={saving || !openaiCompatApiKey} onClick={() => saveSecret("openai_compat_api_key", openaiCompatApiKey)}>Save</button>
                    <button className="secondary" disabled={saving || !secrets?.has_openai_compat_api_key} onClick={() => clearSecret("openai_compat_api_key")}>Clear</button>
                  </div>
                  <div className="small" style={{ opacity: 0.82 }}>
                    Use this for NVIDIA NIM first. Non-NVIDIA OpenAI-style endpoints are compatibility gateways, not the primary product runtime.
                  </div>
                </>
              ) : null}
            </>
          ) : (
            <>
              <div>
                <div className="small" style={{ fontWeight: 800, marginBottom: 4 }}>Remote AI service base URL</div>
                <input value={aiDraft.aiBaseUrl} onChange={(e) => updateAiDraft({ aiBaseUrl: e.target.value })} placeholder="http://127.0.0.1:7862" />
              </div>
              <div className="small" style={{ opacity: 0.82 }}>
                Use this only when an external NVIDIA-compatible gateway owns planning outside the Workshop backend.
              </div>
            </>
          )}

          <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button disabled={savingAi || !aiSettingsDirty || !window.edmg?.setAiSettings} onClick={saveAiSettings}>Save NVIDIA AI startup settings</button>
            {aiRestartRequired && window.edmg?.relaunch ? (
              <button className="secondary" disabled={savingAi} onClick={() => { void window.edmg?.relaunch?.(); }}>Restart now</button>
            ) : null}
            {aiNotice ? <div className="small" style={{ opacity: 0.84 }}>{aiNotice}</div> : null}
          </div>
        </div>
      </div>
    ),
    backendConfig: cfg ? <div className="card"><pre>{JSON.stringify(cfg, null, 2)}</pre></div> : null,
    liveAiStatus: aiStatus || nvidiaStatus || nvidiaDiagnostics ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Live NVIDIA Runtime Status</div>
        <div className="small" style={{ marginBottom: 10 }}>
          This is the provider and NVIDIA service profile the backend is using right now. If it differs from the saved startup config above, restart the backend or NVIDIA Kit runtime to apply your latest change.
        </div>
        {nvidiaDiagnostics?.nvidia?.readiness ? (
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12, marginBottom: 12 }}>
            <NvidiaReadinessCard
              asCard={false}
              diagnostics={nvidiaDiagnostics}
              status={nvidiaStatus}
              onRefresh={() => { void refreshBackendAiStatus(); }}
            />
          </div>
        ) : null}
        {nvidiaStatus?.nvidia ? (
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12, marginBottom: 12 }}>
            <div style={{ fontWeight: 800 }}>NVIDIA service profile</div>
            <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
              Mode: <b>{nvidiaStatus.nvidia.enabled ? "enabled" : "disabled"}</b>
              {" "}• profile <b>{nvidiaStatus.nvidia.profile || "omniverse"}</b>
              {" "}• NGC key <b>{nvidiaStatus.nvidia.credentials?.ngc_api_key_configured ? "configured" : "not configured"}</b>
            </div>
            <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
              NIM: <b>{nvidiaStatus.nvidia.services?.nim?.configured ? "configured" : "not configured"}</b>
              {nvidiaStatus.nvidia.services?.nim?.model ? <> • model <b>{nvidiaStatus.nvidia.services.nim.model}</b></> : null}
              {" "}• Riva: <b>{nvidiaStatus.nvidia.services?.riva?.configured ? "configured" : "not configured"}</b>
              {" "}• Omniverse: <b>{nvidiaStatus.nvidia.services?.omniverse?.configured ? "configured" : "not configured"}</b>
            </div>
            {nvidiaDiagnostics?.nvidia ? (
              <>
                <div style={{ fontWeight: 800, marginTop: 10 }}>NVIDIA runtime diagnostics</div>
                <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
                  GPU: <b>{nvidiaDiagnostics.nvidia.host?.gpu?.ok ? "ready" : "not ready"}</b>
                  {nvidiaDiagnostics.nvidia.host?.gpu?.gpus?.[0]?.name ? <> • {nvidiaDiagnostics.nvidia.host.gpu.gpus[0].name}</> : null}
                  {" "}• Docker NVIDIA runtime: <b>{nvidiaDiagnostics.nvidia.host?.docker?.nvidia_runtime ? "ready" : "not ready"}</b>
                </div>
                <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
                  NIM endpoint: <b>{nvidiaDiagnostics.nvidia.services?.nim?.probe?.reachable ? "reachable" : "not reachable"}</b>
                  {nvidiaDiagnostics.nvidia.services?.nim?.probe?.models_url ? <> • <code>{nvidiaDiagnostics.nvidia.services.nim.probe.models_url}</code></> : null}
                </div>
                <div style={{ fontWeight: 800, marginTop: 10 }}>NVIDIA service stack</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginTop: 8 }}>
                  {optionalNvidiaServices.map((service) => (
                    <div key={service.id} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 8 }}>
                      <div className="small" style={{ fontWeight: 800 }}>{service.label}</div>
                      <div className="small" style={{ marginTop: 4, opacity: 0.84 }}>
                        <b>{service.state}</b>
                        {service.detail ? <><br /><code>{service.detail}</code></> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </div>
        ) : null}
        <pre>{JSON.stringify({ ai: aiStatus, nvidia: nvidiaStatus, diagnostics: nvidiaDiagnostics }, null, 2)}</pre>
      </div>
    ) : null,
    tokens: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Tokens</div>
        <div className="small" style={{ marginBottom: 10 }}>
          Optional. Needed only for gated Hugging Face downloads and some Civitai downloads. Stored in OS keychain when available; otherwise stored locally under the Workshop data directory.
        </div>
        {secrets ? (
          <div className="small" style={{ marginBottom: 10, opacity: 0.9 }}>
            Storage: <b>{secrets.store}</b>
            {secrets.note ? <span style={{ marginLeft: 10, opacity: 0.85 }}>{secrets.note}</span> : null}
          </div>
        ) : (
          <div className="small" style={{ marginBottom: 10, opacity: 0.75 }}>Loading token status…</div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center" }}>
          <div>
            <div className="small" style={{ fontWeight: 800 }}>Hugging Face token</div>
            <div className="small" style={{ opacity: 0.8 }}>Used for gated HF models/checkpoints.</div>
            <input
              value={hfToken}
              onChange={(e) => setHfToken(e.target.value)}
              placeholder={secrets?.has_hf_token ? "(set) paste to replace" : "paste token"}
            />
          </div>
          <button disabled={saving || !hfToken} onClick={() => saveSecret("hf_token", hfToken)}>Save</button>
          <button className="secondary" disabled={saving || !secrets?.has_hf_token} onClick={() => clearSecret("hf_token")}>Clear</button>

          <div>
            <div className="small" style={{ fontWeight: 800 }}>Civitai API key</div>
            <div className="small" style={{ opacity: 0.8 }}>Used for some Civitai imports/downloads.</div>
            <input
              value={civitaiKey}
              onChange={(e) => setCivitaiKey(e.target.value)}
              placeholder={secrets?.has_civitai_api_key ? "(set) paste to replace" : "paste API key"}
            />
          </div>
          <button disabled={saving || !civitaiKey} onClick={() => saveSecret("civitai_api_key", civitaiKey)}>Save</button>
          <button className="secondary" disabled={saving || !secrets?.has_civitai_api_key} onClick={() => clearSecret("civitai_api_key")}>Clear</button>

        </div>
      </div>
    ),
    renderRuntime: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>NVIDIA Render Runtime</div>
        <div className="small" style={{ marginBottom: 10 }}>
          This profile routes production rendering through NVIDIA CUDA internal rendering or NVIDIA Kit/OpenUSD jobs. Alternate hosted and non-NVIDIA device render providers are not configurable from Workshop.
        </div>
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
            <div style={{ fontWeight: 800 }}>CUDA internal renderer</div>
            <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
              Backend: <b>{hardwareProfile.backend || "unknown"}</b>
              {" "}• device <b>{hardwareProfile.device_name || hardwareProfile.device || "not detected"}</b>
            </div>
          </div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
            <div style={{ fontWeight: 800 }}>NVIDIA service render path</div>
            <div className="small" style={{ marginTop: 6, opacity: 0.86 }}>
              NIM: <b>{nvidiaStatus?.nvidia?.services?.nim?.configured ? "configured" : "not configured"}</b>
              {" "}• Omniverse: <b>{nvidiaStatus?.nvidia?.services?.omniverse?.configured ? "configured" : "not configured"}</b>
            </div>
          </div>
        </div>
      </div>
    ),
    comfyui: (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>ComfyUI workflow</div>
        <div className="small">
          Workshop keeps NVIDIA/OpenUSD as the primary production handoff and ships curated ComfyUI routing for secondary stills, AnimateDiff motion, SVD image-to-video, and reference-driven ControlNet stills. The manual checkpoint override still uses <code>EDMG_COMFYUI_CHECKPOINT</code> when no catalog-backed model is selected.
        </div>
        <div className="small" style={{ marginTop: 10, opacity: 0.92 }}>
          Use this mental model:
        </div>
        <ul className="guide-list" style={{ marginTop: 8 }}>
          <li>Setup gets ComfyUI installed and reachable at the configured URL.</li>
          <li>Models decides which base checkpoints and ControlNet units Workshop knows how to route through that ComfyUI server.</li>
          <li>Render only exposes ComfyUI workflow export and ComfyUI-specific still or motion paths when the selected model actually uses the `ComfyUI` engine and the server capabilities are live.</li>
          <li>If Render is still acting like nothing changed, the usual cause is one of three things: ComfyUI is not running, the selected model is an `Internal` model rather than a `ComfyUI` model, or the needed workflow family or ControlNet model is not installed yet.</li>
        </ul>
      </div>
    ),
    deforum: edmgTemplate ? (
      <div className="card" style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Deforum template (from EDMG Core)</div>
        <div className="small">This is an editing surface / reference for Deforum exports.</div>
        <pre>{JSON.stringify(edmgTemplate, null, 2)}</pre>
      </div>
    ) : null,
  };

  return (
    <div>
      <h1>Settings</h1>
      {err && <div style={{ color: "var(--danger)" }}>{err}</div>}
      <StudioLayoutCustomizer
        title="Settings layout"
        description="Reorder or hide Settings panels for your own workflow. This does not change what any setting saves or how the desktop/backend behaves."
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
