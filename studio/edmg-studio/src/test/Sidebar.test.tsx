import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Sidebar from "../components/Sidebar";
import { preloadNavigationIntent } from "../pageRouting";

vi.mock("../pageRouting", async () => {
  const actual = await vi.importActual<typeof import("../pageRouting")>("../pageRouting");
  return {
    ...actual,
    preloadNavigationIntent: vi.fn(),
  };
});

describe("Sidebar", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("uses grouped navigation while keeping standalone labs available", () => {
    render(<Sidebar page="plannerLab" onNavigate={() => {}} />);

    const logo = screen.getByAltText("EDMG NVIDIA Workshop logo");
    expect(logo.getAttribute("src")).toBe("studio-logo.png");
    expect(screen.getByText("Labs")).toBeTruthy();
    expect(screen.getByRole("button", { name: /EDMG Director/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /AI Planner Lab/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Reactive Lab/i })).toBeTruthy();
  });

  it("warms route chunks when navigation intent is shown", () => {
    render(<Sidebar page="dashboard" onNavigate={() => {}} />);

    const renderButton = screen.getByText("Render").closest("button");
    expect(renderButton).toBeTruthy();

    fireEvent.mouseEnter(renderButton!);
    fireEvent.focus(renderButton!);

    expect(preloadNavigationIntent).toHaveBeenCalledWith("render");
    expect(preloadNavigationIntent).toHaveBeenCalledTimes(2);
  });
});
