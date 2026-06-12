# EDMG Director

`edmg-director` is a ChatGPT Apps SDK package for reviewing EDMG NVIDIA Workshop planning output inside ChatGPT and applying approved variants back into the Workshop timeline.

It is shaped as an `interactive-decoupled` app:

- Read-only tools expose EDMG project search and fetch flows.
- Planning runs through the existing EDMG backend instead of duplicating planner logic.
- `generate_plan_preview` opens a React review board widget.
- `apply_plan_variant` is a widget-triggered mutation tool that writes the selected variant into the EDMG timeline.
- `import_planner_lab_payload` and `apply_reactive_handoff` let structured planning and reactive payloads round-trip back into Studio.
- The widget now includes a Studio handoff panel that can import the current preview as planner state, inspect motion curves, and apply an editable reactive JSON draft.

## Package layout

- `src/server.ts`
  MCP server, tool registration, streamable HTTP transport, and static asset serving.
- `src/widget/`
  React review-board source.
- `src/widget/reactiveScheduler.ts`
  Shared reactive schedule generator and parser used by both the widget and focused tests.
- `vite.config.ts`
  Bundles the widget into versioned assets under `assets/`.
- `tsconfig.server.json`
  Emits the MCP server into `dist-server/` so Studio can launch it as a managed sidecar in packaged builds.
- `assets/`
  Generated at build time. The server reads `review-board.html` from here and rewrites local asset paths to the configured public base URL.
- `dist-server/`
  Generated at build time. Contains the runnable Node entrypoint used by both local `pnpm start` and the Studio-managed runtime.

## Tools

- `search`
  Find EDMG NVIDIA Workshop projects by name or ID.
- `fetch`
  Load compact project context: analysis summary, plan count, and timeline summary.
- `analyze_project_audio`
  Trigger backend audio analysis for a project with uploaded audio.
- `generate_plan_preview`
  Call EDMG `/plan`, then render the React review board in ChatGPT.
- `apply_plan_variant`
  App-only tool used by the widget to apply a selected variant to the Workshop timeline.
- `import_planner_lab_payload`
  Import planner-style `analysis`, `plan`, and `settings` payloads into a Studio project and optionally apply the resulting timeline.
- `apply_reactive_handoff`
  Apply reactive cue events, keyframes, schedules, and handoff metadata into a Studio project timeline.

## Environment

- `EDMG_BASE_URL`
  Base URL for the EDMG NVIDIA Workshop backend. Defaults to `http://127.0.0.1:7863`.
- `PORT`
  Local port for this MCP app server. Defaults to `3001`.
- `HOST`
  Local bind host for the MCP server. Defaults to `127.0.0.1`. Set this only if you explicitly need a non-localhost bind.
- `BASE_URL`
  Public base URL that ChatGPT should use for static asset references. Set this when serving behind a tunnel or remote hostname.

## Local run

```bash
pnpm install
pnpm start
```

`pnpm start` builds the widget plus the runnable server output, then starts the MCP server on `http://localhost:3001/mcp`.

For widget-only iteration:

```bash
pnpm run dev:widget
```

## Validation

Low-cost validation for this package:

```bash
pnpm run typecheck
pnpm run test
pnpm run build
```

`pnpm run test` starts a stub EDMG backend plus the local MCP server, smoke-tests the registered tools through a real Streamable HTTP MCP client, and verifies the reactive schedule generator directly.

Runtime validation still requires a live EDMG backend plus ChatGPT Developer Mode or another MCP Apps-capable host.

If you change `BASE_URL`, restart the Director server and refresh the ChatGPT app/connector so it reloads the updated widget metadata and asset URLs.

## Studio-managed runtime

`studio/edmg-studio` can bundle and launch this package as a managed sidecar. The Studio release prep step copies:

- `assets/`
- `dist-server/`
- `node_modules/`
- `package.json`

into `studio/edmg-studio/electron-resources/director/`, then the Electron main process launches `dist-server/server.js` with the packaged Electron runtime in Node mode.
