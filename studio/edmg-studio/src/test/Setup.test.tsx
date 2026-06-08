import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Setup from "../pages/Setup";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Setup page", () => {
  it("shows the active AI path and Studio Home controls", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/setup/status": {
        ollama: { ok: true, model_present: true },
        comfyui: { ok: false },
        ffmpeg: { ok: true },
        backend_bundle: { ok: true },
        sevenzip: { ok: true },
        ai_config: { label: "Local llama.cpp server", ollama_required: false, model_required: false },
        nvidia_profile: { enabled: true, profile: "omniverse" },
      },
      "/v1/nvidia/diagnostics": {
        ok: true,
        nvidia: {
          profile: { enabled: true, profile: "omniverse", credentials: { ngc_api_key_configured: true } },
          host: {
            gpu: { ok: true, gpus: [{ name: "Test RTX" }] },
            docker: { ok: true, nvidia_runtime: true },
          },
          services: { nim: { model: "nvidia/model", probe: { reachable: true, models_url: "http://127.0.0.1:8001/v1/models" } } },
          readiness: {
            level: "ready",
            summary: "NVIDIA profile, local GPU runtime, NGC access, and NIM planner endpoint are ready.",
            checks: [{ id: "gpu_visible", label: "NVIDIA GPU visible to host", ok: true, detail: "Test RTX" }],
            next_actions: [],
          },
        },
      },
      "/v1/models/catalog": { packs: [], catalog: [], user: [] },
    });

    renderWithStudio(<Setup />);

    expect(await screen.findByText("NVIDIA Runtime Setup")).toBeTruthy();
    expect(await screen.findByText(/Active AI path:/)).toBeTruthy();
    const activeAiPath = await screen.findByTestId("setup-active-ai-path");
    expect(activeAiPath.textContent).toContain("NVIDIA");
    expect(activeAiPath.textContent).toContain("enabled");
    expect(await screen.findByText("NVIDIA Runtime Readiness")).toBeTruthy();
    expect((await screen.findAllByText(/Test RTX/)).length).toBeGreaterThan(0);
    expect(await screen.findByDisplayValue("D:\\EDMG-Studio")).toBeTruthy();
  });

  it("shows Linux-friendly storage paths and manual setup guidance", async () => {
    installEdmgBridge({
      getStudioPaths: async () => ({
        ok: true,
        platform: "linux",
        studioHome: "/home/test/EDMG-Studio",
        dataDir: "/home/test/EDMG-Studio/data",
        modelsDir: "/home/test/EDMG-Studio/models",
        cacheRoot: "/home/test/EDMG-Studio/cache",
        logsDir: "/home/test/EDMG-Studio/logs",
        externalDir: "/home/test/EDMG-Studio/external",
        electronUserData: "/home/test/EDMG-Studio/electron",
        sessionData: "/home/test/EDMG-Studio/electron/session",
        bootstrapConfigPath: "/home/test/.config/EDMG Studio/bootstrap.json",
        source: "test",
      }),
    });
    installFetchMock({
      "/v1/setup/status": {
        ollama: { ok: false, model_present: false, launch_available: true },
        comfyui: { ok: false, url: "http://127.0.0.1:8188" },
        ffmpeg: { ok: true },
        backend_bundle: { ok: true },
        backend_bundle_directml: { ok: false, missing: ["onnxruntime-directml"] },
        sevenzip: { ok: true, hint: "optional on linux" },
        hardware: { supports_directml: false },
        ai_config: { label: "Local Ollama", ollama_required: true, model_required: true },
      },
      "/v1/nvidia/diagnostics": {
        ok: true,
        nvidia: {
          profile: { enabled: false, credentials: { ngc_api_key_configured: false } },
          host: { gpu: { ok: false, gpus: [] }, docker: { ok: false, nvidia_runtime: false } },
          services: { nim: { probe: { reachable: false } } },
          readiness: { level: "disabled", summary: "NVIDIA profile is disabled.", checks: [], next_actions: [] },
        },
      },
      "/v1/models/catalog": { packs: [], catalog: [], user: [] },
    });

    renderWithStudio(<Setup />);

    expect(await screen.findByDisplayValue("/home/test/EDMG-Studio")).toBeTruthy();
    expect(await screen.findByText("0) System Setup")).toBeTruthy();
    expect(await screen.findByText(/Linux and macOS use the manual NVIDIA path/)).toBeTruthy();
    expect(await screen.findByText(/Linux support expects a system-installed/i)).toBeTruthy();
    expect(await screen.findByText(/Linux support uses a manually installed ComfyUI instance.*secondary workflows/i)).toBeTruthy();
  });

  it("shows a cancel button for active installer tasks", async () => {
    installEdmgBridge();
    let taskStatus = "running";
    const fetchMock = installFetchMock({
      "/v1/setup/status": () => ({
        ollama: { ok: false, model_present: false },
        comfyui: { ok: false },
        ffmpeg: { ok: true },
        backend_bundle: { ok: true },
        sevenzip: { ok: false },
        ai_config: { label: "Local Ollama", ollama_required: true, model_required: true },
        tasks: [
          {
            id: "task1234",
            name: "install_7zip",
            status: taskStatus,
            progress: taskStatus === "canceled" ? 0.42 : 0.42,
            last_log: taskStatus === "canceled" ? "Cancel requested — stopping after current step." : "Downloading portable 7-Zip CLI",
            cancel_requested: taskStatus === "canceled",
          },
        ],
      }),
      "/v1/nvidia/diagnostics": {
        ok: true,
        nvidia: {
          profile: { enabled: false, credentials: { ngc_api_key_configured: false } },
          host: { gpu: { ok: false, gpus: [] }, docker: { ok: false, nvidia_runtime: false } },
          services: { nim: { probe: { reachable: false } } },
          readiness: { level: "disabled", summary: "NVIDIA profile is disabled.", checks: [], next_actions: [] },
        },
      },
      "/v1/models/catalog": { packs: [], catalog: [], user: [] },
      "POST /v1/setup/tasks/task1234/cancel": () => {
        taskStatus = "canceled";
        return {
          ok: true,
          task: {
            id: "task1234",
            name: "install_7zip",
            status: "canceled",
            progress: 0.42,
            last_log: "Cancel requested — stopping after current step.",
            cancel_requested: true,
          },
        };
      },
    });

    renderWithStudio(<Setup />);

    const cancelButton = await screen.findByRole("button", { name: "Cancel" });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://127.0.0.1:7863/v1/setup/tasks/task1234/cancel",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText(/canceled/i)).toBeTruthy();
  });
});
