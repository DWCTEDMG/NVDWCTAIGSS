import React, { useMemo, useEffect, useState } from "react";
import { apiGet } from "../components/api";
import { StudioLayoutCustomizer } from "../components/StudioLayoutCustomizer";
import { useStudioPageLayout } from "../components/studioLayout";
import { useStudioWorkbenchProject } from "../workbenches/useStudioWorkbenchProject";
import { STUDIO_FORGE_BRIDGES } from "../studio-forge/bridges";
import {
  buildStudioForgeRecommendations,
  type StudioForgeRecommendation,
  type StudioForgeRecommendationStatus,
} from "../studio-forge/recommendations";
import { STUDIO_FORGE_RECIPES } from "../studio-forge/recipes";
import { STUDIO_FORGE_TEMPLATES } from "../studio-forge/templates";
import type {
  StudioForgeBridge,
  StudioForgeBridgeTransport,
  StudioForgeCapability,
  StudioForgeRecipe,
  StudioForgeTemplate,
} from "../studio-forge/types";
import type { PageProps } from "../types/pageProps";

type RuntimeStatus = "available" | "missing" | "optional" | "required" | "unknown" | "error";
type BadgeStatus = RuntimeStatus | "preview";
type StudioForgeSectionId = "runtime" | "recommendations" | "templates" | "recipes" | "bridges" | "validation";

type RuntimeCard = {
  id: string;
  label: string;
  role: string;
  status: RuntimeStatus;
  detail: string;
  impact: string;
};

const CAPABILITY_LABELS: Record<StudioForgeCapability, string> = {
  backend: "Backend",
  openaiCompatible: "OpenAI-compatible",
  nvidiaKit: "NVIDIA Kit",
  nvidiaNim: "NVIDIA NIM",
  nvidiaRiva: "NVIDIA Riva",
  nvidiaTriton: "NVIDIA Triton",
  nvidiaOmniverse: "NVIDIA Omniverse",
  comfyui: "Secondary ComfyUI",
  ffmpeg: "FFmpeg",
  internalRenderer: "Secondary Internal Renderer",
  edmgCore: "EDMG Core",
};

const VALIDATION_COMMANDS = [
  "pnpm run typecheck",
  "pnpm run lint",
  "pnpm run test:ui",
  "pnpm run build",
  "pnpm run validate:desktop",
  "python ../../studio/nvidia-kit/tools/validate_kit_layout.py",
  "python ../../studio/nvidia-kit/tools/validate_scene_plan.py ../../studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json",
  "powershell -NoProfile -ExecutionPolicy Bypass -File ../../deployment/nvidia/stage_nvidia_kit_app.ps1 -PackageMode thin",
];

const BRIDGE_TRANSPORT_LABELS: Record<StudioForgeBridgeTransport, string> = {
  fileExport: "File export",
  http: "HTTP",
  websocket: "WebSocket",
  osc: "OSC",
  remoteControl: "Remote Control",
};

function statusStyle(status: BadgeStatus): React.CSSProperties {
  if (status === "preview") {
    return { background: "#24163a", border: "1px solid #47305f", color: "#dcc2ff" };
  }
  if (status === "available") {
    return { background: "#163a1f", border: "1px solid #245b32", color: "#b7ffcb" };
  }
  if (status === "missing" || status === "error") {
    return { background: "#3a1616", border: "1px solid #5b2424", color: "#ffb7b7" };
  }
  if (status === "optional") {
    return { background: "#16283a", border: "1px solid #24415b", color: "#b7dcff" };
  }
  if (status === "required") {
    return { background: "#3a3116", border: "1px solid #5b4d24", color: "#ffe6a0" };
  }
  return { background: "#232530", border: "1px solid #363a4a", color: "#d2d7ea" };
}

