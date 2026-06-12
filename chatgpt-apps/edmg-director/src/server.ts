import {
  registerAppResource,
  registerAppTool,
  RESOURCE_MIME_TYPE,
} from "@modelcontextprotocol/ext-apps/server";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import cors from "cors";
import express, { type Request, type Response } from "express";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { z } from "zod";

const APP_NAME = "edmg-director";
const SERVER_VERSION = "0.2.0";
const PORT = Number.parseInt(process.env.PORT ?? "3001", 10);
const BIND_HOST = String(process.env.HOST ?? "127.0.0.1");
const EDMG_BASE_URL = String(process.env.EDMG_BASE_URL ?? "http://127.0.0.1:7863").replace(/\/+$/, "");
const REVIEW_WIDGET_URI = "ui://edmg-director/review-board.html";
const REVIEW_WIDGET_DESCRIPTION =
  "Interactive review board for EDMG storyboard variants. Inspect scenes, compare directions, and apply the chosen variant to the Workshop timeline.";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const ASSETS_DIR = path.resolve(ROOT_DIR, "assets");

export type ServerConfig = {
  port: number;
  bindHost: string;
  edmgBaseUrl: string;
  publicBaseUrl: string;
  assetsDir: string;
};

type AnyRecord = Record<string, unknown>;
type ProjectSearchResult = {
  id: string;
  name: string;
  createdAt: number | null;
  updatedAt: number | null;
};
type ProjectFetchOutput = {
  type: "project-detail";
  projectId: string;
  projectName: string;
  analysisSummary: AnalysisSummary | null;
  timelineSummary: TimelineSummary | null;
  variantCount: number;
};
type AnalysisSummary = {
  bpm: number | null;
  durationS: number | null;
  hookLine: string | null;
  narrative: string | null;
};
type PlanScene = {
  index: number;
  title: string;
  prompt: string;
  startS: number | null;
  endS: number | null;
  durationS: number | null;
  shotType: string | null;
  rationale: string | null;
  transitionCue: string | null;
  continuityNote: string | null;
};
type PlanVariant = {
  index: number;
  label: string;
  summary: string | null;
  durationS: number | null;
  scenes: PlanScene[];
};
type PlanPreviewOutput = {
  type: "plan-preview";
  projectId: string;
  projectName: string;
  mode: string;
  planSource: string | null;
  selectedVariantIndex: number;
  analysisSummary: AnalysisSummary | null;
  variants: PlanVariant[];
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

const jsonObjectSchema = z.record(z.string(), z.unknown());
const jsonObjectArraySchema = z.array(jsonObjectSchema);

export function resolveServerConfig(
  env: NodeJS.ProcessEnv = process.env,
  overrides: Partial<ServerConfig> = {},
): ServerConfig {
  const port = Number.parseInt(env.PORT ?? "3001", 10);
  return {
    port,
    bindHost: String(env.HOST ?? "127.0.0.1"),
    edmgBaseUrl: String(env.EDMG_BASE_URL ?? "http://127.0.0.1:7863").replace(/\/+$/, ""),
    publicBaseUrl: String(env.BASE_URL ?? `http://localhost:${port}`).replace(/\/+$/, ""),
    assetsDir: ASSETS_DIR,
    ...overrides,
  };
}

function allowedHosts(config: ServerConfig): string[] | undefined {
  if (
    config.bindHost === "127.0.0.1" ||
    config.bindHost === "localhost" ||
    config.bindHost === "::1"
  ) {
    return undefined;
  }

  const hosts = new Set<string>(["localhost", "127.0.0.1", "[::1]"]);
  try {
    hosts.add(new URL(config.publicBaseUrl).hostname);
  } catch {
    // Ignore invalid BASE_URL values here; startup will still expose them via GET /.
  }
  return [...hosts];
}

function asRecord(value: unknown): AnyRecord {
  return value && typeof value === "object" ? (value as AnyRecord) : {};
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

function readBuiltWidgetHtml(fileName: string, assetsDir: string): string {
  const filePath = path.join(assetsDir, fileName);
  if (!fs.existsSync(filePath)) {
    throw new Error(
      `Widget asset "${fileName}" is missing. Run "pnpm run build" in ${ROOT_DIR} before starting the MCP server.`,
    );
  }
  return fs.readFileSync(filePath, "utf-8");
}

function materializeWidgetHtml(fileName: string, config: ServerConfig): string {
  const assetOrigin = `${config.publicBaseUrl}/assets/`;
  return readBuiltWidgetHtml(fileName, config.assetsDir).replace(
    /(src|href)=["']\.\/([^"']+)["']/g,
    (_match, attr: string, file: string) => `${attr}="${assetOrigin}${file}"`,
  );
}

function extractErrorMessage(payload: unknown, fallback: string): string {
  const record = asRecord(payload);
  const error = asRecord(record.error);
  return (
    asString(error.message) ||
    asString(record.detail) ||
    asString(record.message) ||
    fallback
  );
}

async function requestJson<T = unknown>(
  resourcePath: string,
  edmgBaseUrl: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  headers.set("Accept", "application/json");
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: globalThis.Response;
  try {
    response = await fetch(`${edmgBaseUrl}${resourcePath}`, {
      ...init,
      headers,
    });
  } catch (error) {
    const detail =
      error instanceof Error && error.message
        ? error.message
        : "Unknown connection failure";
    throw new Error(`Could not reach EDMG backend at ${edmgBaseUrl}. ${detail}`);
  }

  const text = await response.text();
  let payload: unknown = null;

  if (text.trim().length > 0) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    throw new Error(
      extractErrorMessage(payload, `${response.status} ${response.statusText}`),
    );
  }

  return payload as T;
}

function summarizeAnalysis(analysisValue: unknown): AnalysisSummary | null {
  const analysis = asRecord(analysisValue);
  const features = asRecord(analysis.features);
  const bpm =
    coerceNumber(features.bpm) ??
    coerceNumber(features.tempo_bpm) ??
    coerceNumber(features.tempo);
  const durationS =
    coerceNumber(analysis.duration_s) ??
    coerceNumber(features.duration_s) ??
    coerceNumber(features.duration);
  const hookLine =
    asString(analysis.hook_line) ||
    asString(analysis.hookLine) ||
    null;
  const narrative =
    asString(analysis.narrative_structure) ||
    asString(analysis.narrativeStructure) ||
    null;

  if (bpm === null && durationS === null && !hookLine && !narrative) {
    return null;
  }

  return {
    bpm,
    durationS,
    hookLine,
    narrative,
  };
}

function summarizeTimeline(timelineValue: unknown): TimelineSummary | null {
  const timeline = asRecord(timelineValue);
  const rootKeys = Object.keys(timeline);
  if (!rootKeys.length) {
    return null;
  }

  const tracksValue = timeline.tracks;
  let trackCount = 0;
  if (Array.isArray(tracksValue)) {
    trackCount = tracksValue.length;
  } else if (tracksValue && typeof tracksValue === "object") {
    trackCount = Object.keys(tracksValue as AnyRecord).length;
  }

  return {
    rootKeys,
    trackCount,
  };
}

function normalizeScene(sceneValue: unknown, index: number): PlanScene {
  const scene = asRecord(sceneValue);
  const startS =
    coerceNumber(scene.start_s) ??
    coerceNumber(scene.start) ??
    coerceNumber(scene.startSeconds);
  const endS =
    coerceNumber(scene.end_s) ??
    coerceNumber(scene.end) ??
    coerceNumber(scene.endSeconds);
  const durationS =
    coerceNumber(scene.duration_s) ??
    (startS !== null && endS !== null ? Math.max(0, endS - startS) : null);

  return {
    index,
    title:
      asString(scene.title) ||
      asString(scene.label) ||
      asString(scene.name) ||
      `Scene ${index + 1}`,
    prompt:
      asString(scene.prompt) ||
      asString(scene.visual_prompt) ||
      asString(scene.text) ||
      asString(scene.description),
    startS,
    endS,
    durationS,
    shotType:
      asString(scene.shot_type) ||
      asString(scene.shotType) ||
      asString(scene.camera) ||
      null,
    rationale:
      asString(scene.rationale) ||
      asString(scene.reason) ||
      asString(scene.intent) ||
      null,
    transitionCue:
      asString(scene.transition_cue) ||
      asString(scene.transitionCue) ||
      asString(scene.transition) ||
      null,
    continuityNote:
      asString(scene.continuity_note) ||
      asString(scene.continuityNote) ||
      asString(scene.continuity) ||
      null,
  };
}

function normalizeVariant(variantValue: unknown, index: number): PlanVariant {
  const variant = asRecord(variantValue);
  const scenes = asArray(variant.scenes).map((scene, sceneIndex) =>
    normalizeScene(scene, sceneIndex),
  );

  return {
    index,
    label:
      asString(variant.title) ||
      asString(variant.label) ||
      asString(variant.name) ||
      `Variant ${index + 1}`,
    summary:
      asString(variant.summary) ||
      asString(variant.description) ||
      asString(variant.logline) ||
      null,
    durationS:
      coerceNumber(variant.duration_s) ??
      coerceNumber(variant.duration) ??
      null,
    scenes,
  };
}

function normalizePlanPreview(
  projectId: string,
  projectName: string,
  mode: string,
  analysisValue: unknown,
  planValue: unknown,
): PlanPreviewOutput {
  const plan = asRecord(planValue);
  const variants = asArray(plan.variants).map((variant, index) =>
    normalizeVariant(variant, index),
  );

  return {
    type: "plan-preview",
    projectId,
    projectName,
    mode,
    planSource: asString(plan.source) || null,
    selectedVariantIndex: 0,
    analysisSummary: summarizeAnalysis(analysisValue),
    variants,
  };
}

function describeSearchResults(results: ProjectSearchResult[]): string {
  if (!results.length) {
    return "No EDMG NVIDIA Workshop projects matched the search.";
  }
  return results
    .map((project) => `- ${project.name} (${project.id})`)
    .join("\n");
}

function textContent(text: string) {
  return {
    type: "text" as const,
    text,
  };
}

export function createServer(config: ServerConfig = resolveServerConfig()): McpServer {
  const server = new McpServer({
    name: APP_NAME,
    version: SERVER_VERSION,
  });
  const requestJsonForBackend = <T = unknown>(resourcePath: string, init?: RequestInit) =>
    requestJson<T>(resourcePath, config.edmgBaseUrl, init);

  registerAppResource(
    server,
    "EDMG Director Review Board",
    REVIEW_WIDGET_URI,
    {
      mimeType: RESOURCE_MIME_TYPE,
      description: REVIEW_WIDGET_DESCRIPTION,
    },
    async () => ({
      contents: [
        {
          uri: REVIEW_WIDGET_URI,
          mimeType: RESOURCE_MIME_TYPE,
          text: materializeWidgetHtml("review-board.html", config),
          _meta: {
            ui: {
              prefersBorder: true,
              csp: {
                connectDomains: [config.edmgBaseUrl],
                resourceDomains: [config.publicBaseUrl],
              },
            },
            "openai/widgetDescription": REVIEW_WIDGET_DESCRIPTION,
            "openai/widgetPrefersBorder": true,
          },
        },
      ],
    }),
  );

  registerAppTool(
    server,
    "backend_status",
    {
      title: "Check EDMG backend status",
      description:
        "Use this when you need to verify whether the EDMG NVIDIA Workshop backend is reachable before running project tools.",
      inputSchema: {},
      annotations: {
        readOnlyHint: true,
      },
      _meta: {
        "openai/toolInvocation/invoking": "Checking EDMG backend",
        "openai/toolInvocation/invoked": "Backend status ready",
      },
    },
    async () => {
      try {
        const payload = asRecord(await requestJsonForBackend("/health"));
        const structuredContent: BackendStatusOutput = {
          type: "backend-status",
          available: payload.ok === true,
          baseUrl: config.edmgBaseUrl,
          version: asString(payload.version) || null,
          detail:
            payload.ok === true
              ? `Connected to EDMG backend at ${config.edmgBaseUrl}.`
              : `EDMG backend at ${config.edmgBaseUrl} responded without an OK status.`,
          checkedAt: new Date().toISOString(),
        };

        return {
          content: [textContent(structuredContent.detail)],
          structuredContent,
        };
      } catch (error) {
        const structuredContent: BackendStatusOutput = {
          type: "backend-status",
          available: false,
          baseUrl: config.edmgBaseUrl,
          version: null,
          detail:
            error instanceof Error
              ? error.message
              : `Could not reach EDMG backend at ${config.edmgBaseUrl}.`,
          checkedAt: new Date().toISOString(),
        };

        return {
          content: [textContent(structuredContent.detail)],
          structuredContent,
        };
      }
    },
  );

  registerAppTool(
    server,
    "search",
    {
      title: "Search EDMG projects",
      description:
        "Use this when you need to find an EDMG NVIDIA Workshop project by name or ID before inspecting or planning it.",
      inputSchema: {
        query: z.string().optional(),
        limit: z.number().int().min(1).max(20).optional(),
      },
      annotations: {
        readOnlyHint: true,
      },
      _meta: {
        "openai/toolInvocation/invoking": "Searching EDMG projects",
        "openai/toolInvocation/invoked": "Project search ready",
      },
    },
    async (input: AnyRecord) => {
      const query = asString(input.query).trim().toLowerCase();
      const limit = Math.min(
        Math.max(Math.trunc(coerceNumber(input.limit) ?? 8), 1),
        20,
      );

      const payload = asRecord(await requestJsonForBackend("/v1/projects"));
      const projects = asArray(payload.projects)
        .map((entry) => asRecord(entry))
        .map<ProjectSearchResult>((project) => ({
          id: asString(project.id),
          name: asString(project.name) || asString(project.id),
          createdAt: coerceNumber(project.created_at) ?? coerceNumber(project.createdAt),
          updatedAt: coerceNumber(project.updated_at) ?? coerceNumber(project.updatedAt),
        }))
        .filter((project) => {
          if (!query) {
            return true;
          }
          return (
            project.id.toLowerCase().includes(query) ||
            project.name.toLowerCase().includes(query)
          );
        })
        .slice(0, limit);

      return {
        content: [textContent(describeSearchResults(projects))],
        structuredContent: {
          type: "project-search-results",
          query,
          results: projects,
        },
      };
    },
  );

  registerAppTool(
    server,
    "fetch",
    {
      title: "Fetch EDMG project",
      description:
        "Use this when you already know the EDMG project ID and need its current analysis, plan, and timeline context.",
      inputSchema: {
        projectId: z.string().min(1),
      },
      annotations: {
        readOnlyHint: true,
      },
      _meta: {
        "openai/toolInvocation/invoking": "Loading EDMG project",
        "openai/toolInvocation/invoked": "Project loaded",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const payload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}`),
      );
      const project = asRecord(payload.project);
      const meta = asRecord(project.meta);
      const variants = asArray(asRecord(meta.last_plan).variants);

      const structuredContent: ProjectFetchOutput = {
        type: "project-detail",
        projectId,
        projectName: asString(project.name) || projectId,
        analysisSummary: summarizeAnalysis(meta.analysis),
        timelineSummary: summarizeTimeline(meta.timeline),
        variantCount: variants.length,
      };

      return {
        content: [
          textContent(
            `${structuredContent.projectName} has ${structuredContent.variantCount} stored plan ` +
              `variant${structuredContent.variantCount === 1 ? "" : "s"}.`,
          ),
        ],
        structuredContent,
      };
    },
  );

  registerAppTool(
    server,
    "analyze_project_audio",
    {
      title: "Analyze EDMG project audio",
      description:
        "Use this when a project already has audio attached and you want the EDMG backend to extract fresh music-analysis context before planning.",
      inputSchema: {
        projectId: z.string().min(1),
      },
      _meta: {
        "openai/toolInvocation/invoking": "Analyzing project audio",
        "openai/toolInvocation/invoked": "Audio analysis ready",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const payload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}/analyze_audio`, {
          method: "POST",
        }),
      );
      const analysisSummary = summarizeAnalysis(payload.analysis);

      return {
        content: [
          textContent(
            analysisSummary
              ? `Analysis ready for ${projectId}: ${analysisSummary.bpm ?? "?"} BPM, ` +
                  `${analysisSummary.durationS ?? "?"} seconds.`
              : `Analysis completed for ${projectId}.`,
          ),
        ],
        structuredContent: {
          type: "audio-analysis",
          projectId,
          analysisSummary,
        },
      };
    },
  );

  registerAppTool(
    server,
    "generate_plan_preview",
    {
      title: "Generate EDMG plan preview",
      description:
        "Use this when you want EDMG NVIDIA Workshop to generate storyboard variants and review them in an interactive board before writing anything into the timeline.",
      inputSchema: {
        projectId: z.string().min(1),
        mode: z.enum(["auto", "ai", "local", "edmg_core"]).optional(),
        title: z.string().optional(),
        userNotes: z.string().optional(),
        stylePrefs: z.string().optional(),
        numVariants: z.number().int().min(1).max(10).optional(),
        maxScenes: z.number().int().min(1).max(64).optional(),
      },
      _meta: {
        ui: {
          resourceUri: REVIEW_WIDGET_URI,
        },
        "openai/toolInvocation/invoking": "Generating EDMG variants",
        "openai/toolInvocation/invoked": "Review board ready",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const mode = asString(input.mode).trim() || "auto";

      const projectPayload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}`),
      );
      const project = asRecord(projectPayload.project);
      const meta = asRecord(project.meta);

      const requestBody = {
        title: asString(input.title) || undefined,
        user_notes: asString(input.userNotes) || undefined,
        style_prefs: asString(input.stylePrefs) || undefined,
        num_variants: Math.min(
          Math.max(Math.trunc(coerceNumber(input.numVariants) ?? 3), 1),
          10,
        ),
        max_scenes: Math.min(
          Math.max(Math.trunc(coerceNumber(input.maxScenes) ?? 12), 1),
          64,
        ),
      };

      const plan = await requestJsonForBackend(
        `/v1/projects/${encodeURIComponent(projectId)}/plan?mode=${encodeURIComponent(mode)}`,
        {
          method: "POST",
          body: JSON.stringify(requestBody),
        },
      );

      const preview = normalizePlanPreview(
        projectId,
        asString(project.name) || projectId,
        mode,
        meta.analysis,
        plan,
      );

      return {
        content: [
          textContent(
            `Prepared ${preview.variants.length} storyboard ` +
              `variant${preview.variants.length === 1 ? "" : "s"} for ${preview.projectName}. ` +
              "Review them in the board and apply the selected variant when ready.",
          ),
        ],
        structuredContent: preview,
      };
    },
  );

  registerAppTool(
    server,
    "apply_plan_variant",
    {
      title: "Apply EDMG plan variant",
      description:
        "Use this when a reviewer has chosen a storyboard variant and wants that variant written into the Workshop timeline.",
      inputSchema: {
        projectId: z.string().min(1),
        variantIndex: z.number().int().min(0),
        overwrite: z.boolean().optional(),
      },
      _meta: {
        ui: {
          visibility: ["app"],
        },
        "openai/toolInvocation/invoking": "Applying storyboard variant",
        "openai/toolInvocation/invoked": "Storyboard variant applied",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const variantIndex = Math.max(Math.trunc(coerceNumber(input.variantIndex) ?? 0), 0);
      const overwrite = input.overwrite === undefined ? true : Boolean(input.overwrite);

      const projectPayload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}`),
      );
      const project = asRecord(projectPayload.project);

      const payload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}/timeline/apply_plan`, {
          method: "POST",
          body: JSON.stringify({
            variant_index: variantIndex,
            overwrite,
          }),
        }),
      );

      const structuredContent: ActionResultOutput = {
        type: "action-result",
        projectId,
        projectName: asString(project.name) || projectId,
        variantIndex,
        overwrite,
        applied: payload.ok === true,
        message:
          payload.ok === true
            ? `Applied variant ${variantIndex + 1} to ${asString(project.name) || projectId}.`
            : `Variant ${variantIndex + 1} could not be applied.`,
        timelineSummary: summarizeTimeline(payload.timeline),
      };

      return {
        content: [textContent(structuredContent.message)],
        structuredContent,
      };
    },
  );

  registerAppTool(
    server,
    "import_planner_lab_payload",
    {
      title: "Import planner payload into EDMG",
      description:
        "Use this when you already have EDMG-style planner analysis, plan, and settings payloads and want to sync them into a Studio project.",
      inputSchema: {
        projectId: z.string().min(1),
        analysis: jsonObjectSchema.optional(),
        plan: jsonObjectSchema,
        settings: jsonObjectSchema.optional(),
        applyTimeline: z.boolean().optional(),
        overwriteTimeline: z.boolean().optional(),
      },
      _meta: {
        "openai/toolInvocation/invoking": "Importing planner payload",
        "openai/toolInvocation/invoked": "Planner payload imported",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const applyTimeline = input.applyTimeline === undefined ? true : Boolean(input.applyTimeline);
      const overwriteTimeline =
        input.overwriteTimeline === undefined ? true : Boolean(input.overwriteTimeline);

      const projectPayload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}`),
      );
      const project = asRecord(projectPayload.project);

      const payload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}/planner_lab/import`, {
          method: "POST",
          body: JSON.stringify({
            analysis: asRecord(input.analysis),
            plan: asRecord(input.plan),
            settings: asRecord(input.settings),
            apply_timeline: applyTimeline,
            overwrite_timeline: overwriteTimeline,
          }),
        }),
      );

      const variantCount = asArray(asRecord(payload.plan).variants).length;
      const structuredContent: PlannerImportResultOutput = {
        type: "planner-import-result",
        projectId,
        projectName: asString(project.name) || projectId,
        variantCount,
        appliedTimeline: payload.timeline !== null && payload.timeline !== undefined,
        timelineSummary: summarizeTimeline(payload.timeline),
        message:
          `Imported planner payload into ${asString(project.name) || projectId}` +
          (applyTimeline ? " and refreshed the Workshop timeline." : "."),
      };

      return {
        content: [textContent(structuredContent.message)],
        structuredContent,
      };
    },
  );

  registerAppTool(
    server,
    "apply_reactive_handoff",
    {
      title: "Apply reactive handoff into EDMG",
      description:
        "Use this when you have reactive cue events, schedules, and handoff metadata that should be merged into a Workshop timeline.",
      inputSchema: {
        projectId: z.string().min(1),
        metadata: jsonObjectSchema.optional(),
        keyframes: jsonObjectArraySchema.optional(),
        beatMarkers: jsonObjectArraySchema.optional(),
        cueEvents: jsonObjectArraySchema.optional(),
        sections: jsonObjectArraySchema.optional(),
        repairSuggestions: jsonObjectArraySchema.optional(),
        schedules: jsonObjectSchema.optional(),
        handoffManifest: jsonObjectSchema.optional(),
        overwriteMotionTrack: z.boolean().optional(),
        overwriteCamera: z.boolean().optional(),
      },
      _meta: {
        "openai/toolInvocation/invoking": "Applying reactive handoff",
        "openai/toolInvocation/invoked": "Reactive handoff applied",
      },
    },
    async (input: AnyRecord) => {
      const projectId = asString(input.projectId).trim();
      const overwriteMotionTrack =
        input.overwriteMotionTrack === undefined ? true : Boolean(input.overwriteMotionTrack);
      const overwriteCamera =
        input.overwriteCamera === undefined ? true : Boolean(input.overwriteCamera);

      const projectPayload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}`),
      );
      const project = asRecord(projectPayload.project);

      const keyframes = asArray(input.keyframes).map((item) => asRecord(item));
      const cueEvents = asArray(input.cueEvents).map((item) => asRecord(item));
      const sections = asArray(input.sections).map((item) => asRecord(item));

      const payload = asRecord(
        await requestJsonForBackend(`/v1/projects/${encodeURIComponent(projectId)}/reactive_lab/apply`, {
          method: "POST",
          body: JSON.stringify({
            metadata: asRecord(input.metadata),
            keyframes,
            beat_markers: asArray(input.beatMarkers).map((item) => asRecord(item)),
            cue_events: cueEvents,
            sections,
            repair_suggestions: asArray(input.repairSuggestions).map((item) => asRecord(item)),
            schedules: asRecord(input.schedules),
            handoff_manifest: asRecord(input.handoffManifest),
            overwrite_motion_track: overwriteMotionTrack,
            overwrite_camera: overwriteCamera,
          }),
        }),
      );

      const structuredContent: ReactiveApplyResultOutput = {
        type: "reactive-apply-result",
        projectId,
        projectName: asString(project.name) || projectId,
        cueEventCount: cueEvents.length,
        keyframeCount: keyframes.length,
        sectionCount: sections.length,
        timelineSummary: summarizeTimeline(payload.timeline),
        message:
          `Applied reactive handoff into ${asString(project.name) || projectId} ` +
          `with ${cueEvents.length} cue event${cueEvents.length === 1 ? "" : "s"}.`,
      };

      return {
        content: [textContent(structuredContent.message)],
        structuredContent,
      };
    },
  );

  return server;
}

