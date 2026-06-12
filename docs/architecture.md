# EDMG NVIDIA Workshop Architecture

## Goals
- EDMG NVIDIA Workshop provides an Omniverse Kit desktop shell backed by a local FastAPI service.
- The backend exposes a stable JSON API envelope: `{ "ok": true|false, ... }`.
- Errors intended for users follow a structured `UserFacingError` format.

## High-level components
### Desktop App (NVIDIA Kit)
- App root: `studio/nvidia-kit/`
- Launch: `deployment/nvidia/start_nvidia_runtime_local.ps1`
- Kit app: `studio/nvidia-kit/apps/edmg.nvidia.studio.kit`
- Extensions: `studio/nvidia-kit/extensions/`

### React Control Surface (Node/TS)
- Package root: `studio/edmg-studio/`
- Package manager: `pnpm@10.33.0` via `packageManager`
- Typecheck: `pnpm run typecheck`
- Dev: `pnpm run dev`

### Python Backend (FastAPI)
- Run: `edmg-studio-backend serve --host 127.0.0.1 --port 7863`
- `edmg_studio_backend/app.py` sets up routes + exception handlers
- `edmg_studio_backend/errors.py` defines user-facing error model
- Logging via `enhanced_deforum_music_generator/utils/logging_utils.py`

## Data flow
1. UI triggers an action (generate / render / process).
2. UI calls backend endpoint.
3. Backend validates input, runs the job, logs progress.
4. Backend returns `{ ok: true, ... }` or `{ ok: false, error: {...} }`.

## Conventions
### API envelope
- Success: `{ "ok": true, "data": ... }` or `{ "ok": true, ... }`
- Failure: `{ "ok": false, "error": { "message": "...", "hint": "...", "code": "..." } }`

### Errors
- Prefer raising `UserFacingError(message, hint, code, status_code)`
- Unknown exceptions map to `{ ok:false, error:{ code:"INTERNAL" } }`

### Logging
- Use the project logger helper to keep formatting consistent
- Avoid printing secrets/tokens/API keys

## Testing
- Repo-level Python: `python -m pytest` from the repo root
- Backend package Python: `python -m pytest` from `studio/edmg-studio/python_backend` after `pip install -e ".[studio_bundle,test]"`
- Combined Python scope: `python scripts/run_pytest_scopes.py` from the repo root
- Node/TS: Vitest + jsdom smoke tests under `studio/edmg-studio/src/test`
