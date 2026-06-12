# Studio Modularity Roadmap

## Goal

Make EDMG NVIDIA Workshop more personal and modular without changing the core application shape:

- keep top-level tabs stable
- let users customize the panels inside pages
- allow theme selection and future theme packs
- evolve Workshop Forge into the builder and preview surface for these capabilities
- keep Unreal Engine optional if it is added later

This plan is additive only. It does not replace:

- the Electron shell
- the React frontend
- the FastAPI backend
- the Setup Wizard
- the default internal renderer
- optional ComfyUI sidecars
- model management
- packaging or backend spawn flow

## Principles

1. Top-level navigation stays canonical.
2. Per-page internals can become modular.
3. Layout state is user-scoped UI state, not project content.
4. Themes are frontend-only token sets.
5. Runtime integrations remain optional providers or bridges.
6. Workshop Forge should preview and advise before it executes.

## Current Progress

The current frontend implementation already includes:

- a global Studio theme provider
- local per-page layout persistence
- shared layout customization controls
- local layout profile slots (`Personal`, `Focus`, `Technical`, `Presentation`)
- modular rollout on `Dashboard`, `Projects`, `Settings`, `Models`, `Workshop Forge`, `Outputs`, `Render Queue`, `Cloud`, `AI Planner Lab`, `Reactive Lab`, and `EDMG Director`
- phase-2 progress already landed for named layout profiles and Workshop Forge layout/preview surfaces

These changes are frontend-only. They do not alter backend contracts, Setup Wizard flow,
model installs, render defaults, desktop backend spawning, or packaging behavior.

## Page Modularity Model

Each customizable page should define a panel registry:

```ts
type StudioPanelDefinition = {
  id: string;
  page: string;
  label: string;
  description: string;
  defaultVisible: boolean;
  advancedOnly?: boolean;
};
```

Each user gets saved layout state per page:

```ts
type StudioPageLayoutState = {
  order: string[];
  hidden: string[];
};
```

Phase 1 uses local frontend persistence. A later phase can move this into desktop-managed user settings under Workshop Home if cross-machine or multi-window sync becomes important.

## Theme Model

Themes should be token-driven and safe:

- colors
- borders
- panel backgrounds
- sidebar/main shell accents
- button tones

Do not theme by rewriting page markup. Theme by overriding CSS variables.

Future extensions:

- font packs
- density modes
- custom user theme JSON
- per-theme background treatments

## Workshop Forge Role

Workshop Forge should evolve into the read-only builder shell for modular Workshop behavior:

- runtime capability inspection
- theme preview
- page layout preview
- workflow compatibility preview
- patch/export preview
- optional bridge preview for external runtimes

Workshop Forge should not bypass the canonical Workspace, Render, Models, or Setup flows.

## Unreal Engine Direction

Unreal should only be added as an optional bridge, never as a required runtime.

Recommended shapes:

- export target for shot and scene metadata
- render handoff target
- control bridge over files, HTTP, WebSocket, OSC, or Remote Control
- optional provider surfaced by Workshop Forge previews

Not recommended:

- embedding Unreal into Studio
- making Unreal the default render path
- changing Setup or packaging to require Unreal

Current status:

- the repo now has a non-destructive Unreal bridge MVP for preview, export bundles, import-plan generation, and return-import back into Studio outputs
- Unreal remains optional, preview-oriented, and non-authoritative
- there is still no required Unreal runtime dependency, packaged plugin, or live control execution path

## Phase Plan

Some phase-2 UI pieces are already in the repo, and a limited Unreal bundle MVP
exists, but the roadmap below remains the intended direction.

### Phase 1

- frontend-only theme provider
- per-page layout state hook
- layout customizer UI
- apply layout customization to safe, non-critical pages first
- document the Unreal bridge strategy and keep any bridge work non-destructive

### Phase 2

- expand modular layouts to more operational pages
- add named layout profiles
- add density and font choices
- add Workshop Forge layout/theme previews

### Phase 3

- desktop-managed persistence for appearance/layout state
- optional import/export of themes and layouts
- preview-only Unreal bridge validation in Workshop Forge

## Current Implementation Boundary

The first implementation pass should stay away from:

- backend contracts
- setup/install flows
- render defaults logic
- model install logic
- backend spawning
- packaging scripts

Only UI presentation, ordering, visibility, and appearance should change.
The current Unreal bridge MVP and Outputs import/export surfaces are the main
exception, and they remain additive rather than execution-authoritative.
