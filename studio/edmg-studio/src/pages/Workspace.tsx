import React, { useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiUpload, getBackendUrl } from "../components/api";
import { CreativeDirectionPanel } from "../components/CreativeDirectionPanel";
import { hasProjectId, resolveProjectId } from "../components/projectSelection";
import { ProgressBar } from "../components/ProgressBar";
import { useOperationProgress } from "../components/useOperationProgress";
import { useStudioSession } from "../components/studioSession";
import { useUiMode } from "../components/uiMode";
import type { PageProps } from "../types/pageProps";
import AiNlpWorkbench from "../workbenches/AiNlpWorkbench";
import AudioReactiveWorkbench from "../workbenches/AudioReactiveWorkbench";

type WorkspaceView = "overview" | "planner" | "reactive" | "storyboard";
type OverviewSectionId = "project" | "audio" | "references" | "plan" | "handoff";

const WORKSPACE_MIN_ZOOM = 4;
const WORKSPACE_MAX_ZOOM = 240;

const DEFAULT_OVERVIEW_SECTIONS: Record<OverviewSectionId, boolean> = {
  project: true,
  audio: true,
  references: false,
  plan: true,
  handoff: true,
};

function clampZoom(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));
}

function bytes(n: number) {
  if (!Number.isFinite(n)) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let u = 0, v = n;
  while (v > 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v.toFixed(u === 0 ? 0 : 2)} ${units[u]}`;
}

const ANALYSIS_FALLBACK_PREFIXES = [
  "no speech detected after vad",
  "no transcript is available",
  "transcription failed",
  "transcription unavailable",
  "audio-only analysis",
  "transcription not enabled",
  "transcription not available",
  "transcribe failed",
];

const NO_TRANSCRIPT_SUMMARY =
  "No transcript is available for this track yet. Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.";
const NO_SPEECH_AFTER_VAD_SUMMARY =
  "No speech detected after VAD. Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.";
const TRANSCRIPTION_FAILED_SUMMARY =
  "Transcription failed. Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.";

function looksLikeFallbackTranscript(text: string) {
  const lowered = String(text || "").trim().toLowerCase();
  return ANALYSIS_FALLBACK_PREFIXES.some((prefix) => lowered.startsWith(prefix));
}

function analysisTranscriptText(analysis: any) {
  const raw = analysis?.transcript;
  if (typeof raw === "string") return looksLikeFallbackTranscript(raw) ? "" : raw.trim();
  if (raw && typeof raw === "object") {
    const direct = String(raw.text || "").trim();
    if (direct && !looksLikeFallbackTranscript(direct)) return direct;
    const segments = Array.isArray(raw.segments) ? raw.segments : [];
    return segments
      .map((segment) => String(segment?.text || "").trim())
      .filter(Boolean)
      .join(" ")
      .trim();
  }
  return "";
}

function analysisNoTranscriptStatusText(analysis: any) {
  const note = String(analysis?.transcript?.note || "").trim();
  if (note) {
    return note.toLowerCase().startsWith("no speech detected after vad")
      ? NO_SPEECH_AFTER_VAD_SUMMARY
      : `${note}${/[.!?]$/.test(note) ? "" : "."} Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.`;
  }
  const durationAfterVad = Number(analysis?.transcript?.duration_after_vad_s || 0);
  if (Number.isFinite(durationAfterVad) && durationAfterVad <= 0 && analysis?.transcript?.source === "faster_whisper") {
    return NO_SPEECH_AFTER_VAD_SUMMARY;
  }
  if (analysis?.transcript?.error) return TRANSCRIPTION_FAILED_SUMMARY;
  return NO_TRANSCRIPT_SUMMARY;
}

function analysisSummaryText(analysis: any) {
  const summary = String(analysis?.summary || "").trim();
  const transcript = analysisTranscriptText(analysis);
  if (summary && transcript) return summary;
  if (transcript) return transcript.split(/(?<=[.!?])\s+/).find(Boolean) || transcript;
  if (analysis) {
    return analysisNoTranscriptStatusText(analysis);
  }
  return "Run analysis to generate transcript, audio sections, and the shared creative-direction base.";
}

function sceneDurationSeconds(scene: any, fallback = 5) {
  const start = Number(scene?.start_s ?? 0);
  const end = Number(scene?.end_s ?? start + fallback);
  return Math.max(0.2, end - start || fallback);
}

function resequenceStoryboardScenes(scenes: any[]) {
  let cursor = 0;
  return scenes.map((scene, index) => {
    const duration = sceneDurationSeconds(scene);
    const nextScene = {
      ...scene,
      name: scene?.name || `Scene ${index + 1}`,
      start_s: Number(cursor.toFixed(2)),
      end_s: Number((cursor + duration).toFixed(2)),
    };
    cursor += duration;
    return nextScene;
  });
}

function moveStoryboardItem<T>(items: T[], fromIndex: number, toIndex: number) {
  const next = [...items];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

function shuffleStoryboardItems<T>(items: T[]) {
  const next = [...items];
  for (let index = next.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [next[index], next[swapIndex]] = [next[swapIndex], next[index]];
  }
  return next;
}

function OverviewSection(props: {
  id: OverviewSectionId;
  title: string;
  description: string;
  progress: number;
  open: boolean;
  onToggle: (id: OverviewSectionId, open: boolean) => void;
  children: React.ReactNode;
}) {
  const { id, title, description, progress, open, onToggle, children } = props;

  return (
    <details
      className={`workspace-accordionSection${open ? " is-open" : ""}`}
      open={open}
      onToggle={(event) => onToggle(id, (event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="workspace-accordionSummary">
        <div className="workspace-accordionHead">
          <div>
            <div className="workspace-sectionTitle">{title}</div>
            <div className="small">{description}</div>
          </div>
          <div className="workspace-accordionMeta">
            <span className="badge">{Math.round(progress)}%</span>
            <ProgressBar value={progress} compact />
          </div>
        </div>
      </summary>
      <div className="workspace-accordionBody">{children}</div>
    </details>
  );
}

export default function Workspace({ onNavigate, backendUrl: backendUrlProp }: PageProps) {
  const { mode: uiMode } = useUiMode();
  const {
    projectId: sessionProjectId,
    setProjectId,
    selectedVariant,
    setSelectedVariant,
    lastHandoff,
    noteHandoff,
  } = useStudioSession();
  const backendUrl = backendUrlProp || getBackendUrl();
  const [projects, setProjects] = useState<any[]>([]);
  const [project, setProject] = useState<any>(null);
  const [projectsReady, setProjectsReady] = useState(false);

  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [refFile, setRefFile] = useState<File | null>(null);
  const [assets, setAssets] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [nvidiaScenePlan, setNvidiaScenePlan] = useState<any>(null);

  const [planMode, setPlanMode] = useState<"auto" | "ai" | "local">("auto");

  const [timelineZoom, setTimelineZoom] = useState<number>(60); // px per second
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>("overview");
  const [overviewSections, setOverviewSections] =
    useState<Record<OverviewSectionId, boolean>>(DEFAULT_OVERVIEW_SECTIONS);

  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const timelineScrollerRef = useRef<HTMLDivElement | null>(null);
  const previewAutoFitKeyRef = useRef<string>("");
  const { progress, runOperation } = useOperationProgress();
  const projectId = projectsReady && hasProjectId(projects, sessionProjectId) ? sessionProjectId : "";

  const refreshProjects = async () => {
    const d = await apiGet("/v1/projects");
    const ps = Array.isArray(d?.projects) ? d.projects : [];
    setProjects(ps);
    setProjectsReady(true);
    const nextProjectId = resolveProjectId(ps, sessionProjectId);
    if (nextProjectId !== sessionProjectId) setProjectId(nextProjectId);
    if (!nextProjectId) {
      setProject(null);
      setAssets(null);
      setAnalysis(null);
      setPlan(null);
      setNvidiaScenePlan(null);
    }
  };

  const refreshProject = async (id: string) => {
    if (!id) return;
    const d = await apiGet(`/v1/projects/${id}`);
    setProject(d.project);
    setAnalysis(d.project?.meta?.analysis || null);
    setPlan(d.project?.meta?.last_plan || null);
    const variantCount = Array.isArray(d.project?.meta?.last_plan?.variants)
      ? d.project.meta.last_plan.variants.length
      : 0;
    if (variantCount > 0 && selectedVariant > variantCount - 1) setSelectedVariant(0);
    try {
      const a = await apiGet(`/v1/projects/${id}/assets`);
      setAssets(a.assets);
    } catch {
      setAssets(null);
    }
    try {
      const nvidiaPlan = await apiGet(`/v1/projects/${encodeURIComponent(id)}/nvidia/scene-plan`);
      setNvidiaScenePlan(nvidiaPlan);
    } catch {
      const saved = d.project?.meta?.last_nvidia_scene_plan || null;
      setNvidiaScenePlan(saved ? { ok: true, project_id: id, ...saved } : null);
    }
  };

  useEffect(() => { refreshProjects().catch(() => {}); }, []);

  useEffect(() => {
    if (!projectsReady) return;
    if (projectId) refreshProject(projectId).catch(() => {});
    else {
      setProject(null);
      setAssets(null);
      setAnalysis(null);
      setPlan(null);
      setNvidiaScenePlan(null);
    }
  }, [projectId, projectsReady]);
  // Workspace stays focused on project + timeline. Rendering lives in the Render page.

  const uploadAudio = async () => {
    if (!audioFile) return;
    setErr(null); setInfo(null);
    try {
      await runOperation(
        {
          label: "Uploading audio",
          detail: audioFile.name,
          successDetail: "Track uploaded and project refreshed.",
        },
        async () => {
          await apiUpload(`/v1/projects/${projectId}/assets/audio`, audioFile);
          await refreshProject(projectId);
        },
      );
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const uploadRef = async () => {
    if (!refFile) return;
    setErr(null); setInfo(null);
    try {
      await runOperation(
        {
          label: "Uploading reference",
          detail: refFile.name,
          successDetail: "Reference image saved to the current project.",
        },
        async () => {
          await apiUpload(`/v1/projects/${projectId}/assets/refs`, refFile);
          setRefFile(null);
          await refreshProject(projectId);
        },
      );
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const runAnalysis = async () => {
    setErr(null); setInfo(null);
    try {
      const d = await runOperation(
        {
          label: "Analyzing audio",
          detail: audioFile ? `Uploading ${audioFile.name}, then running beat detection, transcription, and feature extraction.` : "Beat detection, transcription, and feature extraction.",
          successDetail: "Analysis complete.",
        },
        async () => {
          if (audioFile) {
            await apiUpload(`/v1/projects/${projectId}/assets/audio`, audioFile);
          }
          return apiPost(`/v1/projects/${projectId}/analyze_audio`, {});
        },
      );
      setAnalysis(d?.analysis || d?.project?.meta?.analysis || null);
      await refreshProject(projectId);
    } catch (e: any) { setErr(String(e)); }
  };

  const generatePlan = async () => {
    setErr(null); setInfo(null);
    try {
      const d = await runOperation(
        {
          label: "Generating plan variants",
          detail: `Mode: ${planMode}`,
          successDetail: "Plan variants refreshed for the active project.",
        },
        () =>
          apiPost(`/v1/projects/${projectId}/plan?mode=${planMode}`, {
            title: project?.name || "Untitled",
            style_prefs: "cinematic, coherent subject, high detail, consistent style",
            num_variants: 3,
            max_scenes: 12
          }),
      );
      setPlan(d);
      setSelectedVariant(0);
      await refreshProject(projectId);
    } catch (e: any) { setErr(String(e)); }
  };

  const generateNvidiaScenePlan = async () => {
    if (!projectId) return;
    setErr(null); setInfo(null);
    try {
      const sceneCount = variantScenes.length || analysisSections.length || 6;
      const d = await runOperation(
        {
          label: "Generating NVIDIA scene plan",
          detail: "NIM/OpenAI-compatible planner to USD scene-plan metadata.",
          successDetail: "NVIDIA scene plan saved to the active project.",
        },
        () =>
          apiPost(`/v1/projects/${encodeURIComponent(projectId)}/nvidia/scene-plan`, {
            title: project?.name || "EDMG NVIDIA Scene Plan",
            duration_s: durationS || 60,
            bpm: analysisBpm || undefined,
            style_prefs: "RTX stage lighting, OpenUSD camera moves, audio-reactive motion",
            user_notes: variantScenes[0]?.prompt || analysisSummary,
            max_scenes: Math.max(1, Math.min(12, sceneCount)),
            num_variants: 1,
          }),
      );
      setNvidiaScenePlan(d);
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const exportNvidiaUsda = async () => {
    if (!projectId || !nvidiaScenePlan?.usd_stage?.text) return;
    setErr(null); setInfo(null);
    try {
      const d = await runOperation(
        {
          label: "Exporting NVIDIA USDA",
          detail: "Writing the saved NVIDIA scene plan into project outputs.",
          successDetail: "NVIDIA USDA exported to project outputs.",
        },
        () => apiPost(`/v1/projects/${encodeURIComponent(projectId)}/nvidia/scene-plan/export`, {}),
      );
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const buildNvidiaRenderJob = async () => {
    if (!projectId || !nvidiaScenePlan?.scene_plan) return;
    setErr(null); setInfo(null);
    try {
      const d = await runOperation(
        {
          label: "Building NVIDIA RTX render job",
          detail: "Writing an Omniverse Kit RTX worker handoff from the saved scene plan.",
          successDetail: "NVIDIA RTX render job exported to project outputs.",
        },
        () => apiPost(`/v1/projects/${encodeURIComponent(projectId)}/nvidia/render-job`, { fps: 24, width: 1920, height: 1080 }),
      );
      setInfo(d);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const fileUrl = (pid: string, rel: string) => `${backendUrl}/v1/projects/${pid}/file?path=${encodeURIComponent(rel)}`;

  const variantScenes = plan?.variants?.[selectedVariant]?.scenes || [];
  const analysisFeatures = analysis?.features || {};
  const transcriptText = analysisTranscriptText(analysis);
  const transcriptReady = Boolean(transcriptText);
  const analysisSummary = analysisSummaryText(analysis);
  const analysisSections = Array.isArray(analysis?.sections) ? analysis.sections : [];
  const analysisTags = Array.isArray(analysis?.tags) ? analysis.tags.map((tag: any) => String(tag || "").trim()).filter(Boolean) : [];
  const analysisBpm = Number(analysisFeatures?.bpm || analysisFeatures?.tempo_bpm || analysisFeatures?.tempo || 0);
  const durationS = analysis?.features?.duration_s || analysis?.features?.duration || plan?.duration_s || 0;
  const refAssets = Array.isArray(assets?.refs) ? assets.refs : [];
  const variantCount = Array.isArray(plan?.variants) ? plan.variants.length : 0;
  const selectedVariantName =
    plan?.variants?.[selectedVariant]?.name || (variantCount ? `Variant ${selectedVariant + 1}` : "none");
  const analysisReady = Boolean(analysis);
  const audioReady = Boolean(project?.meta?.audio);
  const storyboardReady = Boolean(variantScenes.length);
  const plannerImportedAt = Number(project?.meta?.last_planner_lab?.imported_at || 0);
  const reactiveAppliedAt = Number(project?.meta?.last_reactive_lab?.applied_at || 0);
  const plannerSceneCount = Number(project?.meta?.last_plan?.variants?.[selectedVariant]?.scenes?.length || 0);
  const reactiveSectionCount = Number(project?.meta?.last_reactive_lab?.sections?.length || 0);
  const projectProgress = projectId ? 100 : 0;
  const audioProgress = analysisReady ? 100 : audioReady ? 58 : 0;
  const referenceProgress = refAssets.length ? Math.min(100, 30 + refAssets.length * 22) : 0;
  const planProgress = storyboardReady ? 100 : variantCount ? 70 : 0;
  const handoffProgress = reactiveAppliedAt ? 100 : plannerImportedAt ? 62 : 0;
  const nvidiaSceneCount = Number(nvidiaScenePlan?.usd_metadata?.["edmg:sceneCount"] || nvidiaScenePlan?.scene_plan?.scenes?.length || 0);
  const nvidiaSavedAt = String(nvidiaScenePlan?.saved_at || "");
  const nvidiaProvider = String(nvidiaScenePlan?.scene_plan?.provider || nvidiaScenePlan?.planner?.provider || "");
  const analysisActionLabel = audioFile ? "Upload + Analyze" : "Analyze + Transcribe";
  const transcriptStatusLabel = transcriptReady
    ? "Transcript ready"
    : analysisReady
      ? String(analysis?.transcript?.note || "").trim().toLowerCase().startsWith("no speech detected after vad")
        || (Number(analysis?.transcript?.duration_after_vad_s || 0) <= 0 && analysis?.transcript?.source === "faster_whisper")
        ? "No speech after VAD"
        : "Audio-only analysis"
      : "Waiting";

  const toggleOverviewSection = (sectionId: OverviewSectionId, isOpen: boolean) => {
    setOverviewSections((current) => ({ ...current, [sectionId]: isOpen }));
  };

  const applyTimelinePlan = async (overwrite: boolean) => {
    if (!projectId || !plan?.variants?.length) return;
    setErr(null);
    try {
      await runOperation(
        {
          label: overwrite ? "Overwriting timeline" : "Applying plan to timeline",
          detail: `Variant ${selectedVariant + 1}`,
          successDetail: "Timeline updated from the selected storyboard variant.",
        },
        () =>
          apiPost(`/v1/projects/${projectId}/timeline/apply_plan`, {
            variant_index: selectedVariant,
            overwrite,
          }),
      );
      await refreshProject(projectId);
      setWorkspaceView("storyboard");
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const updateStoryboardScenes = async (nextScenes: any[], detail: string) => {
    if (!projectId || !plan?.variants?.length) return;
    setErr(null);
    const resequencedScenes = resequenceStoryboardScenes(nextScenes);
    try {
      const result = await runOperation(
        {
          label: "Saving storyboard order",
          detail,
          successDetail: "Storyboard order saved to the current project variant.",
        },
        () =>
          apiPost(`/v1/projects/${projectId}/plan/variant`, {
            variant_index: selectedVariant,
            scenes: resequencedScenes,
          }),
      );
      if (result?.plan) setPlan(result.plan);
      await refreshProject(projectId);
    } catch (e: any) {
      setErr(String(e));
    }
  };

  const moveStoryboardScene = async (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (!variantScenes.length || nextIndex < 0 || nextIndex >= variantScenes.length) return;
    await updateStoryboardScenes(
      moveStoryboardItem(variantScenes, index, nextIndex),
      `Scene ${index + 1} moved ${direction < 0 ? "earlier" : "later"}.`,
    );
  };

  const shuffleStoryboardScenes = async () => {
    if (variantScenes.length < 2) return;
    await updateStoryboardScenes(shuffleStoryboardItems(variantScenes), "Reordered prompt beats for a different scene flow.");
  };

  const syncPlannerLab = async (payload: any) => {
    if (!projectId) throw new Error("Select a Studio project before syncing the planner into the renderer.");
    await runOperation(
      {
        label: "Syncing planner handoff",
        detail: "Importing planner scenes and renderer prompts.",
        successDetail: "Planner handoff applied to the current session.",
      },
      async () => {
        await apiPost(`/v1/projects/${projectId}/planner_lab/import`, payload);
        await refreshProject(projectId);
      },
    );
    noteHandoff({
      type: "planner",
      projectId,
      at: Date.now(),
      summary: `${project?.name || "Selected project"} planner scenes and prompt tracks synced.`,
    });
    setWorkspaceView("storyboard");
    return `${project?.name || "Selected project"} now has synced planner analysis, canonical storyboard scenes, and renderer prompt/motion tracks.`;
  };

  const syncReactiveLab = async (payload: any) => {
    if (!projectId) throw new Error("Select a Studio project before applying reactive motion to the renderer.");
    await runOperation(
      {
        label: "Syncing reactive handoff",
        detail: "Applying motion schedules and camera keyframes.",
        successDetail: "Reactive handoff applied to the timeline.",
      },
      async () => {
        await apiPost(`/v1/projects/${projectId}/reactive_lab/apply`, payload);
        await refreshProject(projectId);
      },
    );
    noteHandoff({
      type: "reactive",
      projectId,
      at: Date.now(),
      summary: `${project?.name || "Selected project"} reactive motion track and camera data synced.`,
    });
    return `${project?.name || "Selected project"} now has the reactive motion track and camera data wired into the internal renderer timeline.`;
  };

  const setTimelineZoomWithFocus = (nextZoom: number, focusSeconds?: number) => {
    const scroller = timelineScrollerRef.current;
    const clamped = clampZoom(nextZoom, WORKSPACE_MIN_ZOOM, WORKSPACE_MAX_ZOOM);
    if (!scroller) {
      setTimelineZoom(clamped);
      return;
    }

    const currentZoom = Math.max(1, timelineZoom);
    const rect = scroller.getBoundingClientRect();
    const fallbackFocus = (scroller.scrollLeft + rect.width / 2) / currentZoom;
    const focus = Math.max(0, focusSeconds ?? fallbackFocus);
    setTimelineZoom(clamped);
    requestAnimationFrame(() => {
      const nextLeft = Math.max(0, focus * clamped - rect.width / 2);
      if (typeof scroller.scrollTo === "function") {
        scroller.scrollTo({
          left: nextLeft,
          behavior: "smooth",
        });
      } else {
        scroller.scrollLeft = nextLeft;
      }
    });
  };

  const fitTimelinePreview = () => {
    const maxDur = Math.max(
      Number(durationS) || 0,
      Number(variantScenes[variantScenes.length - 1]?.end_s ?? 0),
      30,
    );
    const viewport = timelineScrollerRef.current?.clientWidth ?? 920;
    const nextZoom = (Math.max(280, viewport) - 48) / Math.max(1, maxDur);
    setTimelineZoomWithFocus(nextZoom, 0);
  };

  const onTimelinePreviewWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    const scroller = timelineScrollerRef.current;
    if (!scroller) return;
    event.preventDefault();
    const rect = scroller.getBoundingClientRect();
    const focusSeconds =
      (scroller.scrollLeft + (event.clientX - rect.left)) / Math.max(1, timelineZoom);
    const nextZoom = event.deltaY < 0 ? timelineZoom * 1.12 : timelineZoom / 1.12;
    setTimelineZoomWithFocus(nextZoom, focusSeconds);
  };

  useEffect(() => {
    const maxDur = Math.max(
      Number(durationS) || 0,
      Number(variantScenes[variantScenes.length - 1]?.end_s ?? 0),
      30,
    );
    if (!projectId || !maxDur) return;
    const key = `${projectId}:${selectedVariant}:${variantScenes.length}:${maxDur.toFixed(2)}`;
    if (previewAutoFitKeyRef.current === key) return;
    previewAutoFitKeyRef.current = key;
    const useRaf = typeof window.requestAnimationFrame === "function";
    const handle = useRaf
      ? window.requestAnimationFrame(() => fitTimelinePreview())
      : window.setTimeout(() => fitTimelinePreview(), 0);
    return () => {
      if (useRaf) window.cancelAnimationFrame(handle);
      else window.clearTimeout(handle);
    };
  }, [projectId, selectedVariant, variantScenes, durationS]);

  const TimelinePreview = ({ detailed = false }: { detailed?: boolean }) => {
    if (!variantScenes.length) return <div className="small">No scenes. Generate a plan to see a timeline.</div>;
    const lastEnd = Number(variantScenes[variantScenes.length - 1]?.end_s ?? 60);
    const maxDur = Math.max(Number(durationS) || 0, lastEnd);
    const widthPx = Math.max(600, Math.round(maxDur * timelineZoom));
    const tickEvery = 5;
    const ticks: number[] = [];
    const maxT = Math.ceil(maxDur / tickEvery) * tickEvery;
    for (let t = 0; t <= maxT; t += tickEvery) ticks.push(t);
    return (
      <div className="workspace-timelinePane">
        <div className="workspace-timelineToolbar">
          <div className="workspace-timelineZoomGroup">
            <div className="small">Zoom</div>
            <div className="workspace-zoomButtons">
              <button className="secondary" type="button" onClick={() => setTimelineZoomWithFocus(timelineZoom / 1.18)}>
                -
              </button>
              <button className="secondary" type="button" onClick={() => setTimelineZoomWithFocus(timelineZoom * 1.18)}>
                +
              </button>
              <button className="secondary" type="button" onClick={fitTimelinePreview}>
                Fit all
              </button>
            </div>
          </div>
          <input
            className="workspace-timelineRange"
            type="range"
            min={WORKSPACE_MIN_ZOOM}
            max={WORKSPACE_MAX_ZOOM}
            value={timelineZoom}
            onChange={(e) => setTimelineZoomWithFocus(Number(e.target.value))}
          />
          <div className="small workspace-zoomReadout">{Math.round(timelineZoom)}px/s</div>
        </div>
        <div
          className="workspace-timelineScroller"
          ref={timelineScrollerRef}
          onWheel={onTimelinePreviewWheel}
        >
          <div className="workspace-timelineCanvas" style={{ width: widthPx }}>
            <div className="workspace-timelineRuler">
              {ticks.map((t) => (
                <div key={t} className="workspace-timelineTick" style={{ left: t * timelineZoom }}>
                  <div className="workspace-timelineTickLine" />
                  <div className="small workspace-timelineTickLabel">{t}s</div>
                </div>
              ))}
            </div>
            <div className="workspace-sceneStage">
              {variantScenes.map((sc: any, i: number) => {
                const s = Number(sc.start_s ?? (i * 5));
                const e = Number(sc.end_s ?? (s + 5));
                const left = Math.max(0, s * timelineZoom);
                const w = Math.max(10, (e - s) * timelineZoom);
                return (
                  <div
                    key={i}
                    title={sc.prompt}
                    className="workspace-sceneBar"
                    style={{
                      position: "absolute",
                      left,
                      top: 20 + (i % 4) * 24,
                      width: w,
                    }}
                  >
                    {i + 1}. {String(sc.name || "Scene")}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div className="small workspace-timelineHint">
          Use `Ctrl/Cmd + mouse wheel` or the zoom buttons to inspect pacing without losing your place.
        </div>
        <div className="workspace-sceneList">
          <div className="workspace-sectionTitle">{detailed ? "Storyboard scenes" : "Scene list"}</div>
          {variantScenes.map((sc: any, i: number) => (
            <div key={i} className="workspace-sceneRow">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div style={{ fontWeight: 700 }}>{i + 1}. {sc.name || "Scene"}</div>
                <div className="small">{Number(sc.start_s ?? i * 5).toFixed(2)}s → {Number(sc.end_s ?? (i * 5 + 5)).toFixed(2)}s</div>
              </div>
              <div className="small" style={{ marginTop: 6 }}>{sc.prompt}</div>
              {detailed && sc.transition ? (
                <div className="small workspace-sceneMeta">Transition: {String(sc.transition)}</div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const workflowTabs: Array<{ id: WorkspaceView; label: string; meta: string }> = [
    { id: "overview", label: "Overview", meta: `${variantCount || 0} variants` },
    { id: "planner", label: "AI Planner", meta: analysisReady ? "optional story pass" : "analyze first" },
    { id: "reactive", label: "Reactive Lab", meta: analysisReady ? "optional motion pass" : "analyze first" },
    { id: "storyboard", label: "Storyboard", meta: storyboardReady ? `${variantScenes.length} scenes` : "sync or plan first" },
  ];

  return (
    <div className="workspace-page">
      <div className="workspace-header">
        <div>
          <div className="timeline-kicker">Studio Session</div>
          <h1>Workspace</h1>
          <div className="small workspace-headerCopy">
            Ingest, analyze, shape creative direction, and hand the session off to Timeline and Render.
          </div>
        </div>
        <div className="workspace-statusStrip">
          <div className="workspace-stat">
            <span className="small">Project</span>
            <strong>{project?.name || "none"}</strong>
          </div>
          <div className="workspace-stat">
            <span className="small">Audio</span>
            <strong>{audioReady ? "ready" : "missing"}</strong>
          </div>
          <div className="workspace-stat">
            <span className="small">Analysis</span>
            <strong>{analysisReady ? "ready" : "pending"}</strong>
          </div>
          <div className="workspace-stat">
            <span className="small">Variant</span>
            <strong>{selectedVariantName}</strong>
          </div>
        </div>
      </div>

      <div className="workspace-flowTabs" role="tablist" aria-label="Workspace workflow">
        {workflowTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={workspaceView === tab.id}
            className={`workspace-flowTab${workspaceView === tab.id ? " is-active" : ""}`}
            onClick={() => setWorkspaceView(tab.id)}
          >
            <span>{tab.label}</span>
            <span className="workspace-flowTabMeta">{tab.meta}</span>
          </button>
        ))}
      </div>

      <div className="workspace-sessionStrip">
        <div className="workspace-sessionCard">
          <div className="workspace-sessionLabel">Shared session</div>
          <div className="workspace-sessionValue">
            {project?.name || "No active project"} • {selectedVariantName}
          </div>
          <div className="small">
            Overview remains the canonical source. Use the Overview to Reactive path for a fast motion-only pass, or the Overview to Planner to Reactive path when you want a richer story pass first. Timeline and Render consume the same saved session either way.
          </div>
        </div>
        {lastHandoff ? (
          <div className="workspace-sessionCard workspace-sessionCard--accent">
            <div className="workspace-sessionLabel">Last handoff</div>
            <div className="workspace-sessionValue">
              {lastHandoff.type === "planner" ? "Planner sync" : "Reactive sync"}
            </div>
            <div className="small">{lastHandoff.summary}</div>
          </div>
        ) : null}
      </div>

      {progress.label ? (
        <div className="card workspace-progressCard">
          <ProgressBar
            value={progress.value}
            label={progress.label}
            detail={progress.detail}
            tone={progress.tone}
          />
        </div>
      ) : null}

      <details className="card workspace-guideCard">
        <summary className="workspace-guideSummary">
          {uiMode === "advanced" ? "Advanced guide and workflow notes" : workspaceView === "overview" ? "Quick guide and capabilities" : workspaceView === "planner" ? "Planner guide and capabilities" : workspaceView === "storyboard" ? "Storyboard guide and capabilities" : "Reactive guide and capabilities"}
        </summary>
        <div className="workspace-guideBody">
          {workspaceView === "overview" ? (
            <div className="guide-grid">
              <section className="guide-block">
                <div className="guide-kicker">What this view does</div>
                <p>Overview is the canonical intake surface. Upload the track here, run analysis and transcription here, review the first creative direction here, and only then branch into Planner, Reactive Lab, Timeline, or Render if you need deeper control.</p>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Recommended flow</div>
                <ul className="guide-list">
                  <li>Choose the project, pick the track, and use `Analyze + Transcribe`. If a local file is selected, Workspace uploads it first and saves that result into the shared Studio session.</li>
                  <li>Stay in Overview if the base creative direction is already good enough and you do not need a separate story or motion pass.</li>
                  <li>Fast path: go straight from Overview into Reactive Lab when the base story already works and you only need camera or motion scheduling.</li>
                  <li>Deep path: go from Overview into AI Planner when you want richer scene writing or more variety, sync that back, then use Reactive Lab to add schedules on top of the refined story pass.</li>
                </ul>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">What carries forward</div>
                <ul className="guide-list">
                  <li>Audio file, transcript, energy sections, tags, and saved storyboard variant are shared with Planner and Reactive Lab automatically.</li>
                  <li>Timeline Preview lets you verify full-track pacing before going into dense editing.</li>
                  <li>Storyboard keeps the saved scene order visible even if you never open the full standalone labs.</li>
                </ul>
              </section>
              {uiMode === "advanced" ? (
                <section className="guide-block">
                  <div className="guide-kicker">How the stages build</div>
                  <ul className="guide-list">
                    <li>`Creative direction` is the first-pass scene package built from the saved Overview analysis and storyboard.</li>
                    <li>`AI Planner` extends or rewrites scene language and storyboard intent. It does not replace Overview unless you explicitly sync it back into the shared session.</li>
                    <li>`Reactive Lab` reads the current saved story pass and adds camera or motion schedules like pan, drift, zoom, rotation, and strength. It is meant to layer on top, not overwrite the story.</li>
                    <li>`Storyboard` and `Timeline` consume whichever saved pass is currently active, whether that is the original Overview direction or the later Planner-synced version.</li>
                  </ul>
                </section>
              ) : null}
              {uiMode === "advanced" ? (
                <section className="guide-block">
                  <div className="guide-kicker">Export surfaces</div>
                  <ul className="guide-list">
                    <li>`Prompt pack` is the readable scene-by-scene writing bundle you can review or paste into another generator.</li>
                    <li>`Timeline patch` is the Studio-native JSON payload that turns the direction into prompt and motion tracks.</li>
                    <li>`Deforum preview` shows how the same direction would translate into Deforum-style schedules before you render there.</li>
                    <li>`LLM contract` is the structured backend or debug payload, mainly for inspection or integrations rather than direct rendering.</li>
                  </ul>
                </section>
              ) : null}
            </div>
          ) : workspaceView === "planner" ? (
            <div className="guide-grid">
              <section className="guide-block">
                <div className="guide-kicker">What this view does</div>
                <p>The AI Planner builds on the saved Overview analysis. It is where you push story beats, scene phrasing, continuity, alternates, and repair strategy without giving up the base Overview direction.</p>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Capabilities</div>
                <ul className="guide-list">
                  <li>Hydrates the current project audio, transcript, and storyboard automatically.</li>
                  <li>Lets you lock strong scenes, regenerate weaker scenes, and keep subject/palette continuity while diversifying the shot writing.</li>
                  <li>Syncs a refined plan back into the same Studio project without losing the standalone planner workflow.</li>
                </ul>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Recommended flow</div>
                <ul className="guide-list">
                  <li>Start here only after Overview has saved the shared session analysis unless you intentionally want a fresh local-only planner pass.</li>
                  <li>Skip Planner entirely when the Overview story is already strong and you only need motion work.</li>
                  <li>Regenerate to improve story variety, then lock or approve the scenes you want to keep.</li>
                  <li>Sync back when you want the refined storyboard to become the new project base for Reactive Lab, Timeline, and Render.</li>
                </ul>
              </section>
            </div>
          ) : workspaceView === "reactive" ? (
            <div className="guide-grid">
              <section className="guide-block">
                <div className="guide-kicker">What this view does</div>
                <p>Reactive Lab uses the shared Overview analysis and saved storyboard to generate camera and motion schedules. It is for adding motion structure, not for replacing the underlying scene writing.</p>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Capabilities</div>
                <ul className="guide-list">
                  <li>Build deterministic keyframes from the saved track and inspect section energy before render.</li>
                  <li>Generate pan, depth, rotation, strength, and cue schedules from the audio pass.</li>
                  <li>Apply approved reactive motion directly into the project timeline and renderer camera data.</li>
                </ul>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Recommended flow</div>
                <ul className="guide-list">
                  <li>Run Overview analysis first, then come straight here if the base story already works and you only need motion scheduling.</li>
                  <li>Use Reactive Lab after Planner only when you want those schedules to follow a newly refined story pass instead of the original Overview direction.</li>
                  <li>Apply to the renderer timeline, then open Timeline or Render for final arrangement and output.</li>
                </ul>
              </section>
            </div>
          ) : (
            <div className="guide-grid">
              <section className="guide-block">
                <div className="guide-kicker">What this view does</div>
                <p>Storyboard is the saved project-level reading view for the active variant. It keeps the timing preview and the scene cards together so you can evaluate pacing and prompt continuity in one place.</p>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Capabilities</div>
                <ul className="guide-list">
                  <li>Switch variants without leaving Workspace.</li>
                  <li>Inspect scene prompts, notes, and durations next to the zoomable preview.</li>
                  <li>Apply or overwrite the timeline, then jump directly into full Timeline or Render.</li>
                </ul>
              </section>
              <section className="guide-block">
                <div className="guide-kicker">Recommended flow</div>
                <ul className="guide-list">
                  <li>Use Fit all to see the full sequence, then zoom into dense sections when a transition needs closer review.</li>
                  <li>Read the scene cards in order to confirm narrative continuity and prompt variety.</li>
                  <li>Apply the version you want, then open Timeline for detailed arrangement edits.</li>
                </ul>
              </section>
            </div>
          )}
        </div>
      </details>

      {workspaceView === "overview" ? <div className="workspace-shell">
        <div className="card workspace-sideCard">
          <OverviewSection
            id="project"
            title="Project"
            description="Choose the active session and inspect current ingest status."
            progress={projectProgress}
            open={overviewSections.project}
            onToggle={toggleOverviewSection}
          >
            {projects.length ? (
              <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            ) : (
              <div className="small">No projects yet. Create one in Projects tab.</div>
            )}
          </OverviewSection>

          <OverviewSection
            id="audio"
            title="Audio"
            description="Upload the track, then analyze and transcribe it."
            progress={audioProgress}
            open={overviewSections.audio}
            onToggle={toggleOverviewSection}
          >
            <input type="file" accept="audio/*" onChange={(e) => setAudioFile(e.target.files?.[0] || null)} />
            <div className="row workspace-actionRow" style={{ marginTop: 10 }}>
              <button onClick={uploadAudio} disabled={!audioFile || !projectId}>Upload</button>
              <button className="secondary" onClick={runAnalysis} disabled={!projectId}>{analysisActionLabel}</button>
            </div>
            <div className="small">
              {audioFile
                ? `Selected file: ${audioFile.name}. Running analysis will save this track into the shared Studio session first.`
                : "Analyze uses the saved project track. Pick a local file first if you want to replace the current audio and analyze it in one pass."}
            </div>
            {project?.meta?.audio && (
              <div className="small" style={{ marginTop: 10 }}>
                uploaded: {project.meta.audio.filename} ({bytes(project.meta.audio.size_bytes)})
              </div>
            )}
            <div className="workspace-analysisGrid">
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Analysis status</div>
                <strong>{transcriptStatusLabel}</strong>
              </div>
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Duration</div>
                <strong>{durationS ? `${durationS.toFixed(1)}s` : "pending"}</strong>
              </div>
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Tempo</div>
                <strong>{analysisBpm ? `${Math.round(analysisBpm)} BPM` : "pending"}</strong>
              </div>
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Sections</div>
                <strong>{analysisSections.length || 0}</strong>
              </div>
            </div>
            <details className="workspace-inlineDetails" open={analysisReady}>
              <summary>Analysis summary</summary>
              <div className="workspace-scrollPanel">
                <p>{analysisSummary}</p>
                {analysisTags.length ? (
                  <div className="workspace-chipRow">
                    {analysisTags.slice(0, 10).map((tag) => (
                      <span key={tag} className="badge">{tag}</span>
                    ))}
                  </div>
                ) : null}
              </div>
            </details>
            <details className="workspace-inlineDetails" open={uiMode === "advanced" && transcriptReady}>
              <summary>{transcriptReady ? "Transcript" : "Transcript status"}</summary>
              <div className="workspace-scrollPanel">
                {transcriptReady ? (
                  <p>{transcriptText}</p>
                ) : (
                  <p>{analysisSummary}</p>
                )}
              </div>
            </details>
          </OverviewSection>

          <OverviewSection
            id="references"
            title="Reference Assets"
            description="Style and character anchors that guide image and motion prompts."
            progress={referenceProgress}
            open={overviewSections.references}
            onToggle={toggleOverviewSection}
          >
            <div className="small">Reference images (style/character anchors)</div>
            <input type="file" accept="image/*" onChange={(e) => setRefFile(e.target.files?.[0] || null)} />
            <div className="row workspace-actionRow" style={{ marginTop: 10 }}>
              <button onClick={uploadRef} disabled={!refFile || !projectId}>Upload ref</button>
              <button className="secondary" onClick={() => projectId && refreshProject(projectId)} disabled={!projectId}>Refresh assets</button>
            </div>
            <div className="workspace-assetsGrid">
              {refAssets.map((r: any) => (
                <a key={r.path} href={fileUrl(projectId, r.path)} target="_blank" rel="noreferrer">
                  <img src={fileUrl(projectId, r.path)} className="workspace-assetThumb" />
                </a>
              ))}
              {!refAssets.length && <div className="small">No refs yet.</div>}
            </div>
          </OverviewSection>

          <OverviewSection
            id="plan"
            title="Plan Variants"
            description="Generate multiple scene structures, then apply the best one to the timeline."
            progress={planProgress}
            open={overviewSections.plan}
            onToggle={toggleOverviewSection}
          >
            <div className="row workspace-actionRow" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <label className="small row" style={{ gap: 6, alignItems: "center" }}>
                Plan mode
                <select value={planMode} onChange={(e) => setPlanMode(e.target.value as any)}>
                  <option value="auto">Auto</option>
                  <option value="ai">AI-only</option>
                  <option value="local">Local-only</option>
                </select>
              </label>
              <button onClick={generatePlan} disabled={!projectId}>Generate Plan Variants</button>
            </div>

            {plan?.variants?.length ? (
              <>
              <div style={{ marginTop: 12 }}>
                <div className="small">Select variant</div>
                <select value={selectedVariant} onChange={(e) => setSelectedVariant(Number(e.target.value))}>
                  {plan.variants.map((v: any, idx: number) => (
                    <option key={idx} value={idx}>{idx + 1}. {v.name}</option>
                  ))}
                </select>
              </div>
              <div className="row workspace-actionRow" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                <button
                  onClick={() => void applyTimelinePlan(false)}
                  disabled={!projectId || !plan?.variants?.length}
                >
                  Apply variant to timeline
                </button>
                <button
                  className="secondary"
                  onClick={() => void applyTimelinePlan(true)}
                  disabled={!projectId || !plan?.variants?.length}
                >
                  Apply (overwrite)
                </button>
                <button className="secondary" onClick={() => setWorkspaceView("planner")} disabled={!projectId}>
                  Open AI Planner
                </button>
                <button className="secondary" onClick={() => setWorkspaceView("reactive")} disabled={!projectId || !analysisReady}>
                  Open Reactive Lab
                </button>
              </div>
              </>
            ) : (
              <div className="small" style={{ marginTop: 10 }}>No plan generated yet.</div>
            )}
          </OverviewSection>

          <OverviewSection
            id="handoff"
            title="Handoff"
            description="Move from planning and reactive motion into arrangement, rendering, and output review."
            progress={handoffProgress}
            open={overviewSections.handoff}
            onToggle={toggleOverviewSection}
          >
            <div className="small" style={{ marginBottom: 10 }}>
              Workspace is the integrated hub. Standalone Planner Lab and Reactive Lab remain available from the sidebar when you want full-screen specialist views.
            </div>
            <div className="workspace-handoffGrid">
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Planner handoff</div>
                <strong>{plannerImportedAt ? `${plannerSceneCount} scenes synced` : "Not synced yet"}</strong>
              </div>
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">Reactive handoff</div>
                <strong>{reactiveAppliedAt ? `${reactiveSectionCount} sections applied` : "Not synced yet"}</strong>
              </div>
              <div className="workspace-handoffCard">
                <div className="workspace-handoffLabel">NVIDIA scene plan</div>
                <strong>{nvidiaSceneCount ? `${nvidiaSceneCount} USD scenes` : "Not generated yet"}</strong>
              </div>
            </div>
            <div className="workspace-inlineDetails" style={{ marginTop: 12, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontWeight: 800 }}>NVIDIA / OpenUSD handoff</div>
                  <div className="small">
                    {nvidiaSceneCount
                      ? `${nvidiaProvider || "NVIDIA profile"}${nvidiaSavedAt ? ` • saved ${nvidiaSavedAt}` : ""}`
                      : "Generate a project-scoped scene plan for Kit and OpenUSD workflows."}
                  </div>
                </div>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <button className="secondary" onClick={() => void generateNvidiaScenePlan()} disabled={!projectId}>
                    Generate NVIDIA plan
                  </button>
                  <button className="secondary" onClick={() => void exportNvidiaUsda()} disabled={!nvidiaScenePlan?.usd_stage?.text}>
                    Export USDA
                  </button>
                  <button className="secondary" onClick={() => void buildNvidiaRenderJob()} disabled={!nvidiaScenePlan?.scene_plan}>
                    Build RTX job
                  </button>
                </div>
              </div>
            </div>
            <div className="row workspace-actionRow" style={{ gap: 10, flexWrap: "wrap" }}>
              <button className="secondary" onClick={() => setWorkspaceView("planner")} disabled={!projectId}>Planner</button>
              <button className="secondary" onClick={() => setWorkspaceView("reactive")} disabled={!projectId || !analysisReady}>Reactive Lab</button>
              <button onClick={() => onNavigate?.("render")} disabled={!plan?.variants?.length}>Go to Render</button>
              <button className="secondary" onClick={() => setWorkspaceView("storyboard")} disabled={!storyboardReady}>Open Storyboard</button>
              <button className="secondary" onClick={() => onNavigate?.("outputs")}>Outputs</button>
              <button className="secondary" onClick={() => onNavigate?.("queue")}>Render Queue</button>
            </div>
          </OverviewSection>

          {err && <div style={{ marginTop: 12, color: "var(--danger)" }}>{err}</div>}
        </div>

        <div className="workspace-mainStack">
          <div className="card workspace-featureCard">
            <CreativeDirectionPanel
              projectId={projectId}
              analysis={analysis}
              plan={plan}
              selectedVariant={selectedVariant}
              onNavigate={onNavigate}
            />
          </div>

          <div className="card workspace-featureCard">
            <div className="workspace-sectionHead">
              <div className="workspace-sectionTitle">Timeline Preview</div>
              <div className="small">Scene structure and pacing before the full arrangement pass.</div>
            </div>
            <TimelinePreview />
          </div>

          <details className="card workspace-inspectCard" open={uiMode === "advanced"}>
            <summary className="workspace-inspectSummary">Inspect</summary>
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Selected variant (raw)</div>
              {plan?.variants?.length ? (
                <pre>{JSON.stringify(plan.variants[selectedVariant], null, 2)}</pre>
              ) : (
                <div className="small">No plan.</div>
              )}

              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Analysis</div>
              {!analysis && <div className="small">No analysis yet.</div>}
              {analysis && <pre>{JSON.stringify(analysis, null, 2)}</pre>}

              <hr />
              <div style={{ fontWeight: 800, marginBottom: 10 }}>Last action result</div>
              {!info && <div className="small">No recent action.</div>}
              {info && <pre>{JSON.stringify(info, null, 2)}</pre>}
            </div>
          </details>
        </div>
      </div> : null}

      {workspaceView === "planner" ? (
        <div className="workspace-panel card workspace-workbenchCard">
          <div className="workspace-panelHeader">
            <div>
              <div className="workspace-sectionTitle">AI Planner + Storyboard Builder</div>
              <div className="small">
                The detailed planner now runs inside the current project workflow, so prompt generation and renderer sync stay tied to the same session.
              </div>
            </div>
            <div className="workspace-panelActions">
              <button className="secondary" onClick={() => setWorkspaceView("overview")}>
                Back to overview
              </button>
              <button className="secondary" onClick={() => onNavigate?.("directorLab")} disabled={!projectId}>
                Open EDMG Director
              </button>
              <button className="secondary" onClick={() => onNavigate?.("plannerLab")} disabled={!projectId}>
                Open standalone
              </button>
              <button className="secondary" onClick={() => setWorkspaceView("storyboard")} disabled={!storyboardReady}>
                Saved storyboard
              </button>
            </div>
          </div>
          <AiNlpWorkbench
            compact
            studioProjectId={projectId}
            studioProjectName={project?.name || ""}
            studioProject={project}
            studioSelectedVariant={selectedVariant}
            onSyncToStudio={syncPlannerLab}
          />
        </div>
      ) : null}

      {workspaceView === "reactive" ? (
        <div className="workspace-panel card workspace-workbenchCard">
          <div className="workspace-panelHeader">
            <div>
              <div className="workspace-sectionTitle">Reactive Lab + Renderer Handoff</div>
              <div className="small">
                Reactive scheduling now lives inside the current project workflow so motion, cueing, and renderer handoff stay tied to the same session.
              </div>
            </div>
            <div className="workspace-panelActions">
              <button className="secondary" onClick={() => setWorkspaceView("overview")}>
                Back to overview
              </button>
              <button className="secondary" onClick={() => onNavigate?.("directorLab")} disabled={!projectId}>
                Open EDMG Director
              </button>
              <button className="secondary" onClick={() => onNavigate?.("reactiveLab")} disabled={!projectId}>
                Open standalone
              </button>
              <button className="secondary" onClick={() => onNavigate?.("timeline")} disabled={!analysisReady}>
                Open Timeline
              </button>
              <button onClick={() => onNavigate?.("render")} disabled={!analysisReady}>
                Go to Render
              </button>
            </div>
          </div>
          <AudioReactiveWorkbench
            compact
            studioProjectId={projectId}
            studioProjectName={project?.name || ""}
            studioProject={project}
            studioSelectedVariant={selectedVariant}
            onSyncToStudio={syncReactiveLab}
          />
        </div>
      ) : null}

      {workspaceView === "storyboard" ? (
        <div className="workspace-storyboardStack">
          <div className="card workspace-featureCard">
            <div className="workspace-panelHeader">
              <div>
                <div className="workspace-sectionTitle">Storyboard Review</div>
                <div className="small">
                  Review the saved project plan, scene timing, and prompt handoff in one place before opening Timeline or Render.
                </div>
              </div>
              <div className="workspace-panelActions">
                <button className="secondary" onClick={() => setWorkspaceView("planner")}>
                  Open AI Planner
                </button>
                <button className="secondary" onClick={() => onNavigate?.("timeline")} disabled={!storyboardReady}>
                  Open Timeline
                </button>
                <button onClick={() => onNavigate?.("render")} disabled={!storyboardReady}>
                  Go to Render
                </button>
              </div>
            </div>

            {plan?.variants?.length ? (
              <div className="workspace-storyboardMeta">
                <label className="workspace-storyboardField">
                  <span>Variant</span>
                  <select value={selectedVariant} onChange={(e) => setSelectedVariant(Number(e.target.value))}>
                    {plan.variants.map((v: any, idx: number) => (
                      <option key={idx} value={idx}>
                        {idx + 1}. {v.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="workspace-storyboardActions">
                  <button className="secondary" onClick={() => void shuffleStoryboardScenes()} disabled={variantScenes.length < 2}>
                    Shuffle scenes
                  </button>
                  <button onClick={() => void applyTimelinePlan(false)} disabled={!storyboardReady}>
                    Apply to timeline
                  </button>
                  <button className="secondary" onClick={() => void applyTimelinePlan(true)} disabled={!storyboardReady}>
                    Overwrite timeline
                  </button>
                </div>
              </div>
            ) : null}

            <TimelinePreview detailed />
          </div>

          <div className="workspace-storyboardGrid">
            {variantScenes.length ? (
              variantScenes.map((scene: any, index: number) => (
                <article key={scene.id || index} className="card workspace-storyboardCard">
                  <div className="workspace-storyboardCardHead">
                    <div>
                      <div className="workspace-storyboardIndex">Scene {index + 1}</div>
                      <h3>{scene.name || "Untitled scene"}</h3>
                    </div>
                    <div className="small">
                      {Number(scene.start_s ?? index * 5).toFixed(2)}s → {Number(scene.end_s ?? (index * 5 + 5)).toFixed(2)}s
                    </div>
                  </div>
                  <div className="workspace-storyboardCardActions">
                    <button className="secondary" onClick={() => void moveStoryboardScene(index, -1)} disabled={index === 0}>
                      Move earlier
                    </button>
                    <button className="secondary" onClick={() => void moveStoryboardScene(index, 1)} disabled={index === variantScenes.length - 1}>
                      Move later
                    </button>
                  </div>
                  <div className="workspace-storyboardPrompt">{scene.prompt || "No prompt yet."}</div>
                  {scene.negative_prompt ? (
                    <div className="workspace-storyboardNote"><strong>Negative:</strong> {scene.negative_prompt}</div>
                  ) : null}
                  {scene.transition ? (
                    <div className="workspace-storyboardNote"><strong>Transition:</strong> {scene.transition}</div>
                  ) : null}
                </article>
              ))
            ) : (
              <div className="card workspace-storyboardEmpty">
                <div className="workspace-sectionTitle">No storyboard saved yet</div>
                <div className="small">Generate a plan in Overview or sync the AI Planner to populate this review surface.</div>
              </div>
            )}
          </div>
        </div>
      ) : null}

      <div className="small workspace-footerNote">
        Use Outputs to view images/videos. The backend runs an always-on worker by default; Render Queue lets you inspect jobs/logs and retry/cancel, while the sidebar keeps standalone labs available when you want them separate.
      </div>
    </div>
  );
}
