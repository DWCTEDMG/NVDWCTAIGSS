import { describe, expect, it } from "vitest";
import { getPageLoadingDetails, getPagesToPreload } from "../pageRouting";

describe("App route hints", () => {
  it("describes Workshop Forge with page-specific loading copy", () => {
    const loading = getPageLoadingDetails("studioForge");

    expect(loading.label).toBe("Workshop Forge");
    expect(loading.detail).toMatch(/runtime preview cards/i);
  });

  it("preloads likely next routes for workspace flow", () => {
    expect(getPagesToPreload("workspace")).toEqual([
      "timeline",
      "render",
      "directorLab",
      "plannerLab",
      "reactiveLab",
    ]);
  });

  it("preloads setup and render-adjacent pages for Workshop Forge", () => {
    expect(getPagesToPreload("studioForge")).toEqual([
      "setup",
      "models",
      "render",
    ]);
  });
});
