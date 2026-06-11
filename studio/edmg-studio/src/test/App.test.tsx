import React from "react";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("App boot", () => {
  it("boots into the setup page when dependencies are missing", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/config": {},
      "/v1/setup/status": {
        comfyui: { ok: false },
        ffmpeg: { ok: false },
        backend_bundle: { ok: false },
        sevenzip: { ok: false },
        ai_config: { label: "NVIDIA NIM / OpenAI-compatible provider", provider: "openai_compat" },
        nvidia_profile: { enabled: true, profile: "omniverse" },
      },
      "/v1/models/catalog": { packs: [], catalog: [], user: [] },
    });

    renderWithStudio(<App />);

    expect(await screen.findByText("NVIDIA Runtime Setup")).toBeTruthy();
    expect(await screen.findByText(/Active AI path:/)).toBeTruthy();
  });
});
