import React from "react";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Outputs from "../pages/Outputs";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Outputs page", () => {
  it("renders active internal jobs, round-trips Unreal bundle actions, and navigates to the render queue", async () => {
    const onNavigate = vi.fn();
    let unrealExported = false;
    let unrealPlanBuilt = false;
    let unrealReturned = false;
    let exportRequest: any = null;
    let planRequest: any = null;
    let importRequest: any = null;
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Demo Project" }] },
      "/v1/projects/p1/outputs": () => ({
        videos: unrealReturned
          ? [
              {
                path: "outputs/videos/demo_bundle_shot_render.mp4",
                metadata_path: "outputs/videos/demo_bundle_shot_render.mp4.json",
                metadata: {
                  kind: "unreal_bridge_return",
                  sequence_name: "demo_sequence_MainSequence",
                  bundle_dir: "outputs/unreal/demo_bundle",
                  output: { video: "outputs/videos/demo_bundle_shot_render.mp4" },
                },
              },
            ]
          : [],
        images: [
          {
            path: "outputs/images/frame.png",
            metadata_path: "outputs/images/frame.png.json",
            metadata: {
              workflow_family: "outpaint",
              engine: "internal",
              model_family: "sdxl",
              prompt: "A luminous skyline with added edge detail",
              negative_prompt: "blurry",
              seed: 42,
              sampler: "euler",
              steps: 28,
              cfg: 7.5,
              source_asset: "assets/refs/source.png",
              mask_source: "generated_outpaint",
              base_model: { model_id: "hf_sdxl_internal" },
              outpaint: { top_px: 32, right_px: 64, bottom_px: 0, left_px: 16 },
              loras: [{ name: "Neon Accent", weight: 0.8 }],
              controlnet_units: [{ model: "depth", strength: 0.65 }],
              output: { image: "outputs/images/frame.png" },
            },
          },
        ],
        latest_internal_render: null,
        internal_render_history: [],
        active_internal_jobs: [
          {
            id: "job-1",
            project_id: "p1",
            status: "failed",
            type: "internal_video",
            progress: {
              stage: "rendering",
              runtime_checkpoint: {
                resume_percent: 50,
                completed_chunks: 1,
                estimated_chunks: 2,
                next_frame_index: 10,
                total_frames: 20,
                chunk_strategy: "windowed",
                checkpoint_interval_frames: 12,
                can_resume: true,
                outputs: { checkpoint_json: "data/checkpoint.json" },
              },
            },
          },
        ],
        nvidia_exports: [
          {
            path: "outputs/nvidia/nvidia_project_20260531_120000.usda",
            name: "nvidia_project_20260531_120000.usda",
            size_bytes: 2048,
            metadata_path: "outputs/nvidia/nvidia_project_20260531_120000.usda.json",
            metadata: { scene_count: 2, provider: "nvidia-profile:openai_compat:nvidia/test" },
          },
        ],
        nvidia_render_jobs: [
          {
            path: "outputs/nvidia/render_jobs/nvidia_project_20260531_120000.json",
            name: "nvidia_project_20260531_120000.json",
            size_bytes: 4096,
            metadata: {
              kind: "nvidia_rtx_render_job",
              scene_count: 2,
              renderer: { fps: 24 },
              project: { title: "NVIDIA Project" },
            },
            result: {
              status: "completed",
              outputs: {
                still: "outputs/nvidia/renders/001_scene-1/001_scene-1_000000.png",
                movie: "outputs/nvidia/renders/001_scene-1.mp4",
              },
            },
          },
        ],
        unreal_exports: unrealExported
          ? [
              {
                bundle_dir: "outputs/unreal/demo_bundle",
                manifest_path: "outputs/unreal/demo_bundle/bundle_manifest.json",
                zip_path: "outputs/unreal/demo_bundle.zip",
                created_at: "2026-05-05 15:00:00",
                variant_index: 0,
                sequence_name: "demo_sequence_MainSequence",
                import_plan_path: unrealPlanBuilt ? "outputs/unreal/demo_bundle/unreal_import_plan.json" : null,
                import_plan: unrealPlanBuilt
                  ? {
                      asset_path: "/Game/EDMG/Sequences/demo_sequence_MainSequence",
                      content_path: "/Game/EDMG/Sequences",
                      expected_return_dir: "outputs/unreal/demo_bundle/returned",
                    }
                  : null,
                manifest: {
                  files: [
                    { path: "shot_manifest.json" },
                    { path: "audio_markers.json" },
                  ],
                },
              },
            ]
          : [],
        unreal_returns: unrealReturned
          ? [
              {
                bundle_dir: "outputs/unreal/demo_bundle",
                source_dir: "outputs/unreal/demo_bundle/returned",
                created_at: "2026-05-05 15:02:00",
                variant_index: 0,
                sequence_name: "demo_sequence_MainSequence",
                media: [
                  {
                    path: "outputs/videos/demo_bundle_shot_render.mp4",
                    kind: "video",
                    source_path: "outputs/unreal/demo_bundle/returned/shot_render.mp4",
                    metadata_path: "outputs/videos/demo_bundle_shot_render.mp4.json",
                  },
                ],
              },
            ]
          : [],
      }),
      "POST /v1/projects/p1/export/unreal": (_path, init) => {
        exportRequest = init?.body ? JSON.parse(String(init.body)) : null;
        unrealExported = true;
        return {
          ok: true,
          bundle: {
            bundle_dir: "outputs/unreal/demo_bundle",
            manifest_path: "outputs/unreal/demo_bundle/bundle_manifest.json",
            zip_path: "outputs/unreal/demo_bundle.zip",
          },
        };
      },
      "POST /v1/projects/p1/unreal/import-plan": (_path, init) => {
        planRequest = init?.body ? JSON.parse(String(init.body)) : null;
        unrealPlanBuilt = true;
        return {
          ok: true,
          plan_path: "outputs/unreal/demo_bundle/unreal_import_plan.json",
          plan: {
            asset_path: "/Game/EDMG/Sequences/demo_sequence_MainSequence",
            content_path: "/Game/EDMG/Sequences",
            expected_return_dir: "outputs/unreal/demo_bundle/returned",
          },
        };
      },
      "POST /v1/projects/p1/import/unreal": (_path, init) => {
        importRequest = init?.body ? JSON.parse(String(init.body)) : null;
        unrealReturned = true;
        return {
          ok: true,
          imported: {
            bundle_dir: "outputs/unreal/demo_bundle",
            source_dir: "outputs/unreal/demo_bundle/returned",
          },
        };
      },
    });

    renderWithStudio(<Outputs backendUrl="http://127.0.0.1:7863" config={null} onNavigate={onNavigate} />);

    expect(await screen.findByRole("heading", { name: "Outputs" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Outputs layout profile" })).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Open Render Queue" }));
    expect(onNavigate).toHaveBeenCalledWith("queue");
    fireEvent.click(await screen.findByRole("button", { name: "Export Unreal Bundle" }));
    expect(await screen.findByText(/Unreal bridge exports/i)).toBeTruthy();
    expect((await screen.findAllByText(/demo_sequence_MainSequence/i)).length).toBeGreaterThan(0);
    expect(exportRequest).toEqual({
      variant_index: 0,
      bundle_name: null,
      include_zip: true,
    });
    fireEvent.click(await screen.findByRole("button", { name: "Build import plan" }));
    expect((await screen.findAllByText(/\/Game\/EDMG\/Sequences\/demo_sequence_MainSequence/i)).length).toBeGreaterThan(0);
    expect(planRequest).toEqual({
      bundle_dir: "outputs/unreal/demo_bundle",
      content_path: null,
      asset_name: null,
    });
    fireEvent.click(await screen.findByRole("button", { name: "Import returned media" }));
    expect(await screen.findByText(/Unreal bridge returns/i)).toBeTruthy();
    expect((await screen.findAllByText(/outputs\/videos\/demo_bundle_shot_render\.mp4/i)).length).toBeGreaterThan(0);
    expect(importRequest).toEqual({
      bundle_dir: "outputs/unreal/demo_bundle",
      source_dir: null,
    });
    expect((await screen.findAllByText(/Generation metadata/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/A luminous skyline with added edge detail/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Outpaint margins/i)).toBeTruthy();
    expect(await screen.findByText(/NVIDIA \/ OpenUSD exports/i)).toBeTruthy();
    expect((await screen.findAllByText(/nvidia_project_20260531_120000\.usda/i)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/NVIDIA RTX render jobs/i)).toBeTruthy();
    expect((await screen.findAllByText(/outputs\/nvidia\/render_jobs\/nvidia_project_20260531_120000\.json/i)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/outputs\/nvidia\/renders\/001_scene-1\.mp4/i)).length).toBeGreaterThan(0);
  }, 15000);
});
