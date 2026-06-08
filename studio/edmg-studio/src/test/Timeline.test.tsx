import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Timeline from "../pages/Timeline";
import { installEdmgBridge, installFetchMock, renderWithStudio } from "./testUtils";

describe("Timeline page", () => {
  it("updates the transport button when audio playback events fire", async () => {
    installEdmgBridge();
    installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Smoothness Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Smoothness Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 8 },
            analysis: { features: { duration_s: 8, bpm: 120 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [
                    { id: "scene_0", start_s: 0, end_s: 8, prompt: "A continuous guitar performance with smooth motion." },
                  ],
                },
              ],
            },
          },
        },
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    expect(await screen.findByText("Timeline")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Play" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Fit all" })).toBeTruthy();

    await waitFor(() => expect(document.querySelector("audio")).toBeTruthy());
    const audio = document.querySelector("audio");
    expect(audio).toBeTruthy();
    fireEvent.play(audio as HTMLAudioElement);
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();

    fireEvent.pause(audio as HTMLAudioElement);
    expect(await screen.findByRole("button", { name: "Play" })).toBeTruthy();
  });

  it("adds DAW markers and saves the DAW session with the timeline", async () => {
    installEdmgBridge();
    const fetchMock = installFetchMock({
      "/v1/projects": { projects: [{ id: "p1", name: "Post Mix Test" }] },
      "/v1/projects/p1": {
        project: {
          id: "p1",
          name: "Post Mix Test",
          meta: {
            audio: { filename: "track.wav", duration_s: 12 },
            analysis: { features: { duration_s: 12, bpm: 100, energy: 0.62, integrated_lufs: -15.7 } },
            last_plan: {
              variants: [
                {
                  name: "Variant 1",
                  scenes: [
                    { id: "scene_0", start_s: 0, end_s: 6, name: "Opening", prompt: "Opening shot." },
                    { id: "scene_1", start_s: 6, end_s: 12, name: "Finale", prompt: "Finale shot." },
                  ],
                },
              ],
            },
            timeline: { tracks: [], layers: [], camera: { keyframes: [] } },
          },
        },
      },
      "POST /v1/projects/p1/timeline": (_path, init) => {
        const body = JSON.parse(String(init?.body || "{}"));
        return { ok: true, timeline: body.timeline };
      },
    });

    renderWithStudio(<Timeline backendUrl="http://127.0.0.1:7863" config={{}} />);

    fireEvent.click(await screen.findByRole("tab", { name: /Post DAW/i }));
    expect(await screen.findByText("Markers and ADR cues")).toBeTruthy();
    expect(await screen.findByText("Mixer buses")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Add ADR" }));
    expect(await screen.findByDisplayValue("ADR cue 1")).toBeTruthy();

    const musicGain = await screen.findByLabelText("Music gain");
    fireEvent.change(musicGain, { target: { value: "-9" } });

    fireEvent.click(await screen.findByRole("button", { name: /Save timeline/i }));

    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(([input, init]) => {
        return String(input).includes("/v1/projects/p1/timeline") && init?.method === "POST";
      });
      expect(saveCall).toBeTruthy();
      const body = JSON.parse(String(saveCall?.[1]?.body || "{}"));
      expect(body.timeline.daw.markers.some((marker: any) => marker.label === "ADR cue 1")).toBe(true);
      expect(body.timeline.daw.buses.find((bus: any) => bus.id === "music")?.gain_db).toBe(-9);
    });
  });
});
