import type { StudioForgeRecipe } from "./types";

export const STUDIO_FORGE_RECIPES: StudioForgeRecipe[] = [
  {
    id: "audio-analysis-plan-render-assemble",
    name: "Audio -> Analysis -> AI Plan -> Render -> Assemble",
    description: "Tracks the default NVIDIA Studio path from source audio to OpenUSD scene plan, RTX render job, and finished video assembly.",
    stages: ["Audio ingest", "Analysis", "NIM planner pass", "OpenUSD handoff", "RTX render job", "FFmpeg assembly"],
    requiredCapabilities: ["backend", "internalRenderer", "ffmpeg"],
    optionalCapabilities: ["ollama", "edmgCore"],
    destructive: false,
    status: "preview",
  },
  {
    id: "still-image-workflow",
    name: "Still Image Workflow",
    description: "Preview a still-oriented recipe that starts from the NVIDIA plan and can branch to secondary ComfyUI-backed workflows.",
    stages: ["Project context", "NVIDIA prompt design", "OpenUSD shot context", "Still fallback render", "Review outputs"],
    requiredCapabilities: ["backend", "internalRenderer"],
    optionalCapabilities: ["comfyui", "ollama"],
    destructive: false,
    status: "preview",
  },
  {
    id: "motion-workflow",
    name: "Motion Workflow",
    description: "Preview the steps for scene motion rendering with NVIDIA/OpenUSD first and ComfyUI motion as a fallback branch.",
    stages: ["Plan NVIDIA scenes", "Pick RTX or fallback engine", "Queue clips", "Review queue", "Assemble video"],
    requiredCapabilities: ["backend", "ffmpeg"],
    optionalCapabilities: ["comfyui", "internalRenderer"],
    destructive: false,
    status: "preview",
  },
  {
    id: "deforum-export-workflow",
    name: "Deforum Export Workflow",
    description: "Preview how canonical NVIDIA Studio plans can end in a Deforum export without changing runtime defaults.",
    stages: ["Plan NVIDIA variants", "Creative direction", "Deforum settings preview", "Export JSON"],
    requiredCapabilities: ["backend"],
    optionalCapabilities: ["edmgCore", "ffmpeg"],
    destructive: false,
    status: "preview",
  },
];
