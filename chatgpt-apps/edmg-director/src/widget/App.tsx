import { useEffect, useMemo, useState } from "react";
import {
  REACTIVE_SCHEDULE_FIELDS,
  buildReactiveDraft,
  getActiveVariant,
  parseScheduleString,
  type AnalysisSummary,
  type PlanPreviewOutput,
  type PlanScene,
  type PlanVariant,
  type ScheduleField,
  type SchedulePoint,
} from "./reactiveScheduler";

type HostApi = {
  theme?: string;
  locale?: string;
  displayMode?: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  toolResponseMetadata?: Record<string, unknown>;
  widgetState?: unknown;
  callTool?: (name: string, args?: Record<string, unknown>) => Promise<unknown>;
  sendFollowUpMessage?:
    | ((message: string) => Promise<unknown> | void)
    | ((args: { prompt: string; scrollToBottom?: boolean }) => Promise<unknown> | void);
  requestDisplayMode?: (args: {
    mode: "inline" | "pip" | "fullscreen";
  }) => Promise<unknown> | void;
};

type TimelineSummary = {
  rootKeys: string[];
  trackCount: number;
};

type ActionResultOutput = {
  type: "action-result";
  projectId: string;
  projectName: string;
  variantIndex: number;
  overwrite: boolean;
  applied: boolean;
  message: string;
  timelineSummary: TimelineSummary | null;
};

type PlannerImportResultOutput = {
  type: "planner-import-result";
  projectId: string;
  projectName: string;
  variantCount: number;
  appliedTimeline: boolean;
  timelineSummary: TimelineSummary | null;
  message: string;
};

type ReactiveApplyResultOutput = {
  type: "reactive-apply-result";
  projectId: string;
  projectName: string;
  cueEventCount: number;
  keyframeCount: number;
  sectionCount: number;
  timelineSummary: TimelineSummary | null;
  message: string;
};

type BackendStatusOutput = {
  type: "backend-status";
  available: boolean;
  baseUrl: string;
  version: string | null;
  detail: string;
  checkedAt: string;
};

type OperationResult =
  | ActionResultOutput
  | PlannerImportResultOutput
  | ReactiveApplyResultOutput;

type ErrorState = {
  title: string;
  detail: string;
};

type InspectorToolName =
  | "backend_status"
  | "search"
  | "fetch"
  | "analyze_project_audio"
  | "generate_plan_preview"
  | "apply_plan_variant"
  | "import_planner_lab_payload"
  | "apply_reactive_handoff";

type InspectorToolOption = {
  name: InspectorToolName;
  label: string;
  updatesMainOutput: boolean;
};

type SchedulePreviewSeries = {
  field: ScheduleField;
  label: string;
  points: SchedulePoint[];
  frameRange: string;
  valueRange: string;
  path: string;
};

declare global {
  interface Window {
    openai?: HostApi;
  }
}

const INSPECTOR_TOOL_OPTIONS: InspectorToolOption[] = [
  { name: "backend_status", label: "backend_status", updatesMainOutput: false },
  { name: "search", label: "search", updatesMainOutput: false },
  { name: "fetch", label: "fetch", updatesMainOutput: false },
  { name: "analyze_project_audio", label: "analyze_project_audio", updatesMainOutput: false },
  { name: "generate_plan_preview", label: "generate_plan_preview", updatesMainOutput: true },
  { name: "apply_plan_variant", label: "apply_plan_variant", updatesMainOutput: true },
  { name: "import_planner_lab_payload", label: "import_planner_lab_payload", updatesMainOutput: true },
  { name: "apply_reactive_handoff", label: "apply_reactive_handoff", updatesMainOutput: true },
];

const SCHEDULE_FIELD_LABELS: Record<ScheduleField, string> = {
  zoom: "Zoom",
  rotation_y: "Rotation Y",
  rotation_z: "Rotation Z",
  translation_x: "Translation X",
  translation_y: "Translation Y",
  translation_z: "Translation Z",
  strength: "Strength",
  cfg_scale: "CFG Scale",
  brightness: "Brightness",
};

