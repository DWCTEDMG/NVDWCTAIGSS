import type { StudioForgeRecipe } from "./types";

export const STUDIO_FORGE_RECIPES: StudioForgeRecipe[] = [
  {
    id: "audio-analysis-plan-render-assemble",
    name: "Audio -> Analysis -> AI Plan -> Render -> Assemble",
    description: "Tracks the default NVIDIA Studio path from source audio to OpenUSD scene plan, RTX render job, and finished video assembly.",
    stages: ["Audio ingest", "Analysis", "NIM planner pass", "OpenUSD handoff", "RTX render job", "FFmpeg assembly"],
    requiredCapabilities: ["backend", "nvidiaNim", "nvidiaKit", "ffmpeg"],
    optionalCapabilities: ["nvidiaRiva", "edmgCore"],
    destructive: false,
    status: "preview",
  },
  {
    id: "still-image-workflow",
    name: "Still Image Workflow",
    description: "Preview a still-oriented NVIDIA recipe that starts from the NIM plan and exports OpenUSD shot context for RTX rendering.",
    stages: ["Project context", "NIM prompt design", "OpenUSD shot context", "RTX still render", "Review outputs"],
    requiredCapabilities: ["backend", "nvidiaNim", "nvidiaKit"],
    optionalCapabilities: ["nvidiaOmniverse", "ffmpeg"],
    destructive: false,
    status: "preview",
  },
  {
    id: "motion-workflow",
    name: "Motion Workflow",
    description: "Preview the steps for scene motion rendering with NVIDIA/OpenUSD, RTX render jobs, and optional Triton/Cosmos service calls.",
    stages: ["Plan NVIDIA scenes", "Queue RTX jobs", "Invoke service branches", "Review queue", "Assemble video"],
    requiredCapabilities: ["backend", "nvidiaKit", "ffmpeg"],
    optionalCapabilities: ["nvidiaTriton", "nvidiaOmniverse"],
    destructive: false,
    status: "preview",
  },
  {
    id: "deforum-export-workflow",
    name: "Deforum Export Workflow",
    description: "Preview how canonical NVIDIA Studio plans can export legacy Deforum settings without changing runtime defaults.",
    stages: ["Plan NVIDIA variants", "Creative direction", "Deforum settings preview", "Export JSON"],
    requiredCapabilities: ["backend", "nvidiaNim"],
    optionalCapabilities: ["edmgCore", "ffmpeg"],
    destructive: false,
    status: "preview",
  },
];
