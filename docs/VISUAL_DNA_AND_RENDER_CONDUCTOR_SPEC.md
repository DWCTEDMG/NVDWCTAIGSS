# Visual DNA And Render Conductor Spec

## Goal

Add two additive systems to EDMG NVIDIA Workshop without reintroducing retired non-NVIDIA render paths:

- `Visual DNA`: persistent per-project creative memory
- `Render Conductor`: advisory orchestration that recommends the best engine mix per scene

The first implementation is intentionally non-destructive:

- no current route is replaced
- no current render worker is replaced
- no planner/reactive payload is broken
- conductor planning is advisory-only

## Current Repo Integration Points

The repo already has the right seams for this work:

- `studio/edmg-studio/python_backend/edmg_studio_backend/store/projects.py`
  Creates per-project `analysis/`, `outputs/`, and `jobs/` folders. `visual_dna.json` belongs under `analysis/`.
- `studio/edmg-studio/python_backend/edmg_studio_backend/services/workbench_bridge.py`
  Normalizes Planner Lab and Reactive Lab payloads into Studio-native structures.
- `studio/edmg-studio/python_backend/edmg_studio_backend/app.py`
  Owns the current analysis, planner import, reactive apply, and render routes.
- `studio/edmg-studio/python_backend/edmg_studio_backend/services/internal_video.py`
  Remains the canonical internal render runtime.
- `studio/edmg-studio/python_backend/edmg_studio_backend/schemas.py`
  Now carries the shared transport models for DNA and conductor planning.

## Added Backend Surfaces

- `studio/edmg-studio/python_backend/edmg_studio_backend/services/visual_dna.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/render_conductor/planner.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/render_conductor/__init__.py`
- shared models appended to `studio/edmg-studio/python_backend/edmg_studio_backend/schemas.py`

These files are scaffolding only. They do not yet change route behavior by themselves.

## Visual DNA Model

Visual DNA is stored per project at:

- `data/projects/<project_id>/analysis/visual_dna.json`

Canonical Pydantic model:

- `ProjectVisualDNA`

Key sections:

- `identity`
  Creative themes, motifs, palette, lighting language, camera language, texture language.
- `continuity`
  Subject anchors, environment anchors, transition rules, seed lineage.
- `prompt_guidance`
  Positive fragments, negative fragments, style bias.
- `engine_memory`
  Per-engine success/reject/repair memory with context hints.
- `quality_memory`
  Failure patterns and known-good engine/model combinations.
- `trait_memory`
  Provenance-aware observed traits.
- `fingerprints`
  Compact summaries of render outcomes used for learning.
- `learning_state`
  Confidence plus source counters and lock/soft field policy.

## Visual DNA Service API

Implemented in `services/visual_dna.py`.

### Persistence

- `visual_dna_path(project_dir)`
- `visual_dna_json_schema()`
- `create_default_visual_dna(project_id, project_name=None)`
- `load_visual_dna(project_dir, project_id=None, project_name=None)`
- `save_visual_dna(project_dir, dna)`

Writes are atomic via temp-file + `os.replace`, matching the repo’s safer project metadata pattern.

### Ingestion

- `ingest_planner_payload(...)`
  Learns from Planner Lab themes, imagery, approved scenes, prompt fragments, negative prompt fragments, and continuity notes.
- `ingest_reactive_payload(...)`
  Learns from render mode, motion schedules, approved reactive sections, and repair suggestions.
- `record_render_feedback(...)`
  Learns from render outcomes, palette/motif fingerprints, engine/model performance, and repeat failures.

### Prompt Guidance

- `build_prompt_hints(dna, limit=8)`
  Produces a lightweight hint bundle for future prompt enrichment without mutating the saved plan.

## Render Conductor Transport

Shared models live in `schemas.py`.

Primary models:

- `ProjectSnapshot`
- `RenderIntent`
- `RenderIntentSection`
- `RenderStep`
- `RenderSectionPlan`
- `AssemblyPlan`
- `FallbackBranch`
- `RenderPlan`

These models formalize the handoff between:

- project analysis
- saved storyboard plan
- timeline state
- project DNA memory
- engine recommendation logic

## Advisory Render Conductor

Implemented in `render_conductor/planner.py`.

Primary API:

- `build_advisory_render_plan(intent, snapshot, environment=None)`

### Inputs

- `RenderIntent`
  User constraints: quality tier, continuity priority, speed priority, style lock strength, allowed engines, fallback policy.
- `ProjectSnapshot`
  Project `analysis`, `plan`, `timeline`, and optional `visual_dna`.
- `environment`
  Current engine availability and optional quality/speed hints.

### Output

- `RenderPlan`
  Advisory-only plan that:
  - selects an engine per scene
  - emits a step graph
  - computes rough time/cost/risk estimates
  - records only NVIDIA-engine reroute branches

### Current Engine Families

- `internal`
- `comfyui_still`
- `comfyui_motion`
- `deforum_export`

`hosted_video` and `proxy` can still be parsed from older payloads for
compatibility, but the NVIDIA Workshop profile filters them out before planning
and never selects them for execution.

### Current Heuristics

The initial scoring layer considers:

- scene energy
- motion complexity
- continuity priority
- hero-frame bias
- style lock
- environment availability
- prior `engine_memory` from Visual DNA

This is deliberately heuristic, not authoritative. The first goal is to stabilize the contract and make recommendations inspectable.

## Intended Route Wiring

Not implemented yet, but the clean next integration points are:

1. Load DNA during:
   - planner lab import
   - reactive lab apply
   - render completion / approval flows
2. Add advisory endpoints:
   - `GET /v1/projects/{project_id}/visual_dna`
   - `POST /v1/projects/{project_id}/visual_dna/feedback`
   - `POST /v1/projects/{project_id}/render/conductor/plan`
3. Surface advisory plan data in the Render page before execution.

## Recommended Execution Order

### Phase 1

- keep conductor advisory-only
- wire DNA load/save into planner/reactive flows
- capture render feedback metadata

### Phase 2

- expose conductor recommendations in the UI
- allow manual accept/override
- persist accepted `RenderIntent` + `RenderPlan` alongside jobs

### Phase 3

- execute mixed-engine plans via the existing job system
- store per-step result metadata back into Visual DNA

## Non-Goals For This Pass

- no replacement of `render_internal_video_variant(...)`
- no new mandatory engine dependency
- no mixed-engine execution yet
- no UI rewrite
- no route decomposition bundled into this slice

## Validation Strategy

Focused backend tests should cover:

- Visual DNA persistence and schema generation
- Planner Lab ingestion
- Reactive Lab ingestion
- Render feedback learning
- Advisory conductor routing under different engine availability and project priorities

That is enough to validate the contract before deeper route wiring.
