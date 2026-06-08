import { isStudioForgeEnabled } from "./features";

export type Page =
  | "dashboard"
  | "projects"
  | "workspace"
  | "timeline"
  | "render"
  | "queue"
  | "outputs"
  | "cloud"
  | "settings"
  | "setup"
  | "models"
  | "directorLab"
  | "plannerLab"
  | "reactiveLab"
  | "studioForge";

const PAGE_LABELS: Record<Page, string> = {
  dashboard: "Dashboard",
  projects: "Projects",
  workspace: "Workspace",
  timeline: "Timeline",
  render: "Render",
  queue: "Render Queue",
  outputs: "Outputs",
  cloud: "Cloud",
  settings: "Settings",
  setup: "NVIDIA Runtime Setup",
  models: "Models",
  directorLab: "EDMG Director",
  plannerLab: "AI Planner Lab",
  reactiveLab: "Reactive Lab",
  studioForge: "Studio Forge",
};

const PAGE_LOADING_DETAILS: Record<Page, string> = {
  dashboard: "Refreshing status cards and studio overview context.",
  projects: "Loading project cards and recent session context.",
  workspace: "Preparing ingest, planning, and creative direction controls.",
  timeline: "Preparing transport, cue, and arrangement controls.",
  render: "Loading render presets, engines, and output actions.",
  queue: "Loading queue state, progress, and retry controls.",
  outputs: "Loading generated media, active jobs, and review actions.",
  cloud: "Loading remote integration settings and bundle actions.",
  settings: "Loading desktop backend, storage, and preference controls.",
  setup: "Loading NVIDIA runtime health, installers, and dependency checks.",
  models: "Loading model packs, availability, and install actions.",
  directorLab: "Preparing combined planning, reactive, and optional ChatGPT handoff controls.",
  plannerLab: "Preparing AI planning tools and Studio handoff controls.",
  reactiveLab: "Preparing audio-reactive scheduling and handoff controls.",
  studioForge: "Preparing runtime preview cards and builder recipe registries.",
};

const PAGE_LOADERS: Partial<Record<Page, () => Promise<unknown>>> = {
  workspace: () => import("./pages/Workspace"),
  timeline: () => import("./pages/Timeline"),
  render: () => import("./pages/Render"),
  queue: () => import("./pages/RenderQueue"),
  outputs: () => import("./pages/Outputs"),
  cloud: () => import("./pages/Cloud"),
  settings: () => import("./pages/Settings"),
  models: () => import("./pages/Models"),
  directorLab: () => import("./pages/EdmgDirector"),
  plannerLab: () => import("./pages/AiPlannerLab"),
  reactiveLab: () => import("./pages/ReactiveLab"),
  studioForge: () => import("./pages/StudioForge"),
};

const PRELOAD_BY_PAGE: Record<Page, Page[]> = {
  dashboard: ["projects", "workspace", "setup"],
  projects: ["workspace", "dashboard"],
  workspace: ["timeline", "render", "directorLab", "plannerLab", "reactiveLab"],
  timeline: ["render", "outputs", "workspace"],
  render: ["queue", "outputs", "timeline"],
  queue: ["outputs", "render"],
  outputs: ["render", "queue"],
  cloud: ["settings", "models"],
  settings: ["setup", "cloud", "models"],
  setup: ["workspace", "models", "settings"],
  models: ["render", "setup", "workspace"],
  directorLab: ["workspace", "timeline", "render"],
  plannerLab: ["workspace", "timeline", "render"],
  reactiveLab: ["workspace", "timeline", "outputs"],
  studioForge: ["setup", "models", "render"],
};

const BASE_PAGES: Page[] = [
  "dashboard",
  "projects",
  "workspace",
  "timeline",
  "render",
  "queue",
  "outputs",
  "cloud",
  "settings",
  "setup",
  "models",
  "directorLab",
  "plannerLab",
  "reactiveLab",
];

export function getAllowedPages(): Page[] {
  return isStudioForgeEnabled() ? [...BASE_PAGES, "studioForge"] : BASE_PAGES;
}

export function isPage(value: string): value is Page {
  return getAllowedPages().includes(value as Page);
}

export function getPageLoadingDetails(page: Page): { label: string; detail: string } {
  return { label: PAGE_LABELS[page], detail: PAGE_LOADING_DETAILS[page] };
}

export function getPagesToPreload(page: Page): Page[] {
  return PRELOAD_BY_PAGE[page];
}

function filterPreloadCandidates(pages: Page[]): Page[] {
  return [...new Set(pages)].filter(
    (candidate) => candidate !== "studioForge" || isStudioForgeEnabled(),
  );
}

function preloadPages(pages: Page[]) {
  filterPreloadCandidates(pages).forEach((candidate) => {
    void PAGE_LOADERS[candidate]?.();
  });
}

export function preloadLikelyNextPages(page: Page) {
  preloadPages(getPagesToPreload(page).filter((candidate) => candidate !== page));
}

export function preloadNavigationIntent(page: Page) {
  preloadPages([page, ...getPagesToPreload(page)]);
}