export function createStreamableHttpApp(config: ServerConfig = resolveServerConfig()) {
  const app = createMcpExpressApp({
    host: config.bindHost,
    allowedHosts: allowedHosts(config),
  });
  app.use(
    cors({
      origin: "*",
      exposedHeaders: ["Mcp-Session-Id"],
    }),
  );
  app.use("/assets", express.static(config.assetsDir));

  app.get("/", (_req, res) => {
    res.json({
      ok: true,
      name: APP_NAME,
      version: SERVER_VERSION,
      host: config.bindHost,
      mcpPath: "/mcp",
      assetsPath: "/assets",
      publicBaseUrl: config.publicBaseUrl,
      edmgBaseUrl: config.edmgBaseUrl,
    });
  });

  app.all("/mcp", async (req: Request, res: Response) => {
    const server = createServer(config);
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });

    res.on("close", () => {
      transport.close().catch(() => {});
      server.close().catch(() => {});
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      console.error("MCP error:", error);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: {
            code: -32603,
            message: error instanceof Error ? error.message : "Internal server error",
          },
          id: null,
        });
      }
    }
  });
  return app;
}

async function startStreamableHttpServer(config: ServerConfig = resolveServerConfig()): Promise<void> {
  const app = createStreamableHttpApp(config);
  const httpServer = app.listen(config.port, config.bindHost, (error?: Error) => {
    if (error) {
      console.error("Failed to start server:", error);
      process.exit(1);
    }
    console.log(`EDMG Director listening on ${config.publicBaseUrl}`);
  });

  const shutdown = () => {
    httpServer.close(() => process.exit(0));
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

async function startStdioServer(config: ServerConfig = resolveServerConfig()): Promise<void> {
  await createServer(config).connect(new StdioServerTransport());
}

export async function main(
  argv: string[] = process.argv.slice(2),
  config: ServerConfig = resolveServerConfig(),
): Promise<void> {
  if (argv.includes("--stdio")) {
    await startStdioServer(config);
    return;
  }
  await startStreamableHttpServer(config);
}

function isDirectExecution(): boolean {
  const entrypoint = process.argv[1];
  if (!entrypoint) {
    return false;
  }
  return import.meta.url === pathToFileURL(entrypoint).href;
}

if (isDirectExecution()) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