function host(): HostApi | undefined {
  return window.openai;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function coerceNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function readToolOutput(value: unknown): unknown {
  const record = asRecord(value);
  if ("structuredContent" in record) {
    return record.structuredContent;
  }
  return value;
}

function formatSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "TBD";
  }
  const total = Math.max(0, Math.round(value));
  const minutes = Math.floor(total / 60)
    .toString()
    .padStart(2, "0");
  const seconds = (total % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function applyTheme(theme?: string): void {
  const normalized = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = normalized;
  document.documentElement.style.colorScheme = normalized;
}

function isPlanPreview(value: unknown): value is PlanPreviewOutput {
  const record = asRecord(value);
  return record.type === "plan-preview" && Array.isArray(record.variants);
}

function isActionResult(value: unknown): value is ActionResultOutput {
  return asRecord(value).type === "action-result";
}

function isPlannerImportResult(value: unknown): value is PlannerImportResultOutput {
  return asRecord(value).type === "planner-import-result";
}

function isReactiveApplyResult(value: unknown): value is ReactiveApplyResultOutput {
  return asRecord(value).type === "reactive-apply-result";
}

function isBackendStatus(value: unknown): value is BackendStatusOutput {
  return asRecord(value).type === "backend-status";
}

function buildPlannerSyncPayload(
  preview: PlanPreviewOutput,
  selectedVariantIndex: number,
  previewInput: Record<string, unknown>,
) {
  const orderedVariants = [
    ...preview.variants.filter((variant) => variant.index === selectedVariantIndex),
    ...preview.variants.filter((variant) => variant.index !== selectedVariantIndex),
  ];

  const analysis = preview.analysisSummary
    ? {
        features: {
          bpm: preview.analysisSummary.bpm ?? undefined,
          duration_s: preview.analysisSummary.durationS ?? undefined,
        },
        hook_line: preview.analysisSummary.hookLine ?? undefined,
        narrative_structure: preview.analysisSummary.narrative ?? undefined,
      }
    : {};

  const plan = {
    title: preview.projectName,
    source: preview.planSource ?? "edmg-director",
    variants: orderedVariants.map((variant) => ({
      title: variant.label,
      summary: variant.summary ?? undefined,
      duration_s: variant.durationS ?? undefined,
      source_variant_index: variant.index,
      scenes: variant.scenes.map((scene) => ({
        title: scene.title,
        prompt: scene.prompt,
        start_s: scene.startS ?? undefined,
        end_s: scene.endS ?? undefined,
        duration_s: scene.durationS ?? undefined,
        shot_type: scene.shotType ?? undefined,
        rationale: scene.rationale ?? undefined,
        transition_cue: scene.transitionCue ?? undefined,
        continuity_note: scene.continuityNote ?? undefined,
      })),
    })),
  };

  const maxScenesFromInput = coerceNumber(previewInput.maxScenes);
  const numVariantsFromInput = coerceNumber(previewInput.numVariants);
  const maxScenes =
    maxScenesFromInput ??
    Math.max(...orderedVariants.map((variant) => variant.scenes.length), 1);

  const settings = {
    mode: preview.mode,
    selected_variant_index: selectedVariantIndex,
    requested_variant_count: numVariantsFromInput ?? preview.variants.length,
    requested_max_scenes: maxScenes,
    title: asString(previewInput.title) || preview.projectName,
    user_notes: asString(previewInput.userNotes) || undefined,
    style_prefs: asString(previewInput.stylePrefs) || undefined,
  };

  return {
    analysis,
    plan,
    settings,
  };
}


function readErrorDetail(reason: unknown, fallback: string): string {
  if (reason instanceof Error && reason.message) {
    return reason.message;
  }

  const record = asRecord(reason);
  const nestedError = asRecord(record.error);
  return (
    asString(record.message) ||
    asString(record.detail) ||
    asString(nestedError.message) ||
    fallback
  );
}

function buildFriendlyError(reason: unknown, fallback: string): ErrorState {
  const detail = readErrorDetail(reason, fallback);
  const lower = detail.toLowerCase();

  if (lower.includes("could not reach edmg backend") || lower.includes("actively refused")) {
    return {
      title: "Backend unavailable",
      detail: `${detail} Start the EDMG backend or verify EDMG_BASE_URL.`,
    };
  }

  if (lower.includes("timed out") || lower.includes("timeout")) {
    return {
      title: "Backend timed out",
      detail: `${detail} The EDMG backend accepted the request but did not finish in time.`,
    };
  }

  if (lower.includes("not found") || (lower.includes("404") && lower.includes("project"))) {
    return {
      title: "Project unavailable",
      detail: `${detail} Refresh project search results and confirm the selected project still exists.`,
    };
  }

  if (lower.includes("unexpected token") || lower.includes("json")) {
    return {
      title: "Invalid JSON payload",
      detail: "The JSON payload could not be parsed. Fix the editor contents and retry.",
    };
  }

  if (
    lower.includes("validation") ||
    lower.includes("schema") ||
    lower.includes("unprocessable") ||
    lower.includes("required field")
  ) {
    return {
      title: "Backend rejected payload",
      detail: `${detail} Adjust the payload fields and retry.`,
    };
  }

  if (lower.includes("no audio uploaded")) {
    return {
      title: "Audio missing",
      detail: "This project has no uploaded audio, so backend audio analysis cannot run yet.",
    };
  }

  if (lower.includes("host bridge")) {
    return {
      title: "Widget bridge unavailable",
      detail,
    };
  }

  return {
    title: "Tool call failed",
    detail,
  };
}

function safeJsonObject(text: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(text);
    return asRecord(parsed);
  } catch {
    return null;
  }
}

