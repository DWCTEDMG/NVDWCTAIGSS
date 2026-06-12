import React from "react";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Workspace from "../pages/Workspace";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Workspace page", () => {
  it("integrates creative direction from the real project analysis and plan", async () => {
    window.localStorage.clear();
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Demo Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Demo Project",
          meta: {
            audio: { filename: "track.wav", size_bytes: 1024 * 1024, duration_s: 48 },
            analysis: {
              features: {
                duration_s: 48,
                bpm: 122,
                energy: 0.68,
                bass_energy: 0.55,
                mid_energy: 0.49,
                brightness: 0.42,
              },
              transcript: {
                text: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
              },
            },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [
                    { name: "Neon arrival", start_s: 0, end_s: 12, prompt: "Neon streets with rain reflections and kinetic camera drift." },
                    { name: "Skyline lift", start_s: 12, end_s: 24, prompt: "Dawn skyline bloom with silhouettes and stronger motion parallax." },
                  ],
                },
              ],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/audio": {},
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          preset: "cinematic",
          sensitivity: 1,
          metrics: { energy: 0.68, bass: 0.55, mid: 0.49, treble: 0.42, duration_s: 48, source: "analysis" },
          waveform: [0.2, 0.45, 0.8, 0.5],
          motifs: ["neon", "skyline", "dawn"],
          transcript_text: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
          transcript_summary: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
          status: "Creative direction is being derived on the backend from the saved project analysis and plan.",
          export_text: "1. Neon arrival (0.00s - 12.00s)\nNeon streets with rain reflections and kinetic camera drift.",
          scenes: [
            {
              index: 0,
              name: "Neon arrival",
              start_s: 0,
              end_s: 12,
              duration_s: 12,
              energy: 0.64,
              energy_label: "lift",
              prompt: "Neon streets with rain reflections and kinetic camera drift.",
              transcript_cue: "Neon streets open into a dawn skyline while the chorus lifts the whole crowd forward.",
              camera_hint: "Tracking medium shot with progressive push, controlled drift, and bolder edge lighting.",
              motion_hint: "Zoom 1.14, cfg 7.9, strength 0.68, Z travel -16.6.",
              prompt_pack: "Neon streets with rain reflections and kinetic camera drift.",
            },
          ],
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByRole("heading", { name: "Workspace" })).toBeTruthy();
    expect(await screen.findByText("Creative direction")).toBeTruthy();
    expect(await screen.findByText("Scene prompt pack")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Copy prompt pack" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Apply direction to timeline" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Timeline patch" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "LLM contract" })).toBeTruthy();
    expect(await screen.findByText(/Transcript anchor/i)).toBeTruthy();

    fireEvent.click(await screen.findByRole("tab", { name: /Reactive Lab/i }));
    expect(await screen.findByText("Reactive Lab + Renderer Handoff")).toBeTruthy();
  });

  it("reconciles a stale stored project id before loading project detail routes", async () => {
    window.localStorage.clear();
    window.localStorage.setItem("edmg_studio_session_v1", JSON.stringify({
      projectId: "stale-project",
      selectedVariant: 0,
      lastHandoff: null,
    }));
    installEdmgBridge();
    const fetchMock = installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Recovered Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Recovered Project",
          meta: {
            last_plan: {
              variants: [{ name: "Variant 1", scenes: [] }],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          status: "Recovered selection.",
          export_text: "",
          scenes: [],
          metrics: { energy: 0, bass: 0, mid: 0, treble: 0, duration_s: 0, source: "analysis" },
          missing: [],
          waveform: [],
          motifs: [],
          transcript_text: "",
          transcript_summary: "",
          preset: "cinematic",
          sensitivity: 1,
          provider_mode: "local",
          scene_source: "plan",
          ready: false,
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Recovered Project")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/v1/projects/stale-project"))).toBe(false);
  });

  it("surfaces a precise no-speech-after-vad status when no transcript is available", async () => {
    window.localStorage.clear();
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Instrumental Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Instrumental Project",
          meta: {
            analysis: {
              summary: "Transcription unavailable. Using audio-only analysis from rhythm, energy, and spectral movement.",
              transcript: {
                text: "",
                note: "No speech detected after VAD.",
                duration_after_vad_s: 0,
                segment_count: 0,
                source: "nvidia_riva",
              },
              features: {
                duration_s: 374.8,
                bpm: 60,
                energy: 0.41,
              },
            },
            last_plan: {
              variants: [{ name: "Variant 1", scenes: [] }],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          status: "Audio-only creative direction is ready.",
          export_text: "",
          scenes: [],
          metrics: { energy: 0.41, bass: 0.28, mid: 0.35, treble: 0.22, duration_s: 374.8, source: "analysis" },
          missing: [],
          waveform: [],
          motifs: [],
          transcript_text: "",
          transcript_summary: "No speech detected after VAD. Workshop is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement.",
          preset: "cinematic",
          sensitivity: 1,
          provider_mode: "local",
          scene_source: "analysis_fallback",
          ready: true,
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect((await screen.findAllByText(/No speech detected after VAD/i)).length).toBeGreaterThan(0);
    expect(await screen.findByText("No speech after VAD")).toBeTruthy();
  });

  it("surfaces the persisted NVIDIA scene plan and can request a project-scoped refresh", async () => {
    window.localStorage.clear();
    installEdmgBridge();
    const fetchMock = installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "NVIDIA Project" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "NVIDIA Project",
          meta: {
            analysis: { features: { duration_s: 32, bpm: 128 }, transcript: { text: "Neon chorus." } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [{ name: "Neon pass", start_s: 0, end_s: 16, prompt: "RTX neon stage." }],
                },
              ],
            },
          },
        },
      },
      "/v1/projects/p1/assets": { assets: { refs: [] } },
      "/v1/projects/p1/nvidia/scene-plan": {
        ok: true,
        project_id: "p1",
        saved_at: "2026-05-31 12:00:00",
        scene_plan: { provider: "nvidia-profile:openai_compat:nvidia/test", scenes: [{ id: "a" }, { id: "b" }] },
        planner: { provider: "openai_compat", model: "nvidia/test" },
        usd_metadata: { "edmg:sceneCount": 2 },
        usd_stage: { format: "usda", text: "#usda 1.0\n" },
      },
      "POST /v1/projects/p1/nvidia/scene-plan": {
        ok: true,
        project: { persisted: true, project_id: "p1" },
        scene_plan: { provider: "nvidia-profile:openai_compat:nvidia/test", scenes: [{ id: "a" }, { id: "b" }] },
        planner: { provider: "openai_compat", model: "nvidia/test" },
        usd_metadata: { "edmg:sceneCount": 2 },
        usd_stage: { format: "usda", text: "#usda 1.0\n" },
      },
      "POST /v1/projects/p1/nvidia/scene-plan/export": {
        ok: true,
        export: { path: "outputs/nvidia/nvidia_project_20260531_120000.usda", scene_count: 2 },
      },
      "POST /v1/projects/p1/nvidia/render-job": {
        ok: true,
        export: { path: "outputs/nvidia/render_jobs/nvidia_project_20260531_120000.json", scene_count: 2 },
        job: { kind: "nvidia_rtx_render_job", scene_count: 2 },
      },
      "/v1/projects/p1/creative_direction*": {
        creative_direction: {
          status: "NVIDIA project direction.",
          export_text: "",
          scenes: [],
          metrics: { energy: 0, bass: 0, mid: 0, treble: 0, duration_s: 32, source: "analysis" },
          missing: [],
          waveform: [],
          motifs: [],
          transcript_text: "Neon chorus.",
          transcript_summary: "Neon chorus.",
          preset: "cinematic",
          sensitivity: 1,
          provider_mode: "local",
          scene_source: "plan",
          ready: true,
        },
      },
    });

    renderWithStudio(<Workspace backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("NVIDIA / OpenUSD handoff")).toBeTruthy();
    expect(await screen.findByText("2 USD scenes")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Generate NVIDIA plan" }));

    expect(
      await screen.findByText("NVIDIA scene plan saved to the active project."),
    ).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Export USDA" }));
    expect(await screen.findByText("NVIDIA USDA exported to project outputs.")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Build RTX job" }));
    expect(await screen.findByText("NVIDIA RTX render job exported to project outputs.")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).includes("/v1/projects/p1/nvidia/scene-plan") && String(init?.method || "GET").toUpperCase() === "POST",
    )).toBe(true);
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).includes("/v1/projects/p1/nvidia/scene-plan/export") && String(init?.method || "GET").toUpperCase() === "POST",
    )).toBe(true);
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).includes("/v1/projects/p1/nvidia/render-job") && String(init?.method || "GET").toUpperCase() === "POST",
    )).toBe(true);
  });
});
