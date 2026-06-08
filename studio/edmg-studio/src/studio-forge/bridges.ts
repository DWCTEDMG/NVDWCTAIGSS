import type { StudioForgeBridge } from "./types";

export const STUDIO_FORGE_BRIDGES: StudioForgeBridge[] = [
  {
    id: "unreal-shot-metadata-export",
    name: "Unreal Shot Metadata Export",
    kind: "metadataExport",
    description:
      "Preview a non-destructive export that packages scene timing, prompt context, continuity notes, and shot metadata for Unreal ingest tooling.",
    transports: ["fileExport"],
    outputs: [
      "Scene + shot manifest JSON",
      "Prompt and continuity packet",
      "Sequencer-friendly timing markers",
    ],
    previewPayload: {
      engine: "unreal",
      handoff_kind: "shot_metadata_export",
      sequence_name: "EDMG_MainSequence",
      project_fields: ["project_id", "project_name", "fps", "audio_path"],
      shot_fields: ["shot_id", "scene_id", "start_frame", "end_frame", "prompt", "continuity_tags"],
      marker_fields: ["label", "frame", "time_seconds"],
    },
    limitations:
      "Preview only. Studio Forge does not write Unreal project files or require an Unreal plugin in this phase.",
    requiredCapabilities: ["backend"],
    optionalCapabilities: ["edmgCore"],
    destructive: false,
    status: "preview",
  },
  {
    id: "unreal-render-handoff",
    name: "Unreal Render Handoff",
    kind: "renderHandoff",
    description:
      "Preview an external render target where NVIDIA Studio stays the planner and assembly owner while Unreal becomes a secondary execution endpoint.",
    transports: ["fileExport", "http"],
    outputs: [
      "Render intent manifest",
      "Audio stem and marker map",
      "Assembly contract for the return path",
    ],
    previewPayload: {
      engine: "unreal",
      handoff_kind: "render_handoff",
      execution_owner: "external_runtime",
      return_owner: "studio",
      expected_inputs: ["shot_manifest.json", "audio_markers.json", "style_packet.json"],
      expected_outputs: ["shot_render.mov", "alpha_pass.mov", "metadata.json"],
      assembly_mode: "ffmpeg_back_in_studio",
    },
    limitations:
      "Preview only. No Unreal render job is launched from Studio Forge, and NVIDIA/OpenUSD remains the default runtime.",
    requiredCapabilities: ["backend", "ffmpeg"],
    optionalCapabilities: ["internalRenderer", "edmgCore"],
    destructive: false,
    status: "preview",
  },
  {
    id: "unreal-live-control-bridge",
    name: "Unreal Live Control Bridge",
    kind: "controlBridge",
    description:
      "Preview section-aware control outputs that could drive Unreal scenes over OSC, WebSocket, or Remote Control without making Unreal part of Studio startup.",
    transports: ["websocket", "osc", "remoteControl"],
    outputs: [
      "Section and cue events",
      "Beat, onset, and envelope streams",
      "Camera and lighting control hints",
    ],
    previewPayload: {
      engine: "unreal",
      handoff_kind: "live_control_bridge",
      transports: {
        osc: ["/edmg/section", "/edmg/beat", "/edmg/camera"],
        websocket: ["section_change", "beat_pulse", "lighting_envelope"],
        remote_control: ["sequence.PlayRate", "camera.FocalLength", "lights.Intensity"],
      },
      cadence_hz: 30,
      section_payload_fields: ["section_id", "energy", "continuity_priority"],
    },
    limitations:
      "Preview only. Studio Forge does not open live control sockets or bind directly to Unreal in the current phase.",
    requiredCapabilities: ["backend"],
    optionalCapabilities: ["ollama", "ffmpeg"],
    destructive: false,
    status: "preview",
  },
];
