import React, { useState } from "react";
import { isStudioForgeEnabled } from "../features";
import { preloadNavigationIntent, type Page } from "../pageRouting";

type NavGroupId = "flow" | "delivery" | "labs" | "system";

type NavItem = {
  page: Page;
  label: string;
  hint: string;
};

type NavGroup = {
  id: NavGroupId;
  label: string;
  hint: string;
  items: NavItem[];
};

function getNavGroups(): NavGroup[] {
  const labs: NavItem[] = [
    { page: "directorLab", label: "EDMG Director", hint: "combined planning + reactive" },
    { page: "plannerLab", label: "AI Planner Lab", hint: "deep prompt authoring" },
    { page: "reactiveLab", label: "Reactive Lab", hint: "audio-reactive scheduling" },
  ];
  if (isStudioForgeEnabled()) {
    labs.push({ page: "studioForge", label: "Studio Forge", hint: "AI builder preview" });
  }

  return [
    {
      id: "flow",
      label: "Core Flow",
      hint: "Canonical studio path",
      items: [
        { page: "dashboard", label: "Dashboard", hint: "status + quick access" },
        { page: "projects", label: "Projects", hint: "create and pick sessions" },
        { page: "workspace", label: "Workspace", hint: "ingest, plan, reactive handoff" },
        { page: "timeline", label: "Timeline", hint: "arrange full track and cues" },
      ],
    },
    {
      id: "delivery",
      label: "Delivery",
      hint: "Render and review",
      items: [
        { page: "render", label: "Render", hint: "launch outputs" },
        { page: "queue", label: "Render Queue", hint: "logs, retries, progress" },
        { page: "outputs", label: "Outputs", hint: "browse generated media" },
      ],
    },
    {
      id: "labs",
      label: "Labs",
      hint: "Standalone specialist tools",
      items: labs,
    },
    {
      id: "system",
      label: "System",
      hint: "Models, setup, services",
      items: [
        { page: "cloud", label: "Cloud", hint: "remote integrations" },
        { page: "models", label: "Models", hint: "packs and availability" },
        { page: "settings", label: "Settings", hint: "paths and preferences" },
        { page: "setup", label: "Setup", hint: "dependency health" },
      ],
    },
  ];
}

const DEFAULT_GROUP_STATE: Record<NavGroupId, boolean> = {
  flow: true,
  delivery: true,
  labs: false,
  system: false,
};

export default function Sidebar({
  page,
  onNavigate
}: {
  page: Page;
  onNavigate: (p: Page) => void;
}) {
  const [openGroups, setOpenGroups] =
    useState<Record<NavGroupId, boolean>>(DEFAULT_GROUP_STATE);
  const navGroups = getNavGroups();

  const activeItem =
    navGroups.flatMap((group) => group.items).find((item) => item.page === page) || null;

  return (
    <div className="sidebar">
      <div className="sidebar-brandBlock">
        <div className="sidebar-logoShell">
          <img
            src="studio-logo.png"
            alt="EDMG Studio logo"
            className="sidebar-logo"
          />
        </div>
        <div className="sidebar-brandText">
          <div className="sidebar-eyebrow">NVIDIA Kit Runtime</div>
          <div className="sidebar-brand">EDMG NVIDIA Studio</div>
        </div>
      </div>
      <div className="small sidebar-tagline">
        NVIDIA Kit control for NIM planning, OpenUSD stages, RTX render jobs, and service diagnostics
      </div>

      <div className="sidebar-focusCard">
        <div className="sidebar-focusLabel">Current focus</div>
        <div className="sidebar-focusValue">{activeItem?.label || "Studio home"}</div>
        <div className="small">{activeItem?.hint || "Use Workspace as the integrated hub."}</div>
      </div>

      <div className="sidebar-nav">
        {navGroups.map((group) => {
          const groupIsActive = group.items.some((item) => item.page === page);
          const isOpen = groupIsActive || openGroups[group.id];

          return (
            <details
              key={group.id}
              className={`sidebar-group${groupIsActive ? " is-active" : ""}`}
              open={isOpen}
              onToggle={(event) => {
                const nextOpen = (event.currentTarget as HTMLDetailsElement).open;
                setOpenGroups((current) => ({ ...current, [group.id]: nextOpen }));
              }}
            >
              <summary className="sidebar-groupSummary">
                <span className="sidebar-groupTitle">{group.label}</span>
                <span className="sidebar-groupMeta">{group.hint}</span>
              </summary>

              <div className="sidebar-groupBody">
                {group.items.map((item) => (
                  <button
                    key={item.page}
                    onClick={() => onNavigate(item.page)}
                    onMouseEnter={() => preloadNavigationIntent(item.page)}
                    onFocus={() => preloadNavigationIntent(item.page)}
                    className={`sidebar-navButton${page === item.page ? " is-active" : ""}`}
                  >
                    <span className="sidebar-navCopy">
                      <span className="sidebar-navText">{item.label}</span>
                      <span className="sidebar-navMeta">{item.hint}</span>
                    </span>
                  </button>
                ))}
              </div>
            </details>
          );
        })}
      </div>

      <div className="sidebar-footer">
        <span className="badge">NVIDIA profile</span>
        <span className="badge">NIM 8001</span>
        <span className="badge">Kit / OpenUSD</span>
      </div>
    </div>
  );
}
