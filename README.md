# EDMG NVIDIA Workshop Monolith Repo

This repository is the single authoritative source tree for EDMG NVIDIA Workshop.

The Studio code is merged into the NVIDIA Workshop. The NVIDIA Kit shell, OpenUSD/RTX workflow,
FastAPI backend, vendored EDMG engine packages, setup flow, release packaging,
and support tooling all converge on one product path:

- `studio/nvidia-kit/` for the primary NVIDIA desktop runtime
- `studio/edmg-studio/python_backend/` for the orchestration backend

The older standalone-engine installers, Electron-first launch path, and extra
README entrypoints have been retired so the repo presents one NVIDIA product
instead of multiple competing workflows.

The legacy standalone web UI prototypes have also been retired. Their planning
and audio-reactive capabilities now live inside the Workshop workbenches:

- `studio/edmg-studio/src/workbenches/AiNlpWorkbench.tsx`
- `studio/edmg-studio/src/workbenches/AudioReactiveWorkbench.tsx`

## Canonical launch

Windows:

```bat
RUN_ME.bat
```

macOS/Linux:

```bash
./run_me.sh
```

Those launchers start the NVIDIA Kit runtime through
`deployment/nvidia/start_nvidia_runtime_local.ps1`.

## Canonical product layout

- `studio/edmg-studio/`
  React control surface, packaging scripts, release validation, and the only
  Node/pnpm package root in the repo. This supports the NVIDIA Workshop instead
  of defining a separate app.
- `studio/nvidia-kit/`
  Omniverse Kit app, EDMG extensions, OpenUSD sample projects, RTX render queue,
  AI Director panel, and dependency-light validators.
- `studio/edmg-studio/python_backend/`
  FastAPI backend plus the vendored `enhanced_deforum_music_generator` and
  `deforum_music` engine packages that power planning, analysis, schedules, and
  EDMG Core support.
- `studio/edmg-studio/tools/launcher_gui.py`
  Shared NVIDIA launcher, Workshop Home bootstrap flow, and backend controls.
- `deployment/nvidia/`
  NVIDIA runtime, Kit staging, service profile, diagnostics, and smoke helpers.

## Workshop Home

The Workshop separates the app install directory from the heavy runtime storage
root. The `Workshop Home` contains:

- `data`
- `models`
- `cache`
- `logs`
- `external`
- `kit-session`

That keeps large downloads, render caches, and external tools off the app
install path and allows migration to another drive or mount such as `D:\` on
Windows or `/mnt/media/EDMG-NVIDIA-Workshop` on Linux.

## JS Tooling

- Run JS commands for the React control surface from `studio/edmg-studio/`.
- The canonical package manager is `pnpm@10.33.0`, pinned in `studio/edmg-studio/package.json`.
- NVIDIA Kit packaging is staged by `deployment/nvidia/stage_nvidia_kit_app.ps1`.

## Release, strategy, and operator docs

- [studio/edmg-studio/README.md](./studio/edmg-studio/README.md)
- [RELEASE.md](./RELEASE.md)
- [docs/STUDIO_RELEASE_RUNBOOK.md](./docs/STUDIO_RELEASE_RUNBOOK.md)
- [docs/STUDIO_REPO_MAP.md](./docs/STUDIO_REPO_MAP.md)
- [studio/edmg-studio/docs/STUDIO_MODULARITY.md](./studio/edmg-studio/docs/STUDIO_MODULARITY.md)
- [docs/STUDIO_FORGE.md](./docs/STUDIO_FORGE.md)
- [docs/UNIFIED_INTERNAL_RENDERER_PLAN.md](./docs/UNIFIED_INTERNAL_RENDERER_PLAN.md)
- [docs/VISUAL_DNA_AND_RENDER_CONDUCTOR_SPEC.md](./docs/VISUAL_DNA_AND_RENDER_CONDUCTOR_SPEC.md)
- [docs/STARLIFT_VM_DEPLOY.md](./docs/STARLIFT_VM_DEPLOY.md)
- [docs/GCP_GPU_VM_DEPLOY.md](./docs/GCP_GPU_VM_DEPLOY.md)
- [docs/AWS_MODEL_CACHE.md](./docs/AWS_MODEL_CACHE.md)
- [docs/NVIDIA_GETTING_STARTED.md](./docs/NVIDIA_GETTING_STARTED.md)
- [docs/NVIDIA_OFFICIAL_OPTIONS.md](./docs/NVIDIA_OFFICIAL_OPTIONS.md)
- [docs/NVIDIA_OMNIVERSE_STUDIO.md](./docs/NVIDIA_OMNIVERSE_STUDIO.md)
- [docs/AI_PROVIDERS.md](./docs/AI_PROVIDERS.md)
- [docs/HF_VIDEO_MODELS.md](./docs/HF_VIDEO_MODELS.md)

## Test strategy

- Repo-level tests live under `tests/` and run from the repo root with `python -m pytest`.
- Backend package tests live under `studio/edmg-studio/python_backend/` and run from that directory with `python -m pytest`.
- To run both scopes in sequence from the repo root, use `python scripts/run_pytest_scopes.py`.
- The root `pytest.ini` intentionally excludes the backend-local pytest scope so `python -m pytest` from the repo root stays a repo-level command.

## Recommended Local Stack

- Planner default: NVIDIA NIM / OpenAI-compatible `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- OpenAI-compatible default base URL: `http://127.0.0.1:8001/v1`
- Broad still-image default: SDXL Base 1.0
- Fast still-image option: SD3.5 Large Turbo
- Reference still guidance: SD3.5 ControlNet Blur, Canny, and Depth
- Primary HF video backend: Wan2.2 TI2V 5B
- Short image-to-video option: SVD XT Img2Vid

## Hardware Tiers

- Mid-range: NVIDIA NIM + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny
- High-end: NVIDIA NIM + RTX/OpenUSD Kit render jobs + SD3.5 Blur/Canny/Depth + Wan2.2 TI2V 5B

## Compatibility shims

- Repo-root `sitecustomize.py` and the repo-root `librosa/` package are source-tree compatibility shims for development and tests.
- Packaged/backend install flows rely on the declared Python dependencies and do not package those repo-root shims.

## Unreal bridge status

- Studio-side bridge: usable. The repo can preview, export, build an Unreal import plan, and import returned renders back into canonical project outputs.
- Unreal-side runtime integration: partial. There is no verified in-editor smoke test on this machine, no packaged Unreal plugin/module, no live OSC/WebSocket/Remote Control execution path, no one-click Unreal render job launcher, and no deeper Sequencer build beyond the first importer pass.

## Compatibility note

The repo root now keeps one launch path and monolith-level docs. Runtime code,
packaging logic, backend engine packages, and operator tooling live under
`studio/edmg-studio/` and serve the NVIDIA Workshop runtime.
