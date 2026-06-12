import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Settings from "../pages/Settings";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Settings page", () => {
  it("persists desktop backend mode and target settings", async () => {
    const setBackendSettings = vi.fn(async (settings: { mode: string; host: string; port: string; url?: string }) => ({
      ok: true,
      restartRequired: true,
      ...settings,
      currentBackendUrl: String(settings.url || `http://${settings.host}:${settings.port}`),
    }));

    installEdmgBridge({
      getBackendSettings: async () => ({
        ok: true,
        mode: "managed",
        host: "127.0.0.1",
        port: "7863",
        url: "",
        source: "bootstrap",
        currentBackendUrl: "http://127.0.0.1:7863",
      }),
      setBackendSettings,
    });

    installFetchMock({
      "/v1/config": {
        ai_mode: "local",
        ai_provider: "openai_compat",
        ai_openai_compat_base_url: "http://127.0.0.1:8001/v1",
        ai_openai_compat_model: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
      },
      "/v1/ai/status": { ok: true, provider: "openai_compat" },
      "/v1/nvidia/status": {
        ok: true,
        nvidia: {
          enabled: true,
          profile: "omniverse",
          credentials: { ngc_api_key_configured: true },
          services: {
            nim: { configured: true, base_url: "http://127.0.0.1:8001/v1", model: "nvidia/model" },
            riva: { configured: true, base_url: "http://127.0.0.1:50051" },
            omniverse: { configured: false, base_url: "" },
            triton: { configured: true, base_url: "http://127.0.0.1:8002", model: "edmg_ensemble" },
            audio2face: { configured: false, base_url: "" },
          },
        },
      },
      "/v1/nvidia/diagnostics": {
        ok: true,
        nvidia: {
          host: {
            gpu: { ok: true, gpus: [{ name: "Test RTX" }] },
            docker: { ok: true, nvidia_runtime: true },
          },
          services: {
            nim: {
              probe: {
                reachable: true,
                models_url: "http://127.0.0.1:8001/v1/models",
              },
            },
            triton: {
              configured: true,
              base_url: "http://127.0.0.1:8002",
              model: "edmg_ensemble",
              probe: { reachable: true },
            },
            audio2face: {
              configured: false,
              base_url: "",
              probe: { configured: false, skipped: true },
            },
            ace: {
              configured: false,
              base_url: "",
              probe: { configured: false, skipped: true },
            },
            cosmos: {
              configured: true,
              base_url: "http://127.0.0.1:8010",
              model: "cosmos-test",
              probe: { reachable: false, error: "offline" },
            },
          },
        },
      },
      "/v1/edmg/deforum_template": { ok: true },
      "/v1/settings/secrets/status": { store: "test", has_openai_compat_api_key: false },
      "/v1/hardware": {
        hardware: {
          device_name: "Test GPU",
          backend_family: "cuda",
          recommended_tier: "balanced",
        },
      },
      "/v1/settings/render_profiles": {
        recommended_profile: "balanced_auto",
        profiles: {
          balanced_auto: {
            label: "Balanced Auto",
            description: "General desktop profile",
            render_preset: "balanced",
            internal_render_tier: "balanced",
            resume_existing_frames: true,
          },
        },
      },
      "/v1/settings/render_providers": {
        settings: {},
      },
      "/health": { ok: true },
    });

    renderWithStudio(<Settings backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Desktop Backend")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Desktop backend mode"), {
      target: { value: "external" },
    });
    fireEvent.change(screen.getByLabelText("Desktop backend URL"), {
      target: { value: "https://edmg-backend.example.com" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save backend startup settings" }));

    await waitFor(() => {
      expect(setBackendSettings).toHaveBeenCalledWith({
        mode: "external",
        host: "127.0.0.1",
        port: "7863",
        url: "https://edmg-backend.example.com",
      });
    });

    expect(await screen.findByText(/Saved\. Restart Workshop so it relaunches against the selected backend target\./)).toBeTruthy();
    expect(await screen.findByText("NVIDIA service profile")).toBeTruthy();
    expect(await screen.findByText("NVIDIA runtime diagnostics")).toBeTruthy();
    expect(await screen.findByText("NVIDIA service stack")).toBeTruthy();
    expect(await screen.findByText("Audio2Face")).toBeTruthy();
    expect(await screen.findByText("Triton")).toBeTruthy();
    expect(await screen.findByText("edmg_ensemble")).toBeTruthy();
    expect((await screen.findAllByText(/Test RTX/)).length).toBeGreaterThan(0);
  });
});