function statusLabel(status: BadgeStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function StatusBadge({ status, label }: { status: BadgeStatus; label?: string }) {
  return (
    <span
      style={{
        ...statusStyle(status),
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 800,
        letterSpacing: 0.3,
        padding: "4px 10px",
      }}
    >
      {label ?? statusLabel(status)}
    </span>
  );
}

function CapabilityList({
  label,
  capabilities,
}: {
  label: string;
  capabilities: StudioForgeCapability[];
}) {
  return (
    <div className="small" style={{ display: "grid", gap: 4 }}>
      <div style={{ fontWeight: 800 }}>{label}</div>
      <div>{capabilities.map((capability) => CAPABILITY_LABELS[capability]).join(", ")}</div>
    </div>
  );
}

function TemplateCard({ template }: { template: StudioForgeTemplate }) {
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <div className="timeline-kicker">Template Preview</div>
          <div style={{ fontWeight: 900 }}>{template.name}</div>
        </div>
        <StatusBadge status={template.status} />
      </div>
      <div className="small" style={{ marginTop: 8 }}>
        {template.description}
      </div>
      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div className="small">Kind: <b>{template.kind}</b></div>
        <CapabilityList label="Required capabilities" capabilities={template.requiredCapabilities} />
        {template.optionalCapabilities?.length ? (
          <CapabilityList label="Optional capabilities" capabilities={template.optionalCapabilities} />
        ) : null}
        <div className="small">Execution: read-only preview only</div>
      </div>
    </div>
  );
}

function RecipeCard({ recipe }: { recipe: StudioForgeRecipe }) {
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <div className="timeline-kicker">Workflow Recipe</div>
          <div style={{ fontWeight: 900 }}>{recipe.name}</div>
        </div>
        <StatusBadge status={recipe.status} />
      </div>
      <div className="small" style={{ marginTop: 8 }}>
        {recipe.description}
      </div>
      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div className="small">
          Flow: <b>{recipe.stages.join(" -> ")}</b>
        </div>
        <CapabilityList label="Required capabilities" capabilities={recipe.requiredCapabilities} />
        {recipe.optionalCapabilities?.length ? (
          <CapabilityList label="Optional capabilities" capabilities={recipe.optionalCapabilities} />
        ) : null}
        <div className="small">Execution: preview only, no writes or runtime control</div>
      </div>
    </div>
  );
}

function BridgeCard({
  bridge,
  previewPayload,
}: {
  bridge: StudioForgeBridge;
  previewPayload?: Record<string, unknown> | null;
}) {
  const payload = previewPayload ?? bridge.previewPayload;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <div className="timeline-kicker">Unreal Bridge Preview</div>
          <div style={{ fontWeight: 900 }}>{bridge.name}</div>
        </div>
        <StatusBadge status={bridge.status} />
      </div>
      <div className="small" style={{ marginTop: 8 }}>
        {bridge.description}
      </div>
      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div className="small">
          Transport: <b>{bridge.transports.map((transport) => BRIDGE_TRANSPORT_LABELS[transport]).join(" -> ")}</b>
        </div>
        <div className="small">
          Outputs: <b>{bridge.outputs.join(", ")}</b>
        </div>
        <CapabilityList label="Required capabilities" capabilities={bridge.requiredCapabilities} />
        {bridge.optionalCapabilities?.length ? (
          <CapabilityList label="Optional capabilities" capabilities={bridge.optionalCapabilities} />
        ) : null}
        <div className="small">
          Preview payload:
          <pre
            style={{
              marginTop: 8,
              padding: 10,
              borderRadius: 10,
              overflowX: "auto",
              background: "rgba(0, 0, 0, 0.28)",
              border: "1px solid rgba(255, 255, 255, 0.08)",
              whiteSpace: "pre-wrap",
            }}
          >
            {JSON.stringify(payload, null, 2)}
          </pre>
        </div>
        <div className="small">{bridge.limitations}</div>
      </div>
    </div>
  );
}

function recommendationBadgeStatus(
  status: StudioForgeRecommendationStatus,
): { badge: BadgeStatus; label: string } {
  if (status === "ready") return { badge: "available", label: "Ready now" };
  if (status === "optionalBoost") return { badge: "optional", label: "Optional boosts" };
  return { badge: "missing", label: "Setup needed" };
}

function formatCapabilityList(
  capabilities: StudioForgeCapability[],
  capabilityLabels: Record<StudioForgeCapability, string>,
): string {
  return capabilities.map((capability) => capabilityLabels[capability]).join(", ");
}

