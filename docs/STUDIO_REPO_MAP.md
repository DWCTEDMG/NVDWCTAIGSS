# EDMG Studio Repo Map

This repository now presents one product surface: EDMG NVIDIA Studio.

## Canonical product tree

- `studio/edmg-studio/`
  Legacy Electron/React compatibility client and Node tooling.
- `studio/edmg-studio/src/`
  React/Vite frontend retained for migration and browser/client experiments.
- `studio/edmg-studio/main.mjs`, `preload.cjs`, `main-process/`
  Legacy Electron desktop shell and runtime glue.
- `studio/edmg-studio/python_backend/`
  FastAPI backend plus the vendored EDMG engine packages.
- `studio/edmg-studio/tools/launcher_gui.py`
  Shared NVIDIA launcher, Studio Home bootstrap flow, backend controls, and
  legacy Electron dev controls.
- `studio/edmg-studio/packaging/windows/`
  Windows-first packaged release automation.
- `studio/edmg-studio/packaging/linux/`
  Legacy Linux AppImage packaging notes and operator guidance.
- `studio/nvidia-kit/`
  Primary Omniverse Kit app source, AI Director/Timeline/Render Queue extensions,
  OpenUSD sample project, and dependency-light validators.
- `deployment/nvidia/`
  NVIDIA runtime launchers, Kit staging, service profiles, diagnostics, and
  smoke tests for NIM/Riva/NeMo/Triton/Omniverse-style GPU services.

## Canonical entrypoints

- `RUN_ME.bat`
- `./run_me.sh`
- `deployment/nvidia/start_nvidia_runtime_local.ps1`

## Internal support surfaces

These are still part of the monolith, but they are support tooling rather than
separate user-facing products:

- `studio/edmg-studio/scripts/`
- `studio/edmg-studio/tools/edmgctl/`

Important: these support paths exist to serve the NVIDIA Kit product. They are
not alternative primary install flows.

## Legacy surfaces retired from the public repo entry

The old standalone installers, duplicate desktop shell, extra top-level README
entrypoints, and archived standalone web UI prototypes have been removed from
the active product surface.

The Studio app now owns those workflows directly through:

- `studio/edmg-studio/src/workbenches/AiNlpWorkbench.tsx`
- `studio/edmg-studio/src/workbenches/AudioReactiveWorkbench.tsx`

## Release and validation

Primary Studio docs:

- [README.md](../README.md)
- [studio/edmg-studio/README.md](../studio/edmg-studio/README.md)
- [RELEASE.md](../RELEASE.md)
- [docs/STUDIO_RELEASE_RUNBOOK.md](./STUDIO_RELEASE_RUNBOOK.md)
- [docs/NVIDIA_OMNIVERSE_STUDIO.md](./NVIDIA_OMNIVERSE_STUDIO.md)

Key legacy client validation commands run from `studio/edmg-studio/`:

- `pnpm run check:tooling`
- `pnpm run validate:desktop`
- `pnpm run dist:win`
- `pnpm run dist:linux`
- `pnpm run validate:packaged-customer-flow`
- `pnpm run validate:packaged-upgrade-proof`
- `pnpm run validate:release:linux`

Canonical packaged desktop version source:

- `studio/edmg-studio/package.json#version`
