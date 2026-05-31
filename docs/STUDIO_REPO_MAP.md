# EDMG Studio Repo Map

This repository now presents one product surface: EDMG Studio.

## Canonical product tree

- `studio/edmg-studio/`
  Canonical Electron desktop app.
- `studio/edmg-studio/src/`
  React/Vite frontend.
- `studio/edmg-studio/main.mjs`, `preload.cjs`, `main-process/`
  Desktop shell and runtime glue.
- `studio/edmg-studio/python_backend/`
  FastAPI backend plus the vendored EDMG engine packages.
- `studio/edmg-studio/tools/launcher_gui.py`
  Shared dev launcher and Studio Home bootstrap flow.
- `studio/edmg-studio/packaging/windows/`
  Windows-first packaged release automation.
- `studio/edmg-studio/packaging/linux/`
  Linux AppImage packaging notes and operator guidance.
- `studio/nvidia-kit/`
  Omniverse Kit app source, AI Director/Timeline/Render Queue extensions,
  OpenUSD sample project, and dependency-light validators.
- `deployment/nvidia/`
  NVIDIA service-profile starter for NIM/Riva/NeMo-style GPU services.

## Canonical entrypoints

- `RUN_ME.bat`
- `./run_me.sh`

## Internal support surfaces

These are still part of the monolith, but they are support tooling rather than
separate user-facing products:

- `studio/edmg-studio/scripts/`
- `studio/edmg-studio/tools/edmgctl/`

Important: these support paths exist to serve the Studio product. They are not
alternative install flows.

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

Key validation commands run from `studio/edmg-studio/`:

- `pnpm run check:tooling`
- `pnpm run validate:desktop`
- `pnpm run dist:win`
- `pnpm run dist:linux`
- `pnpm run validate:packaged-customer-flow`
- `pnpm run validate:packaged-upgrade-proof`
- `pnpm run validate:release:linux`

Canonical packaged desktop version source:

- `studio/edmg-studio/package.json#version`