function buildSparklinePath(points: SchedulePoint[]): string {
  if (!points.length) {
    return "";
  }

  if (points.length === 1) {
    return "M6 40 L198 40";
  }

  const width = 192;
  const height = 68;
  const left = 6;
  const top = 6;
  const frames = points.map((point) => point.frame);
  const values = points.map((point) => point.value);
  const minFrame = Math.min(...frames);
  const maxFrame = Math.max(...frames);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const frameSpan = Math.max(maxFrame - minFrame, 1);
  const valueSpan = Math.max(maxValue - minValue, 0.001);

  return points
    .map((point, index) => {
      const x = left + ((point.frame - minFrame) / frameSpan) * width;
      const y = top + height - ((point.value - minValue) / valueSpan) * height;
      const command = index === 0 ? "M" : "L";
      return `${command}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildSchedulePreviewSeries(reactiveDraft: string): SchedulePreviewSeries[] | null {
  const parsed = safeJsonObject(reactiveDraft);
  if (!parsed) {
    return null;
  }

  const schedules = asRecord(parsed.schedules);
  return REACTIVE_SCHEDULE_FIELDS.map((field) => {
    const points = parseScheduleString(schedules[field]);
    const frames = points.map((point) => point.frame);
    const values = points.map((point) => point.value);
    const frameRange = points.length
      ? `${Math.min(...frames)}-${Math.max(...frames)}f`
      : "No frames";
    const valueRange = points.length
      ? `${Math.min(...values).toFixed(2)} to ${Math.max(...values).toFixed(2)}`
      : "No values";

    return {
      field,
      label: SCHEDULE_FIELD_LABELS[field],
      points,
      frameRange,
      valueRange,
      path: buildSparklinePath(points),
    };
  });
}

function buildInspectorInput(
  tool: InspectorToolName,
  preview: PlanPreviewOutput | null,
  activeVariant: PlanVariant | null,
  previewInput: Record<string, unknown> | null,
  reactiveDraft: string,
  plannerApplyTimeline: boolean,
  plannerOverwriteTimeline: boolean,
  reactiveOverwriteMotionTrack: boolean,
  reactiveOverwriteCamera: boolean,
) {
  const projectId = preview?.projectId ?? "";
  const variantIndex = activeVariant?.index ?? 0;

  if (tool === "backend_status") {
    return {};
  }

  if (tool === "search") {
    return {
      query: preview?.projectName ?? "",
      limit: 5,
    };
  }

  if (tool === "fetch" || tool === "analyze_project_audio") {
    return {
      projectId,
    };
  }

  if (tool === "generate_plan_preview") {
    return {
      projectId,
      mode: asString(previewInput?.mode) || preview?.mode || "auto",
      title: asString(previewInput?.title) || preview?.projectName || "",
      userNotes: asString(previewInput?.userNotes) || "",
      stylePrefs: asString(previewInput?.stylePrefs) || "",
      numVariants: coerceNumber(previewInput?.numVariants) ?? preview?.variants.length ?? 3,
      maxScenes:
        coerceNumber(previewInput?.maxScenes) ??
        Math.max(...(preview?.variants.map((variant) => variant.scenes.length) ?? [12])),
    };
  }

  if (tool === "apply_plan_variant") {
    return {
      projectId,
      variantIndex,
      overwrite: true,
    };
  }

  if (tool === "import_planner_lab_payload") {
    if (!preview) {
      return {
        projectId,
        plan: {},
      };
    }
    const payload = buildPlannerSyncPayload(preview, variantIndex, previewInput ?? {});
    return {
      projectId,
      analysis: payload.analysis,
      plan: payload.plan,
      settings: payload.settings,
      applyTimeline: plannerApplyTimeline,
      overwriteTimeline: plannerOverwriteTimeline,
    };
  }

  if (tool === "apply_reactive_handoff") {
    const parsed = safeJsonObject(reactiveDraft) ?? {};
    return {
      projectId,
      metadata: asRecord(parsed.metadata),
      keyframes: asArray(parsed.keyframes),
      beatMarkers: asArray(parsed.beatMarkers),
      cueEvents: asArray(parsed.cueEvents),
      sections: asArray(parsed.sections),
      repairSuggestions: asArray(parsed.repairSuggestions),
      schedules: asRecord(parsed.schedules),
      handoffManifest: asRecord(parsed.handoffManifest),
      overwriteMotionTrack: reactiveOverwriteMotionTrack,
      overwriteCamera: reactiveOverwriteCamera,
    };
  }

  return {};
}

function StatusBanner(props: {
  tone: "success" | "error" | "warning";
  title: string;
  detail: string;
}) {
  return (
    <section className={`status-banner ${props.tone}`}>
      <div className="status-title">{props.title}</div>
      <p>{props.detail}</p>
    </section>
  );
}

function ScheduleInspector(props: { reactiveDraft: string }) {
  const series = buildSchedulePreviewSeries(props.reactiveDraft);

  if (!series) {
    return (
      <div className="schedule-inspector empty">
        <div className="schedule-inspector-head">
          <div>
            <h4>Schedule Preview</h4>
            <p>Fix the reactive JSON to render the motion-curve preview.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="schedule-inspector">
      <div className="schedule-inspector-head">
        <div>
          <h4>Schedule Preview</h4>
          <p>Review the generated motion curves before applying the reactive handoff.</p>
        </div>
      </div>
      <div className="schedule-grid">
        {series.map((entry) => (
          <article key={entry.field} className="schedule-card">
            <div className="schedule-card-head">
              <strong>{entry.label}</strong>
              <span>{entry.points.length} pts</span>
            </div>
            <svg
              className="schedule-chart"
              viewBox="0 0 204 80"
              role="img"
              aria-label={`${entry.label} schedule preview`}
            >
              <line x1="6" y1="74" x2="198" y2="74" className="schedule-axis" />
              {entry.path ? <path d={entry.path} className="schedule-line" /> : null}
            </svg>
            <div className="schedule-card-meta">
              <span>{entry.frameRange}</span>
              <span>{entry.valueRange}</span>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function Hero(props: {
  projectName: string;
  mode: string;
  source: string | null;
  variantCount: number;
  onFullscreen: () => Promise<void>;
  onAskAssistant: () => Promise<void>;
}) {
  return (
    <header className="hero">
      <div>
        <div className="eyebrow">EDMG Director</div>
        <h1>{props.projectName}</h1>
        <p>
          Interactive storyboard review for {props.variantCount} variant
          {props.variantCount === 1 ? "" : "s"}.
        </p>
      </div>
      <div className="hero-meta">
        <div className="pill-row">
          <span className="pill">mode: {props.mode}</span>
          {props.source ? <span className="pill">source: {props.source}</span> : null}
        </div>
        <div className="hero-actions">
          <button className="ghost-button" onClick={() => void props.onAskAssistant()}>
            Ask ChatGPT
          </button>
          <button className="primary-button" onClick={() => void props.onFullscreen()}>
            Fullscreen
          </button>
        </div>
      </div>
    </header>
  );
}

function AnalysisPanel(props: { summary: AnalysisSummary | null }) {
  if (!props.summary) {
    return (
      <section className="analysis-panel muted">
        <h2>Analysis Snapshot</h2>
        <p>No audio analysis summary was attached to this plan.</p>
      </section>
    );
  }

  return (
    <section className="analysis-panel">
      <h2>Analysis Snapshot</h2>
      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">Tempo</span>
          <strong>{props.summary.bpm ?? "?"} BPM</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Duration</span>
          <strong>{formatSeconds(props.summary.durationS)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-label">Narrative</span>
          <strong>{props.summary.narrative ?? "Unspecified"}</strong>
        </div>
      </div>
      {props.summary.hookLine ? (
        <div className="hook-line">
          <span className="metric-label">Hook line</span>
          <p>{props.summary.hookLine}</p>
        </div>
      ) : null}
    </section>
  );
}

function ResultView(props: {
  result: OperationResult;
  onAskAssistant: () => Promise<void>;
}) {
  let eyebrow = "Timeline Update";
  let pills: string[] = [];

  if (props.result.type === "action-result") {
    pills = [
      `variant ${props.result.variantIndex + 1}`,
      props.result.overwrite ? "overwrite" : "merge",
    ];
  } else if (props.result.type === "planner-import-result") {
    eyebrow = "Planner Import";
    pills = [
      `${props.result.variantCount} variant${props.result.variantCount === 1 ? "" : "s"}`,
      props.result.appliedTimeline ? "timeline refreshed" : "plan only",
    ];
  } else if (props.result.type === "reactive-apply-result") {
    eyebrow = "Reactive Handoff";
    pills = [
      `${props.result.cueEventCount} cue${props.result.cueEventCount === 1 ? "" : "s"}`,
      `${props.result.keyframeCount} keyframe${props.result.keyframeCount === 1 ? "" : "s"}`,
      `${props.result.sectionCount} section${props.result.sectionCount === 1 ? "" : "s"}`,
    ];
  }

  if (props.result.timelineSummary) {
    pills.push(
      `${props.result.timelineSummary.trackCount} track${props.result.timelineSummary.trackCount === 1 ? "" : "s"}`,
    );
  }

  return (
    <section className="result-card">
      <div className="result-header">
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h2>{props.result.projectName}</h2>
        </div>
        <button className="ghost-button" onClick={() => void props.onAskAssistant()}>
          Discuss Next Steps
        </button>
      </div>
      <p>{props.result.message}</p>
      <div className="pill-row">
        {pills.map((pill) => (
          <span key={pill} className="pill">
            {pill}
          </span>
        ))}
      </div>
    </section>
  );
}

function VariantCard(props: {
  variant: PlanVariant;
  busy: boolean;
  isWorkingVariant: boolean;
  onApply: () => Promise<void>;
  onSelect: () => void;
}) {
  return (
    <article className={`variant-card ${props.isWorkingVariant ? "selected-card" : ""}`}>
      <div className="variant-head">
        <div>
          <div className="variant-kicker">Variant {props.variant.index + 1}</div>
          <h3>{props.variant.label}</h3>
          {props.variant.summary ? <p>{props.variant.summary}</p> : null}
        </div>
        <div className="variant-side">
          <span className="scene-count">
            {props.variant.scenes.length} scene{props.variant.scenes.length === 1 ? "" : "s"}
          </span>
          <span className="scene-count">{formatSeconds(props.variant.durationS)}</span>
        </div>
      </div>

      <div className="card-actions">
        <button className="ghost-button" onClick={props.onSelect}>
          {props.isWorkingVariant ? "Working Variant" : "Use For Handoff"}
        </button>
        <button
          className="primary-button apply-button"
          disabled={props.busy}
          onClick={() => void props.onApply()}
        >
          {props.busy ? "Applying..." : "Apply To Timeline"}
        </button>
      </div>

      <div className="scene-list">
        {props.variant.scenes.map((scene) => (
          <section key={`${props.variant.index}-${scene.index}`} className="scene-card">
            <div className="scene-head">
              <div>
                <span className="scene-chip">Scene {scene.index + 1}</span>
                <h4>{scene.title}</h4>
              </div>
              <span className="scene-time">
                {formatSeconds(scene.startS)} - {formatSeconds(scene.endS)}
              </span>
            </div>
            {scene.shotType ? <div className="micro-note">shot: {scene.shotType}</div> : null}
            <p className="scene-prompt">{scene.prompt || "No prompt provided."}</p>
            <div className="scene-notes">
              {scene.rationale ? (
                <div>
                  <span>Rationale</span>
                  <p>{scene.rationale}</p>
                </div>
              ) : null}
              {scene.transitionCue ? (
                <div>
                  <span>Transition</span>
                  <p>{scene.transitionCue}</p>
                </div>
              ) : null}
              {scene.continuityNote ? (
                <div>
                  <span>Continuity</span>
                  <p>{scene.continuityNote}</p>
                </div>
              ) : null}
            </div>
          </section>
        ))}
      </div>
    </article>
  );
}

function HandoffPanel(props: {
  activeVariant: PlanVariant | null;
  plannerBusy: boolean;
  reactiveBusy: boolean;
  plannerApplyTimeline: boolean;
  plannerOverwriteTimeline: boolean;
  reactiveOverwriteMotionTrack: boolean;
  reactiveOverwriteCamera: boolean;
  reactiveDraft: string;
  reactiveDirty: boolean;
  onPlannerApplyTimelineChange: (value: boolean) => void;
  onPlannerOverwriteTimelineChange: (value: boolean) => void;
  onReactiveOverwriteMotionTrackChange: (value: boolean) => void;
  onReactiveOverwriteCameraChange: (value: boolean) => void;
  onReactiveDraftChange: (value: string) => void;
  onResetReactiveDraft: () => void;
  onImportPlanner: () => Promise<void>;
  onApplyReactive: () => Promise<void>;
}) {
  return (
    <section className="handoff-panel">
      <div className="handoff-header">
        <div>
          <div className="eyebrow">Studio Handoff</div>
          <h2>Push the working variant back into Studio</h2>
          <p>
            Planner import reorders the current review pack so the working variant becomes
            variant 1 in the imported plan. That matches the backend behavior that applies
            variant 0 when timeline sync is enabled.
          </p>
        </div>
        {props.activeVariant ? (
          <div className="pill-row">
            <span className="pill">working: {props.activeVariant.label}</span>
            <span className="pill">
              {props.activeVariant.scenes.length} scene
              {props.activeVariant.scenes.length === 1 ? "" : "s"}
            </span>
            <span className="pill">{formatSeconds(props.activeVariant.durationS)}</span>
          </div>
        ) : null}
      </div>

      <div className="handoff-grid">
        <article className="handoff-card">
          <div className="handoff-card-head">
            <div>
              <h3>Planner Sync</h3>
              <p>
                Import the review board preview as a planner payload and optionally refresh
                the Workshop timeline immediately.
              </p>
            </div>
            <button
              className="primary-button"
              disabled={!props.activeVariant || props.plannerBusy}
              onClick={() => void props.onImportPlanner()}
            >
              {props.plannerBusy ? "Syncing..." : "Import Current Preview"}
            </button>
          </div>

          <div className="option-grid">
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={props.plannerApplyTimeline}
                onChange={(event) => props.onPlannerApplyTimelineChange(event.target.checked)}
              />
              <span>Apply timeline after import</span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={props.plannerOverwriteTimeline}
                onChange={(event) => props.onPlannerOverwriteTimelineChange(event.target.checked)}
              />
              <span>Overwrite existing timeline</span>
            </label>
          </div>
        </article>

        <article className="handoff-card">
          <div className="handoff-card-head">
            <div>
              <h3>Reactive Handoff</h3>
              <p>
                The draft below is now derived from scene timing, transition language, and
                prompt intensity. Edit it if needed before applying.
              </p>
            </div>
            <div className="card-actions">
              <button className="ghost-button" onClick={props.onResetReactiveDraft}>
                Reset Draft
              </button>
              <button
                className="primary-button"
                disabled={!props.activeVariant || props.reactiveBusy}
                onClick={() => void props.onApplyReactive()}
              >
                {props.reactiveBusy ? "Applying..." : "Apply Reactive Handoff"}
              </button>
            </div>
          </div>

          <div className="option-grid">
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={props.reactiveOverwriteMotionTrack}
                onChange={(event) =>
                  props.onReactiveOverwriteMotionTrackChange(event.target.checked)
                }
              />
              <span>Overwrite motion track</span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={props.reactiveOverwriteCamera}
                onChange={(event) => props.onReactiveOverwriteCameraChange(event.target.checked)}
              />
              <span>Overwrite camera track</span>
            </label>
          </div>

          <ScheduleInspector reactiveDraft={props.reactiveDraft} />

          <label className="editor-label" htmlFor="reactive-draft">
            Reactive payload JSON{props.reactiveDirty ? " (edited)" : ""}
          </label>
          <textarea
            id="reactive-draft"
            className="json-editor"
            value={props.reactiveDraft}
            onChange={(event) => props.onReactiveDraftChange(event.target.value)}
            spellCheck={false}
          />
        </article>
      </div>
    </section>
  );
}

function InspectorPanel(props: {
  backendStatus: BackendStatusOutput | null;
  backendBusy: boolean;
  bridgeAvailable: boolean;
  preview: PlanPreviewOutput | null;
  activeVariant: PlanVariant | null;
  selectedTool: InspectorToolName;
  toolBusy: boolean;
  toolInput: string;
  toolOutput: string;
  toolDirty: boolean;
  onRefreshBackend: () => Promise<void>;
  onToolChange: (tool: InspectorToolName) => void;
  onToolInputChange: (value: string) => void;
  onResetToolInput: () => void;
  onRunTool: () => Promise<void>;
}) {
  const hostApi = host();
  const metadata = {
    theme: hostApi?.theme ?? null,
    locale: hostApi?.locale ?? null,
    displayMode: hostApi?.displayMode ?? null,
    hasCallTool: Boolean(hostApi?.callTool),
    hasSendFollowUpMessage: Boolean(hostApi?.sendFollowUpMessage),
    hasRequestDisplayMode: Boolean(hostApi?.requestDisplayMode),
    currentToolInput: hostApi?.toolInput ?? null,
    currentToolResponseMetadata: hostApi?.toolResponseMetadata ?? null,
  };

  return (
    <details className="inspector-panel" open={props.backendStatus?.available === false}>
      <summary>Inspector & MCP tool harness</summary>
      <div className="inspector-grid">
        <article className="inspector-card">
          <div className="inspector-card-head">
            <div>
              <h3>Runtime health</h3>
              <p>Check host bridge availability and probe the EDMG backend directly through the MCP app.</p>
            </div>
            <button className="ghost-button" disabled={props.backendBusy} onClick={() => void props.onRefreshBackend()}>
              {props.backendBusy ? "Checking..." : "Refresh backend"}
            </button>
          </div>
          <div className="pill-row">
            <span className={`status-pill ${props.bridgeAvailable ? "good" : "bad"}`}>
              bridge {props.bridgeAvailable ? "ready" : "missing"}
            </span>
            <span
              className={`status-pill ${
                props.backendStatus?.available ? "good" : props.backendStatus ? "bad" : "warn"
              }`}
            >
              backend {props.backendStatus?.available ? "ready" : props.backendStatus ? "offline" : "unknown"}
            </span>
            {props.preview ? (
              <span className="status-pill neutral">project {props.preview.projectId}</span>
            ) : null}
            {props.activeVariant ? (
              <span className="status-pill neutral">variant {props.activeVariant.index + 1}</span>
            ) : null}
          </div>
          {props.backendStatus ? (
            <div className={`backend-card ${props.backendStatus.available ? "good" : "bad"}`}>
              <strong>{props.backendStatus.available ? "Backend reachable" : "Backend unavailable"}</strong>
              <p>{props.backendStatus.detail}</p>
              <div className="micro-note">
                base URL: {props.backendStatus.baseUrl}
                {props.backendStatus.version ? ` · version ${props.backendStatus.version}` : ""}
              </div>
            </div>
          ) : (
            <div className="backend-card warn">
              <strong>Backend status not checked yet</strong>
              <p>Run the health probe before troubleshooting other MCP tool failures.</p>
            </div>
          )}
          <label className="editor-label" htmlFor="runtime-metadata">
            Host metadata
          </label>
          <pre id="runtime-metadata" className="inspector-output">
            {formatJson(metadata)}
          </pre>
        </article>

        <article className="inspector-card">
          <div className="inspector-card-head">
            <div>
              <h3>Tool runner</h3>
              <p>Run any registered tool with editable JSON input and inspect the raw result without leaving the widget.</p>
            </div>
            <div className="card-actions">
              <button className="ghost-button" onClick={props.onResetToolInput}>
                Reset input
              </button>
              <button
                className="primary-button"
                disabled={!props.bridgeAvailable || props.toolBusy}
                onClick={() => void props.onRunTool()}
              >
                {props.toolBusy ? "Running..." : "Run tool"}
              </button>
            </div>
          </div>
          <label className="editor-label" htmlFor="tool-select">
            Tool
          </label>
          <select
            id="tool-select"
            className="inspector-select"
            value={props.selectedTool}
            onChange={(event) => props.onToolChange(event.target.value as InspectorToolName)}
          >
            {INSPECTOR_TOOL_OPTIONS.map((option) => (
              <option key={option.name} value={option.name}>
                {option.label}
              </option>
            ))}
          </select>

          <label className="editor-label" htmlFor="tool-input">
            Tool input JSON{props.toolDirty ? " (edited)" : ""}
          </label>
          <textarea
            id="tool-input"
            className="json-editor compact"
            value={props.toolInput}
            onChange={(event) => props.onToolInputChange(event.target.value)}
            spellCheck={false}
          />

          <label className="editor-label" htmlFor="tool-output">
            Tool output
          </label>
          <pre id="tool-output" className="inspector-output">
            {props.toolOutput || "Run a tool to inspect its raw result."}
          </pre>
        </article>
      </div>
    </details>
  );
}

function FallbackView(props: { raw: unknown }) {
  return (
    <section className="fallback-card">
      <div className="eyebrow">Widget Output</div>
      <h1>Awaiting plan preview</h1>
      <p>
        Run <code>generate_plan_preview</code> to load storyboard variants into the review
        board.
      </p>
      {props.raw ? <pre>{JSON.stringify(props.raw, null, 2)}</pre> : null}
    </section>
  );
}

export default function App() {
  const initialOutput = readToolOutput(host()?.toolOutput);
  const initialPreview = isPlanPreview(initialOutput) ? initialOutput : null;
  const initialPreviewInput = initialPreview ? asRecord(host()?.toolInput) : null;
  const initialSelectedVariantIndex =
    initialPreview?.selectedVariantIndex ?? initialPreview?.variants[0]?.index ?? 0;

  const [output, setOutput] = useState<unknown>(initialOutput);
  const [lastPreview, setLastPreview] = useState<PlanPreviewOutput | null>(initialPreview);
  const [lastPreviewInput, setLastPreviewInput] = useState<Record<string, unknown> | null>(
    initialPreviewInput,
  );
  const [selectedVariantIndex, setSelectedVariantIndex] = useState<number>(
    initialSelectedVariantIndex,
  );
  const [busyVariantIndex, setBusyVariantIndex] = useState<number | null>(null);
  const [plannerBusy, setPlannerBusy] = useState(false);
  const [reactiveBusy, setReactiveBusy] = useState(false);
  const [backendBusy, setBackendBusy] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatusOutput | null>(null);
  const [plannerApplyTimeline, setPlannerApplyTimeline] = useState(true);
  const [plannerOverwriteTimeline, setPlannerOverwriteTimeline] = useState(true);
  const [reactiveOverwriteMotionTrack, setReactiveOverwriteMotionTrack] = useState(true);
  const [reactiveOverwriteCamera, setReactiveOverwriteCamera] = useState(true);
  const [reactiveDraft, setReactiveDraft] = useState<string>(
    initialPreview ? formatJson(buildReactiveDraft(initialPreview, initialSelectedVariantIndex)) : "",
  );
  const [reactiveDirty, setReactiveDirty] = useState(false);
  const [inspectorTool, setInspectorTool] = useState<InspectorToolName>("backend_status");
  const [inspectorInput, setInspectorInput] = useState<string>(
    formatJson(
      buildInspectorInput(
        "backend_status",
        initialPreview,
        getActiveVariant(initialPreview, initialSelectedVariantIndex),
        initialPreviewInput,
        initialPreview
          ? formatJson(buildReactiveDraft(initialPreview, initialSelectedVariantIndex))
          : "",
        true,
        true,
        true,
        true,
      ),
    ),
  );
  const [inspectorOutput, setInspectorOutput] = useState("");
  const [inspectorBusy, setInspectorBusy] = useState(false);
  const [inspectorDirty, setInspectorDirty] = useState(false);
  const [error, setError] = useState<ErrorState | null>(null);

  useEffect(() => {
    applyTheme(host()?.theme);
    const sync = () => {
      applyTheme(host()?.theme);
      const nextOutput = readToolOutput(host()?.toolOutput);
      setOutput(nextOutput);
      if (isPlanPreview(nextOutput)) {
        setLastPreview(nextOutput);
        setLastPreviewInput(asRecord(host()?.toolInput));
      }
    };

    window.addEventListener("openai:set_globals", sync as EventListener);
    if (host()?.callTool) {
      void refreshBackendStatus(true);
    }
    return () => {
      window.removeEventListener("openai:set_globals", sync as EventListener);
    };
  }, []);

  useEffect(() => {
    if (isPlanPreview(output)) {
      setLastPreview(output);
      setLastPreviewInput(asRecord(host()?.toolInput));
    }
  }, [output]);

  const preview = useMemo(
    () => (isPlanPreview(output) ? output : lastPreview),
    [lastPreview, output],
  );
  const activeVariant = useMemo(
    () => getActiveVariant(preview, selectedVariantIndex),
    [preview, selectedVariantIndex],
  );
  const operationResult = useMemo(() => {
    if (isActionResult(output) || isPlannerImportResult(output) || isReactiveApplyResult(output)) {
      return output;
    }
    return null;
  }, [output]);
  const bridgeAvailable = Boolean(host()?.callTool);

  useEffect(() => {
    if (!preview) {
      return;
    }
    const selectedStillExists = preview.variants.some(
      (variant) => variant.index === selectedVariantIndex,
    );
    if (!selectedStillExists) {
      setSelectedVariantIndex(preview.selectedVariantIndex ?? preview.variants[0]?.index ?? 0);
    }
  }, [preview, selectedVariantIndex]);

  useEffect(() => {
    if (!preview || !activeVariant || reactiveDirty) {
      return;
    }
    setReactiveDraft(formatJson(buildReactiveDraft(preview, activeVariant.index)));
  }, [activeVariant, preview, reactiveDirty]);

  useEffect(() => {
    if (inspectorDirty) {
      return;
    }
    setInspectorInput(
      formatJson(
        buildInspectorInput(
          inspectorTool,
          preview,
          activeVariant,
          lastPreviewInput,
          reactiveDraft,
          plannerApplyTimeline,
          plannerOverwriteTimeline,
          reactiveOverwriteMotionTrack,
          reactiveOverwriteCamera,
        ),
      ),
    );
  }, [
    activeVariant,
    inspectorDirty,
    inspectorTool,
    lastPreviewInput,
    plannerApplyTimeline,
    plannerOverwriteTimeline,
    preview,
    reactiveDraft,
    reactiveOverwriteCamera,
    reactiveOverwriteMotionTrack,
  ]);

  async function refreshBackendStatus(silent = false): Promise<void> {
    if (!host()?.callTool) {
      if (!silent) {
        setError({
          title: "Widget bridge unavailable",
          detail: "The ChatGPT host bridge is unavailable, so backend status cannot be probed from the widget.",
        });
      }
      return;
    }

    setBackendBusy(true);
    if (!silent) {
      setError(null);
    }
    try {
      const result = await host()!.callTool!("backend_status", {});
      const normalized = readToolOutput(result);
      if (isBackendStatus(normalized)) {
        setBackendStatus(normalized);
        setInspectorOutput(formatJson(result));
      }
    } catch (reason) {
      const friendly = buildFriendlyError(reason, "Backend health probe failed.");
      if (!silent) {
        setError(friendly);
      }
    } finally {
      setBackendBusy(false);
    }
  }

  async function handleApplyVariant(variantIndex: number): Promise<void> {
    if (!preview) {
      setError({
        title: "Plan preview missing",
        detail: "No plan preview is loaded.",
      });
      return;
    }
    if (!host()?.callTool) {
      setError({
        title: "Widget bridge unavailable",
        detail: "The ChatGPT host bridge is unavailable.",
      });
      return;
    }

    setBusyVariantIndex(variantIndex);
    setError(null);

    try {
      const result = await host()!.callTool!("apply_plan_variant", {
        projectId: preview.projectId,
        variantIndex,
        overwrite: true,
      });
      setOutput(readToolOutput(result));
    } catch (reason) {
      const friendly = buildFriendlyError(reason, "Failed to apply the selected variant.");
      setError(friendly);
      if (friendly.title === "Backend unavailable") {
        void refreshBackendStatus(true);
      }
    } finally {
      setBusyVariantIndex(null);
    }
  }

  async function handleImportPlanner(): Promise<void> {
    if (!preview || !activeVariant) {
      setError({
        title: "Working variant missing",
        detail: "No working variant is available for planner import.",
      });
      return;
    }
    if (!host()?.callTool) {
      setError({
        title: "Widget bridge unavailable",
        detail: "The ChatGPT host bridge is unavailable.",
      });
      return;
    }

    setPlannerBusy(true);
    setError(null);

    try {
      const payload = buildPlannerSyncPayload(preview, activeVariant.index, lastPreviewInput ?? {});
      const result = await host()!.callTool!("import_planner_lab_payload", {
        projectId: preview.projectId,
        analysis: payload.analysis,
        plan: payload.plan,
        settings: payload.settings,
        applyTimeline: plannerApplyTimeline,
        overwriteTimeline: plannerOverwriteTimeline,
      });
      setOutput(readToolOutput(result));
    } catch (reason) {
      const friendly = buildFriendlyError(reason, "Planner import failed.");
      setError(friendly);
      if (friendly.title === "Backend unavailable") {
        void refreshBackendStatus(true);
      }
    } finally {
      setPlannerBusy(false);
    }
  }

  async function handleApplyReactive(): Promise<void> {
    if (!preview || !activeVariant) {
      setError({
        title: "Working variant missing",
        detail: "No working variant is available for reactive handoff.",
      });
      return;
    }
    if (!host()?.callTool) {
      setError({
        title: "Widget bridge unavailable",
        detail: "The ChatGPT host bridge is unavailable.",
      });
      return;
    }

    setReactiveBusy(true);
    setError(null);

    try {
      const parsed = asRecord(JSON.parse(reactiveDraft));
      const result = await host()!.callTool!("apply_reactive_handoff", {
        projectId: preview.projectId,
        metadata: asRecord(parsed.metadata),
        keyframes: asArray(parsed.keyframes),
        beatMarkers: asArray(parsed.beatMarkers),
        cueEvents: asArray(parsed.cueEvents),
        sections: asArray(parsed.sections),
        repairSuggestions: asArray(parsed.repairSuggestions),
        schedules: asRecord(parsed.schedules),
        handoffManifest: asRecord(parsed.handoffManifest),
        overwriteMotionTrack: reactiveOverwriteMotionTrack,
        overwriteCamera: reactiveOverwriteCamera,
      });
      setOutput(readToolOutput(result));
    } catch (reason) {
      const friendly = buildFriendlyError(
        reason,
        "Reactive handoff failed. Check the JSON payload and retry.",
      );
      setError(friendly);
      if (friendly.title === "Backend unavailable") {
        void refreshBackendStatus(true);
      }
    } finally {
      setReactiveBusy(false);
    }
  }

  function handleReactiveDraftChange(value: string): void {
    setReactiveDraft(value);
    setReactiveDirty(true);
  }

  function handleResetReactiveDraft(): void {
    if (!preview || !activeVariant) {
      return;
    }
    setReactiveDraft(formatJson(buildReactiveDraft(preview, activeVariant.index)));
    setReactiveDirty(false);
  }

  function updateInspectorTool(tool: InspectorToolName): void {
    setInspectorTool(tool);
    setInspectorDirty(false);
    setInspectorInput(
      formatJson(
        buildInspectorInput(
          tool,
          preview,
          activeVariant,
          lastPreviewInput,
          reactiveDraft,
          plannerApplyTimeline,
          plannerOverwriteTimeline,
          reactiveOverwriteMotionTrack,
          reactiveOverwriteCamera,
        ),
      ),
    );
  }

  function resetInspectorInput(): void {
    setInspectorDirty(false);
    setInspectorInput(
      formatJson(
        buildInspectorInput(
          inspectorTool,
          preview,
          activeVariant,
          lastPreviewInput,
          reactiveDraft,
          plannerApplyTimeline,
          plannerOverwriteTimeline,
          reactiveOverwriteMotionTrack,
          reactiveOverwriteCamera,
        ),
      ),
    );
  }

  async function runInspectorTool(): Promise<void> {
    if (!host()?.callTool) {
      setError({
        title: "Widget bridge unavailable",
        detail: "The ChatGPT host bridge is unavailable.",
      });
      return;
    }

    setInspectorBusy(true);
    setError(null);

    try {
      const parsedArgs = asRecord(JSON.parse(inspectorInput));
      const result = await host()!.callTool!(inspectorTool, parsedArgs);
      const normalized = readToolOutput(result);
      setInspectorOutput(formatJson(result));

      if (isBackendStatus(normalized)) {
        setBackendStatus(normalized);
      }

      const updatesMainOutput = INSPECTOR_TOOL_OPTIONS.find(
        (option) => option.name === inspectorTool,
      )?.updatesMainOutput;

      if (updatesMainOutput) {
        setOutput(normalized);
        if (isPlanPreview(normalized)) {
          setLastPreview(normalized);
          setLastPreviewInput(parsedArgs);
          setSelectedVariantIndex(
            normalized.selectedVariantIndex ?? normalized.variants[0]?.index ?? 0,
          );
          setReactiveDirty(false);
        }
      }
    } catch (reason) {
      const friendly = buildFriendlyError(reason, "Tool harness call failed.");
      setError(friendly);
      setInspectorOutput(
        formatJson({
          error: friendly,
        }),
      );
      if (friendly.title === "Backend unavailable") {
        void refreshBackendStatus(true);
      }
    } finally {
      setInspectorBusy(false);
    }
  }

  async function handleAskAssistant(): Promise<void> {
    if (!host()?.sendFollowUpMessage) {
      setError({
        title: "Follow-up messaging unavailable",
        detail: "Follow-up messaging is unavailable in this host.",
      });
      return;
    }

    setError(null);
    try {
      const variantPrompt = activeVariant
        ? `Focus on ${activeVariant.label} while comparing it to the other variants.`
        : "Compare the current EDMG variants.";
      const prompt =
        `${variantPrompt} Recommend the strongest direction, call out continuity risks, and ` +
        "suggest whether I should import planner state, reactive handoff, or both before committing the timeline.";

      const sender = host()!.sendFollowUpMessage!;
      if (typeof sender === "function") {
        try {
          await sender({ prompt, scrollToBottom: true } as never);
        } catch {
          await sender(prompt as never);
        }
      }
    } catch (reason) {
      setError(buildFriendlyError(reason, "Could not send the follow-up prompt."));
    }
  }

  async function handleFullscreen(): Promise<void> {
    try {
      await host()?.requestDisplayMode?.({ mode: "fullscreen" });
    } catch {
      // Layout requests are best-effort only.
    }
  }

  if (!preview && !operationResult) {
    return <FallbackView raw={output} />;
  }

  return (
    <main className="shell">
      {backendStatus && !backendStatus.available ? (
        <StatusBanner tone="warning" title="Backend unavailable" detail={backendStatus.detail} />
      ) : null}
      {operationResult ? <ResultView result={operationResult} onAskAssistant={handleAskAssistant} /> : null}
      {error ? <StatusBanner tone="error" title={error.title} detail={error.detail} /> : null}
      {preview ? (
        <>
          <Hero
            projectName={preview.projectName}
            mode={preview.mode}
            source={preview.planSource}
            variantCount={preview.variants.length}
            onAskAssistant={handleAskAssistant}
            onFullscreen={handleFullscreen}
          />
          <AnalysisPanel summary={preview.analysisSummary} />
          <HandoffPanel
            activeVariant={activeVariant}
            plannerBusy={plannerBusy}
            reactiveBusy={reactiveBusy}
            plannerApplyTimeline={plannerApplyTimeline}
            plannerOverwriteTimeline={plannerOverwriteTimeline}
            reactiveOverwriteMotionTrack={reactiveOverwriteMotionTrack}
            reactiveOverwriteCamera={reactiveOverwriteCamera}
            reactiveDraft={reactiveDraft}
            reactiveDirty={reactiveDirty}
            onPlannerApplyTimelineChange={setPlannerApplyTimeline}
            onPlannerOverwriteTimelineChange={setPlannerOverwriteTimeline}
            onReactiveOverwriteMotionTrackChange={setReactiveOverwriteMotionTrack}
            onReactiveOverwriteCameraChange={setReactiveOverwriteCamera}
            onReactiveDraftChange={handleReactiveDraftChange}
            onResetReactiveDraft={handleResetReactiveDraft}
            onImportPlanner={handleImportPlanner}
            onApplyReactive={handleApplyReactive}
          />
          <InspectorPanel
            backendStatus={backendStatus}
            backendBusy={backendBusy}
            bridgeAvailable={bridgeAvailable}
            preview={preview}
            activeVariant={activeVariant}
            selectedTool={inspectorTool}
            toolBusy={inspectorBusy}
            toolInput={inspectorInput}
            toolOutput={inspectorOutput}
            toolDirty={inspectorDirty}
            onRefreshBackend={() => refreshBackendStatus(false)}
            onToolChange={updateInspectorTool}
            onToolInputChange={(value) => {
              setInspectorInput(value);
              setInspectorDirty(true);
            }}
            onResetToolInput={resetInspectorInput}
            onRunTool={runInspectorTool}
          />
          <section className="variant-grid">
            {preview.variants.map((variant) => (
              <VariantCard
                key={variant.index}
                variant={variant}
                busy={busyVariantIndex === variant.index}
                isWorkingVariant={activeVariant?.index === variant.index}
                onApply={() => handleApplyVariant(variant.index)}
                onSelect={() => setSelectedVariantIndex(variant.index)}
              />
            ))}
          </section>
        </>
      ) : null}
    </main>
  );
}