function recommendationSummary(
  recommendation: StudioForgeRecommendation,
  capabilityLabels: Record<StudioForgeCapability, string>,
): string {
  if (recommendation.status === "ready") {
    return "Ready with the currently detected runtime stack.";
  }
  if (recommendation.status === "optionalBoost") {
    return `Ready now. Optional boosts not detected: ${formatCapabilityList(recommendation.missingOptional, capabilityLabels)}.`;
  }
  return `Needs before previewing a live path: ${formatCapabilityList(recommendation.missingRequired, capabilityLabels)}.`;
}

function RecommendationCard({
  recommendation,
}: {
  recommendation: StudioForgeRecommendation;
}) {
  const badge = recommendationBadgeStatus(recommendation.status);
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <div className="timeline-kicker">{recommendation.kindLabel}</div>
          <div style={{ fontWeight: 900 }}>{recommendation.name}</div>
        </div>
        <StatusBadge status={badge.badge} label={badge.label} />
      </div>
      <div className="small" style={{ marginTop: 8 }}>
        {recommendation.description}
      </div>
      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div className="small">{recommendationSummary(recommendation, CAPABILITY_LABELS)}</div>
        {recommendation.missingRequired.length ? (
          <CapabilityList
            label="Missing required capabilities"
            capabilities={recommendation.missingRequired}
          />
        ) : (
          <div className="small">All required capabilities are currently detected.</div>
        )}
        {recommendation.missingOptional.length ? (
          <CapabilityList
            label="Optional boosts not detected"
            capabilities={recommendation.missingOptional}
          />
        ) : (
          <div className="small">No optional capability gaps for this preview.</div>
        )}
      </div>
    </div>
  );
}

