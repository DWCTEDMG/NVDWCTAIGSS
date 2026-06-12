import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import App from "../App";
import StudioForge from "../pages/StudioForge";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

const ORIGINAL_STUDIO_FORGE_FLAG = import.meta.env.VITE_EDMG_ENABLE_STUDIO_FORGE;

const READY_STATUS = {
  comfyui: {
    ok: false,
    url: "http://127.0.0.1:8188",
  },
  ffmpeg: {
    ok: true,
    path: "ffmpeg",
  },
  backend_bundle: {
    ok: true,
  },
  edmg: {
    available: true,
  },
  ai_config: {
    label: "NVIDIA NIM / OpenAI-compatible provider",
    provider: "openai_compat",
    base_url: "http://127.0.0.1:8001/v1",
    model: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  },
  nvidia_profile: {
    enabled: true,
    profile: "omniverse",
    services: {
      nim: { configured: true, base_url: "http://127.0.0.1:8001/v1", model: "nvidia/llama-3.3-nemotron-super-49b-v1.5" },
      riva: { configured: true, base_url: "127.0.0.1:50051" },
      triton: { configured: true, base_url: "http://127.0.0.1:8002", model: "edmg_ensemble" },
      omniverse: { configured: true, base_url: "http://127.0.0.1:8011" },
    },
  },
};

const FETCH_FIXTURES = {
  "/health": { ok: true },
  "/v1/projects": {
    projects: [{ id: "p1", name: "Forge Demo" }],
  },
  "/v1/projects/p1": {
    project: { id: "p1", name: "Forge Demo" },
  },
  "/v1/projects/p1/unreal/preview?variant_index=0": {
    ok: true,
    preview: {
      project_id: "p1",
      project_name: "Forge Demo",
      variant_index: 0,
      source: "studio_project",
      diagnostics: [],
      shot_metadata_export: {
        engine: "unreal",
        handoff_kind: "shot_metadata_export",
        sequence_name: "forge_demo_MainSequence",
        fps: 24,
        duration_seconds: 8,
        audio_path: "projects/forge-demo/assets/audio/source.wav",
        project_fields: ["project_id", "project_name", "fps", "audio_path"],
        shot_fields: ["shot_id", "scene_id", "start_frame", "end_frame", "prompt", "continuity_tags"],
        marker_fields: ["label", "frame", "time_seconds"],
        shots: [{ shot_id: "shot_001_scene_1", scene_id: "scene-1", start_frame: 0, end_frame: 96, approved: true }],
        markers: [{ label: "Intro", frame: 0, time_seconds: 0 }],
      },
      render_handoff: {
        engine: "unreal",
        handoff_kind: "render_handoff",
        execution_owner: "external_runtime",
        return_owner: "studio",
        render_mode: "performance-led",
        schedule_stride: 2,
        approved_section_ids: ["scene-1"],
        expected_inputs: ["shot_manifest.json", "audio_markers.json", "style_packet.json"],
        expected_outputs: ["shot_render.mov", "alpha_pass.mov", "metadata.json"],
        assembly_mode: "ffmpeg_back_in_studio",
        sections: [{ shot_id: "shot_001_scene_1", scene_id: "scene-1", start_frame: 0, end_frame: 96, approved: true, engine_hint: "comfyui_motion" }],
      },
      live_control_bridge: {
        engine: "unreal",
        handoff_kind: "live_control_bridge",
        transports: {
          osc: ["/edmg/section", "/edmg/beat", "/edmg/camera"],
          websocket: ["section_change", "beat_pulse", "lighting_envelope"],
          remote_control: ["sequence.PlayRate", "camera.FocalLength", "lights.Intensity"],
        },
        cadence_hz: 30,
        bpm: 124,
        section_payload_fields: ["section_id", "energy", "continuity_priority"],
        section_events: [{ section_id: "scene-1", label: "Intro", time_seconds: 0, energy: 0.35, continuity_priority: 1 }],
        cue_events: [],
        beat_times: [0, 0.5, 1, 1.5],
        camera_keyframes: [{ t: 0, zoom: 1 }],
      },
    },
  },
  "/v1/config": {
    ai_mode: "local",
    ai_provider: "openai_compat",
    model: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  },
  "/v1/setup/status": READY_STATUS,
  "/v1/nvidia/diagnostics": {
    ok: true,
    nvidia: {
      profile: { enabled: true, profile: "omniverse" },
      host: { gpu: { ok: true, gpus: [{ name: "Test RTX" }] }, docker: { ok: true, nvidia_runtime: true } },
      services: {
        nim: { configured: true, probe: { reachable: true, models_url: "http://127.0.0.1:8001/v1/models" } },
        riva: { configured: true, probe: { reachable: true } },
        triton: { configured: true, probe: { reachable: true } },
        omniverse: { configured: true, probe: { reachable: true } },
      },
    },
  },
  "/v1/comfyui/capabilities": {
    ok: true,
    nodes: [],
  },
};

