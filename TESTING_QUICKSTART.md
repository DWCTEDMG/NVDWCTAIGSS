# EDMG NVIDIA Workshop quick test

The supported launch path is the NVIDIA profile end to end.

```powershell
.\RUN_ME.bat
```

`RUN_ME.bat` forwards to:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\nvidia\start_nvidia_runtime_local.ps1
```

That script starts the NVIDIA Workshop runtime with the NVIDIA profile, CUDA-only
render requirements, NIM/OpenAI-compatible planning, and the staged Kit/Workshop
path when requested.

## Prereqs

- NVIDIA driver and CUDA-capable GPU
- Python `>=3.10,<3.14`
- Node.js LTS and pnpm
- FFmpeg on PATH for MP4 assembly
- NVIDIA NIM or OpenAI-compatible NVIDIA endpoint at the configured base URL

## Validation

From `studio/edmg-studio/`:

```powershell
pnpm run check:tooling
pnpm run typecheck
pnpm run test:ui
pnpm run build
pnpm run validate:electron-bridge
pnpm run validate:packaged-nvidia-setup
```

From the repo root:

```powershell
python -m pytest
python scripts/run_pytest_scopes.py
```

The packaged setup proof is now:

```powershell
pnpm run validate:packaged-nvidia-setup
```
