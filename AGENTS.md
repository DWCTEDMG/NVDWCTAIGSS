# AGENTS.md

## Product / UX priority (user preference)

This is the **NVIDIA Workshop** variant of EDMG Studio. The React control
surface under `studio/edmg-studio/src/` is the priority: when adding or changing
functionality, surface it in the UI (Render, Workspace, Models, Settings, etc.),
not just as a backend API endpoint, unless the user says otherwise.

## Cursor Cloud specific instructions

EDMG NVIDIA Workshop is the NVIDIA-profile sibling of EDMG Studio. It shares the
same FastAPI backend package (`edmg_studio_backend`) and React/Vite frontend, and
adds an NVIDIA Omniverse Kit runtime under `studio/nvidia-kit/`. Two services
matter for local development:

- **Backend** — FastAPI app in `studio/edmg-studio/python_backend/`.
- **Frontend** — React/Vite control surface in `studio/edmg-studio/`
  (Vite dev server), normally wrapped by an Electron/NVIDIA shell.

The update script installs the frontend deps (`pnpm install` from
`studio/edmg-studio`). The heavy Python backend deps (torch / diffusers /
transformers / librosa / spacy / nltk, etc.) are installed **once** from the
sibling `DWCTGenerativeSoundStudio` repo's backend
(`pip install -e ".[studio_bundle,test]"`) and shared via the user site-packages,
so this repo's backend reuses them. This repo's own NVIDIA-only extras
(`nvidia-riva-client`, `tritonclient`) are **not** installed here — they target
the NVIDIA runtime and are not needed for local dev.

### Backend
- Use `python3` (there is no `python` alias on this VM).
- Start the backend with the module form from inside `python_backend` so the
  **local NVIDIA-variant** package is imported (cwd takes precedence over the
  sibling repo's editable install):
  `cd studio/edmg-studio/python_backend && python3 -m edmg_studio_backend serve --host 127.0.0.1 --port 7864`.
  (Pick a port that does not collide with the EDMG Studio backend if both run.)
- **AI provider gotcha:** the NVIDIA profile only supports
  `EDMG_AI_PROVIDER=openai_compat` pointed at an OpenAI-compatible / NVIDIA NIM
  endpoint (`EDMG_NVIDIA_NIM_URL`). There is **no `rule_based` fallback** (unlike
  EDMG Studio), so AI planning (`/v1/projects/{id}/plan`) requires a running
  NIM/OpenAI-compatible server, which is **not present here**. The non-AI core
  still works without it: project CRUD (`/v1/projects`), audio upload, and
  `analyze_audio` (librosa) all function offline.
- `studio/nvidia-kit/` (Omniverse Kit / OpenUSD / RTX) cannot run in this VM
  (no NVIDIA Omniverse runtime/GPU).

### Frontend / pnpm
- Always run `pnpm` from inside `studio/edmg-studio`. From the repo root,
  corepack may pick a newer pnpm that fails the pinned `packageManager` check.
- `pnpm run dev` runs Vite **and** Electron concurrently; Electron cannot launch
  headless in the cloud VM. To exercise the UI in a browser, run Vite alone:
  `pnpm exec vite --host 127.0.0.1 --port 5174 --strictPort`.
- **Browser-only gotcha:** open the UI with the backend URL as a query param,
  e.g. `http://127.0.0.1:5174/?backendUrl=http://127.0.0.1:7864`. Without it the
  browser fallback in `src/components/api.ts` recurses (no Electron `window.edmg`
  bridge). This is a browser-dev-only quirk, not a setup problem.
- Backend CORS is open, so a browser can call the backend cross-origin.

### Tests (known results on Linux)
- Frontend: `pnpm run lint` and `pnpm run typecheck` pass clean; `pnpm run test:ui`
  → 45 pass (23 files).
- Backend: `cd studio/edmg-studio/python_backend && python3 -m pytest` → 79 pass /
  6 fail. The 6 failures are **pre-existing test/code drift**, not an environment
  problem: they live in
  `enhanced_deforum_music_generator/tests/test_audio_analysis_regressions.py` and
  monkeypatch `edmg_ai_service.asr._load_model`, but the NVIDIA variant's
  `edmg_ai_service/asr.py` is intentionally Riva-only and exposes no `_load_model`.
- Repo-level tests live under `tests/`; run from the repo root with
  `python3 -m pytest`, or run both scopes with
  `python3 scripts/run_pytest_scopes.py`.

### Storage
- Backend project data is written to
  `studio/edmg-studio/python_backend/data/` (gitignored) when `EDMG_STUDIO_HOME`
  is not set. Set `EDMG_STUDIO_HOME` to relocate data/models/cache/logs.