describe("Workshop Forge", () => {
  afterEach(() => {
    vi.stubEnv("VITE_EDMG_ENABLE_STUDIO_FORGE", ORIGINAL_STUDIO_FORGE_FLAG ?? "");
    window.history.pushState({}, "", "/");
  });

  it("renders the read-only page shell and template previews", async () => {
    installEdmgBridge();
    installFetchMock(FETCH_FIXTURES);

    renderWithStudio(
      <StudioForge backendUrl="http://127.0.0.1:7863" config={FETCH_FIXTURES["/v1/config"]} />,
    );

    expect(await screen.findByRole("heading", { name: /Workshop Forge/i })).toBeTruthy();
    expect(screen.getByText(/Read-only preview mode/i)).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Runtime Recommendations/i })).toBeTruthy();
    expect(screen.getByRole("heading", { name: /Unreal Bridge Previews/i })).toBeTruthy();
    expect((await screen.findAllByText(/Ready now/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Render Queue Dashboard/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Unreal Shot Metadata Export/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Preview payload/i)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/forge_demo_MainSequence/i)).toBeTruthy();
    expect(await screen.findByText(/Live preview payloads are coming from the active Studio project/i)).toBeTruthy();
    expect((await screen.findAllByText(/Audio -> Analysis -> AI Plan -> Render -> Assemble/i)).length).toBeGreaterThan(0);
    expect(screen.getByText(/Developer validation commands only/i)).toBeTruthy();
  });

  it("surfaces setup-needed recommendations when runtime capabilities are missing", async () => {
    installEdmgBridge();
    installFetchMock({
      ...FETCH_FIXTURES,
      "/v1/setup/status": {
        ...READY_STATUS,
        nvidia_profile: {
          enabled: false,
          profile: "omniverse",
          services: {},
        },
        comfyui: {
          ok: false,
          url: "http://127.0.0.1:8188",
        },
        ffmpeg: {
          ok: false,
          path: "ffmpeg",
        },
        edmg: {
          available: false,
        },
      },
      "/v1/nvidia/diagnostics": {
        ok: true,
        nvidia: {
          profile: { enabled: false, profile: "omniverse" },
          host: { gpu: { ok: false, gpus: [] }, docker: { ok: false, nvidia_runtime: false } },
          services: { nim: { configured: false, probe: { reachable: false } } },
        },
      },
      "/v1/comfyui/capabilities": () => {
        throw new Error("ComfyUI offline");
      },
    });

    renderWithStudio(
      <StudioForge backendUrl="http://127.0.0.1:7863" config={FETCH_FIXTURES["/v1/config"]} />,
    );

    expect((await screen.findAllByText(/Setup needed/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Missing required capabilities/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Optional boosts not detected/i)).length).toBeGreaterThan(0);
  });

  it("falls back to dashboard when direct page access is disabled", async () => {
    installEdmgBridge();
    installFetchMock(FETCH_FIXTURES);
    window.history.pushState({}, "", "/?page=studioForge");

    renderWithStudio(<App />);

    expect(await screen.findByRole("heading", { name: /Dashboard/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Workshop Forge/i })).toBeNull();
  });

  it("enables direct page access and labs navigation when the flag is on", async () => {
    vi.stubEnv("VITE_EDMG_ENABLE_STUDIO_FORGE", "1");
    installEdmgBridge();
    installFetchMock(FETCH_FIXTURES);
    window.history.pushState({}, "", "/?page=studioForge");

    renderWithStudio(<App />);

    expect(await screen.findByRole("heading", { name: /Workshop Forge/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Workshop Forge/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /AI Planner Lab/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Reactive Lab/i })).toBeTruthy();
  });
});