export default function StudioForge({ backendUrl, config, onNavigate }: PageProps) {
  const { projectId, selectedVariant } = useStudioWorkbenchProject();
  const [health, setHealth] = useState<any>(null);
  const [healthError, setHealthError] = useState<string>("");
  const [setupStatus, setSetupStatus] = useState<any>(null);
  const [setupError, setSetupError] = useState<string>("");
  const [backendConfig, setBackendConfig] = useState<any>(null);
  const [backendConfigError, setBackendConfigError] = useState<string>("");
  const [nvidiaDiagnostics, setNvidiaDiagnostics] = useState<any>(null);
  const [nvidiaDiagnosticsError, setNvidiaDiagnosticsError] = useState<string>("");
  const [comfyCapabilities, setComfyCapabilities] = useState<any>(null);
  const [comfyError, setComfyError] = useState<string>("");
  const [unrealPreview, setUnrealPreview] = useState<any>(null);
  const [unrealPreviewError, setUnrealPreviewError] = useState<string>("");

  useEffect(() => {
    if (!backendUrl) return;
    let alive = true;

    const load = async (
      path: string,
      onSuccess: (value: any) => void,
      onError: (message: string) => void,
    ) => {
      try {
        const value = await apiGet(path);
        if (!alive) return;
        onSuccess(value);
        onError("");
      } catch (error: any) {
        if (!alive) return;
        onError(String(error?.message ?? error));
      }
    };

    void load("/health", setHealth, setHealthError);
    void load("/v1/config", setBackendConfig, setBackendConfigError);
    void load("/v1/setup/status", setSetupStatus, setSetupError);
    void load("/v1/nvidia/diagnostics", setNvidiaDiagnostics, setNvidiaDiagnosticsError);
    void load("/v1/comfyui/capabilities", setComfyCapabilities, setComfyError);

    return () => {
      alive = false;
    };
  }, [backendUrl]);

  useEffect(() => {
    if (!backendUrl || !projectId) {
      setUnrealPreview(null);
      setUnrealPreviewError("");
      return;
    }
    let alive = true;
    apiGet(`/v1/projects/${projectId}/unreal/preview?variant_index=${selectedVariant}`)
      .then((value) => {
        if (!alive) return;
        setUnrealPreview(value?.preview ?? null);
        setUnrealPreviewError("");
      })
      .catch((error: any) => {
        if (!alive) return;
        setUnrealPreview(null);
        setUnrealPreviewError(String(error?.message ?? error));
      });
    return () => {
      alive = false;
    };
  }, [backendUrl, projectId, selectedVariant]);

  const aiConfig = setupStatus?.ai_config ?? {};
  const activeConfig = backendConfig ?? config ?? {};
  const backendBundleOk = !!setupStatus?.backend_bundle?.ok;
  const ffmpegOk = !!setupStatus?.ffmpeg?.ok;
  const nvidiaProfile = setupStatus?.nvidia_profile ?? nvidiaDiagnostics?.nvidia?.profile ?? {};
  const nvidiaEnabled = !!nvidiaProfile?.enabled || !!setupStatus?.nvidia_mode;
  const nvidiaServices = nvidiaDiagnostics?.nvidia?.services ?? {};
  const nvidiaHost = nvidiaDiagnostics?.nvidia?.host ?? {};
  const nvidiaGpuOk = !!nvidiaHost?.gpu?.ok;
  const nimReady = !!nvidiaServices?.nim?.probe?.reachable || aiConfig?.provider === "openai_compat";
  const rivaReady = !!nvidiaServices?.riva?.configured || !!nvidiaProfile?.services?.riva?.configured;
  const tritonReady = !!nvidiaServices?.triton?.configured || !!nvidiaProfile?.services?.triton?.configured;
  const omniverseReady = !!nvidiaServices?.omniverse?.configured || !!nvidiaProfile?.services?.omniverse?.configured || nvidiaGpuOk;
  const kitReady = nvidiaEnabled && (nvidiaGpuOk || omniverseReady || !nvidiaDiagnosticsError);
  const setupReady = backendBundleOk && ffmpegOk && nvidiaEnabled && nimReady;
  const missingSetupParts = [
    !backendBundleOk ? "backend bundle" : null,
    !ffmpegOk ? "FFmpeg" : null,
    !nvidiaEnabled ? "NVIDIA profile" : null,
    !nimReady ? "NVIDIA NIM" : null,
  ].filter(Boolean);

  const comfyUrl = String(setupStatus?.comfyui?.url ?? "http://127.0.0.1:8188");
  const comfyConfigured = !!setupStatus?.comfyui?.ok;
  const comfyAvailable = !comfyError && !!comfyCapabilities;
  const ffmpegPath = String(setupStatus?.ffmpeg?.path ?? "ffmpeg");
  const edmgAvailable = !!setupStatus?.edmg?.available;
  const activeAiProvider = String(
    activeConfig?.ai_provider ??
    activeConfig?.provider ??
    aiConfig?.provider ??
    "",
  ).toLowerCase();
  const activeAiMode = String(activeConfig?.ai_mode ?? activeConfig?.mode ?? "").toLowerCase();
  const openaiCompatibleConfigured = activeAiProvider === "openai_compat" || activeAiMode === "openai_compat";

  const availableCapabilities = useMemo<StudioForgeCapability[]>(() => {
    const capabilities: StudioForgeCapability[] = [];
    if (health?.ok && !healthError) capabilities.push("backend");
    if (backendBundleOk && !setupError) capabilities.push("internalRenderer");
    if (ffmpegOk && !setupError) capabilities.push("ffmpeg");
    if (openaiCompatibleConfigured) capabilities.push("openaiCompatible");
    if (kitReady) capabilities.push("nvidiaKit");
    if (nimReady) capabilities.push("nvidiaNim");
    if (rivaReady) capabilities.push("nvidiaRiva");
    if (tritonReady) capabilities.push("nvidiaTriton");
    if (omniverseReady) capabilities.push("nvidiaOmniverse");
    if (comfyAvailable) capabilities.push("comfyui");
    if (edmgAvailable) capabilities.push("edmgCore");
    return capabilities;
  }, [
    backendBundleOk,
    comfyAvailable,
    edmgAvailable,
    ffmpegOk,
    health?.ok,
    healthError,
    kitReady,
    nimReady,
    nvidiaEnabled,
    omniverseReady,
    openaiCompatibleConfigured,
    rivaReady,
    setupError,
    tritonReady,
  ]);
  const recommendations = useMemo(
    () =>
      buildStudioForgeRecommendations({
        bridges: STUDIO_FORGE_BRIDGES,
        templates: STUDIO_FORGE_TEMPLATES,
        recipes: STUDIO_FORGE_RECIPES,
        availableCapabilities,
      }),
    [availableCapabilities],
  );
  const liveBridgePreviewById = useMemo<Record<string, Record<string, unknown>>>(
    () =>
      unrealPreview
        ? {
            "unreal-shot-metadata-export": unrealPreview.shot_metadata_export as Record<string, unknown>,
            "unreal-render-handoff": unrealPreview.render_handoff as Record<string, unknown>,
            "unreal-live-control-bridge": unrealPreview.live_control_bridge as Record<string, unknown>,
          }
        : {},
    [unrealPreview],
  );

  const runtimeCards: RuntimeCard[] = [
    {
      id: "backend",
      label: "Backend",
      role: "Required",
      status: healthError ? "error" : health?.ok ? "available" : "unknown",
      detail: healthError || backendUrl,
      impact: "Local FastAPI services remain the source of truth for project, setup, and render operations.",
    },
    {
      id: "setup",
      label: "NVIDIA Runtime Setup",
      role: "Required",
      status: setupError ? "error" : setupStatus ? (setupReady ? "available" : "missing") : "unknown",
      detail: setupError || (
        setupReady
          ? "Backend bundle, FFmpeg, and the NVIDIA AI path are ready."
          : `Still needs ${missingSetupParts.join(", ")}.`
      ),
      impact: "Studio Forge reads setup health only; NVIDIA Kit launch and repair stay in the runtime launcher and Setup page.",
    },
    {
      id: "nvidia-nim",
      label: "NVIDIA NIM",
      role: "Required",
      status: nvidiaDiagnosticsError ? (nimReady ? "available" : "unknown") : (nimReady ? "available" : "missing"),
      detail: aiConfig?.model
        ? `${aiConfig.model}${aiConfig?.base_url ? ` • ${aiConfig.base_url}` : ""}`
        : "NIM/OpenAI-compatible planner model is configured through backend startup settings.",
      impact: "NIM is the primary AI planner for Kit scene plans and OpenUSD handoff metadata.",
    },
    {
      id: "nvidia-kit",
      label: "NVIDIA Kit / RTX",
      role: "Required",
      status: kitReady ? "available" : "missing",
      detail: nvidiaGpuOk
        ? `GPU ready${nvidiaHost?.gpu?.gpus?.[0]?.name ? ` • ${nvidiaHost.gpu.gpus[0].name}` : ""}`
        : "Kit runtime readiness depends on an installed Kit SDK and NVIDIA GPU/service profile.",
      impact: "Kit owns the primary desktop shell, OpenUSD stage, RTX viewport, and render-job consumer.",
    },
    {
      id: "nvidia-services",
      label: "NVIDIA Services",
      role: "Service stack",
      status: rivaReady || tritonReady || omniverseReady ? "available" : "optional",
      detail: `Riva ${rivaReady ? "ready" : "optional"} • Triton ${tritonReady ? "ready" : "optional"} • Omniverse ${omniverseReady ? "ready" : "optional"}`,
      impact: "Riva, Triton, Audio2Face/ACE, Cosmos, and Omniverse service profiles extend the Kit runtime without replacing it.",
    },
    {
      id: "comfyui",
      label: "Secondary ComfyUI",
      role: "Legacy optional",
      status: comfyAvailable ? "available" : (comfyConfigured ? "unknown" : (comfyError ? "optional" : "optional")),
      detail: comfyAvailable
        ? `${comfyUrl} responded to capability discovery.`
        : `${comfyUrl} is unavailable or not configured for this session.`,
      impact: "NVIDIA/OpenUSD remains the primary handoff even when ComfyUI is missing.",
    },
    {
      id: "ffmpeg",
      label: "FFmpeg",
      role: "Required",
      status: setupError ? "unknown" : (ffmpegOk ? "available" : "missing"),
      detail: ffmpegOk ? ffmpegPath : `${ffmpegPath} is not ready for assembly.`,
      impact: "Assembly and export still rely on the existing FFmpeg integration and packaging flow.",
    },
    {
      id: "models",
      label: "Models",
      role: "NVIDIA assets",
      status: setupError ? "unknown" : "optional",
      detail: "NVIDIA model configuration is available through Settings; local model assets remain managed by Models and Setup.",
      impact: edmgAvailable
        ? "Installed models can stay aligned with EDMG Core and the NVIDIA/OpenUSD handoff."
        : "Model management remains owned by the current Models page and setup flow.",
    },
  ];

  const sectionDefinitions = useMemo(
    () => [
      {
        id: "runtime" as const,
        label: "Runtime Status",
        description: "Read-only health for backend, NVIDIA Kit, NIM, service stack, FFmpeg, and model assets.",
      },
      {
        id: "recommendations" as const,
        label: "Runtime Recommendations",
        description: "Read-only guidance that ranks Forge previews against the capabilities detected right now.",
      },
      {
        id: "templates" as const,
        label: "Builder Templates",
        description: "Static registry of preview-only NVIDIA Studio panel, workflow, and model-profile concepts.",
      },
      {
        id: "recipes" as const,
        label: "Workflow Recipes",
        description: "Canonical workflow previews that stay compatible with the NVIDIA Kit Studio flow.",
      },
      {
        id: "validation" as const,
        label: "Validation Checklist",
        description: "Developer validation commands surfaced as documentation, not executable actions.",
      },
      {
        id: "bridges" as const,
        label: "Unreal Bridge Previews",
        description: "Preview-only Unreal export, handoff, and live-control targets that keep Unreal optional.",
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
  } = useStudioPageLayout<StudioForgeSectionId>(
    "studio_forge",
    sectionDefinitions.map((section) => section.id),
  );
  const sectionDefinitionById = useMemo(
    () =>
      Object.fromEntries(
        sectionDefinitions.map((definition) => [definition.id, definition]),
      ) as Record<StudioForgeSectionId, (typeof sectionDefinitions)[number]>,
    [sectionDefinitions],
  );
  const sectionControlItems = layoutState.order.map((sectionId, index) => ({
    id: sectionId,
    label: sectionDefinitionById[sectionId].label,
    description: sectionDefinitionById[sectionId].description,
    hidden: layoutState.hidden.includes(sectionId),
    canMoveUp: index > 0,
    canMoveDown: index < layoutState.order.length - 1,
  }));

  const sectionContent: Record<StudioForgeSectionId, React.ReactNode> = {
    runtime: (
      <div>
        <h2 style={{ marginBottom: 10 }}>Runtime Status</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Existing backend and NVIDIA endpoints only. Graceful read-only probes: <code>/health</code>,
          <code> /v1/config</code>, <code> /v1/setup/status</code>, <code> /v1/nvidia/diagnostics</code>, and <code> /v1/comfyui/capabilities</code>.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
          {runtimeCards.map((card) => (
            <div key={card.id} className="card">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                <div>
                  <div className="timeline-kicker">{card.role}</div>
                  <div style={{ fontWeight: 900 }}>{card.label}</div>
                </div>
                <StatusBadge status={card.status} />
              </div>
              <div className="small" style={{ marginTop: 10 }}>{card.detail}</div>
              <div className="small" style={{ marginTop: 10, opacity: 0.84 }}>{card.impact}</div>
            </div>
          ))}
        </div>
        {backendConfigError ? (
          <div className="small" style={{ marginTop: 10, opacity: 0.84 }}>
            Config read note: {backendConfigError}
          </div>
        ) : null}
        {nvidiaDiagnosticsError ? (
          <div className="small" style={{ marginTop: 10, opacity: 0.84 }}>
            NVIDIA diagnostics note: {nvidiaDiagnosticsError}
          </div>
        ) : null}
      </div>
    ),
    recommendations: (
      <div>
        <h2 style={{ marginBottom: 10 }}>Runtime Recommendations</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Read-only guidance that scores Forge templates and workflow recipes against the runtime
          capabilities detected by the existing backend and setup probes.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {recommendations.map((recommendation) => (
            <RecommendationCard key={recommendation.id} recommendation={recommendation} />
          ))}
        </div>
      </div>
    ),
    templates: (
      <div>
        <h2 style={{ marginBottom: 10 }}>Builder Templates</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Static preview registry for NVIDIA Studio page, panel, workflow, and model-profile concepts.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {STUDIO_FORGE_TEMPLATES.map((template) => (
            <TemplateCard key={template.id} template={template} />
          ))}
        </div>
      </div>
    ),
    recipes: (
      <div>
        <h2 style={{ marginBottom: 10 }}>Workflow Recipes</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Preview-only recipes that stay compatible with the NVIDIA Kit AI Director, Timeline, Render Queue, and OpenUSD output flow.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {STUDIO_FORGE_RECIPES.map((recipe) => (
            <RecipeCard key={recipe.id} recipe={recipe} />
          ))}
        </div>
      </div>
    ),
    bridges: (
      <div>
        <h2 style={{ marginBottom: 10 }}>Unreal Bridge Previews</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Secondary bridge concepts only. These previews describe export, handoff, and control shapes for external engines without
          changing the NVIDIA Kit/OpenUSD setup, packaging, or RTX render-job flow.
        </div>
        {projectId && unrealPreview ? (
          <div className="small" style={{ marginBottom: 10 }}>
            Live preview payloads are coming from the active Studio project <b>{projectId}</b> on variant{" "}
            <b>{selectedVariant}</b>. Static contract cards remain as the fallback shape when no project preview is available.
          </div>
        ) : null}
        {projectId && unrealPreviewError ? (
          <div className="small" style={{ marginBottom: 10, opacity: 0.84 }}>
            Live preview unavailable for <b>{projectId}</b>: {unrealPreviewError}
          </div>
        ) : null}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {STUDIO_FORGE_BRIDGES.map((bridge) => (
            <BridgeCard
              key={bridge.id}
              bridge={bridge}
              previewPayload={liveBridgePreviewById[bridge.id]}
            />
          ))}
        </div>
      </div>
    ),
    validation: (
      <div className="card">
        <h2 style={{ marginBottom: 10 }}>Validation Checklist</h2>
        <div className="small" style={{ marginBottom: 10 }}>
          Developer validation commands only. Studio Forge v1 does not execute shell commands from the frontend.
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          {VALIDATION_COMMANDS.map((command) => (
            <div key={command} className="small">
              <code>{command}</code>
            </div>
          ))}
        </div>
      </div>
    ),
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="card">
        <div className="timeline-kicker">Experimental Workbench</div>
        <h1>Studio Forge</h1>
        <div className="small" style={{ marginTop: 8, maxWidth: 900 }}>
          Experimental NVIDIA builder workbench in the legacy React client. Read-only preview mode. This surface inspects
          Kit/runtime state and previews future builder templates without writing files, changing setup,
          pulling models, launching renders, or mutating project data.
        </div>
        {onNavigate ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            <button className="secondary" onClick={() => onNavigate("setup")}>Open Setup</button>
            <button className="secondary" onClick={() => onNavigate("models")}>Open Models</button>
            <button className="secondary" onClick={() => onNavigate("render")}>Open Render</button>
          </div>
        ) : null}
      </div>

      <div style={{ display: "grid", gap: 14 }}>
        <StudioLayoutCustomizer
          title="Studio Forge layout"
          description="Reorder or hide preview panels for your own working style. This only changes the local page layout."
          items={sectionControlItems}
          profileOptions={profileOptions}
          activeProfile={activeProfile}
          onSelectProfile={setActiveProfile}
          onMove={movePanel}
          onToggleHidden={updateHidden}
          onReset={resetLayout}
        />
        {visibleOrder.map((sectionId) => (
          <React.Fragment key={sectionId}>{sectionContent[sectionId]}</React.Fragment>
        ))}
      </div>
    </div>
  );
}
