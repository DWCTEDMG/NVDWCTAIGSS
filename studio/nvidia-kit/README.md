# EDMG NVIDIA Kit App

This directory contains the starter Omniverse Kit version of EDMG Studio.

Do not treat this as an Electron replacement yet. The starter Kit app proves the
product boundary:

1. declares a Windows-launchable Kit app shape.
2. opens a starter OpenUSD stage.
3. loads audio timeline metadata.
4. calls the EDMG backend or NVIDIA service profile for planning.
5. writes normalized scene-plan metadata back to the project.
6. renders or previews a simple RTX scene.

Current starter layout:

```text
studio/nvidia-kit/
|- apps/edmg.nvidia.studio.kit
|- extensions/
|  |- edmg.timeline/
|  |- edmg.ai_director/
|  |- edmg.usd_schema/
|  `- edmg.render_queue/
|- sample_projects/
`- tools/
```

The app is still a starter, but the main user-facing extensions now do useful
work:

- `edmg.ai_director` opens a Kit panel for the first project-scoped workflow.
  From that panel you can enter a Studio `project_id`, fetch the saved NVIDIA
  scene plan, ask the backend/NIM planner for a refreshed plan, export the
  persisted USDA, and open the generated USDA preview as a local Kit stage.
- `edmg.timeline` opens a scene timeline panel from the configured starter
  scene-plan JSON and summarizes the OpenUSD cue boundaries.
- `edmg.render_queue` opens a backend readiness panel that checks the Studio
  worker status plus NVIDIA diagnostics/readiness, scans the Studio
  `outputs/nvidia/render_jobs/*.json` handoff files, opens the referenced USDA
  stage, captures RTX viewport frames, assembles MP4 output with FFmpeg when the
  job requests it, and reports the result back to the backend.

A full Kit App Template checkout, launcher scripts, and redistributable runtime
packaging should be added only after selecting and verifying a specific
Omniverse Kit SDK/App Template version on the target Windows build machine.

## Starter Sample

The first sample scene lives at:

- `sample_projects/audio_reactive_stage/stage.usda`
- `sample_projects/audio_reactive_stage/scene_plan.json`

Use it as the initial OpenUSD contract for the Kit proof-of-concept before
pulling in real user projects or large generated assets.

Validate the starter scene plan with:

```powershell
python studio/nvidia-kit/tools/validate_scene_plan.py studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json
```

Validate the Kit app/extension skeleton with:

```powershell
python studio/nvidia-kit/tools/validate_kit_layout.py
```

The dependency-free tests for the Kit panels can run without a local Kit SDK:

```powershell
python -m pytest `
  tests/test_nvidia_kit_panel.py `
  tests/test_nvidia_kit_runtime_panels.py `
  tests/test_nvidia_kit_backend_client.py `
  tests/test_nvidia_kit_layout.py `
  tests/test_nvidia_scene_plan_validator.py
```

Launch against a local Kit SDK by passing the SDK root that contains `kit.exe`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File studio/nvidia-kit/tools/launch_edmg_kit.ps1 `
  -KitSdkRoot "C:\path\to\kit-sdk-public" `
  -ProjectsRoot "C:\NVDWCTAIGSS\studio\edmg-studio\python_backend\data\projects"
```

The launcher auto-detects `ffmpeg` from `PATH` for MP4 assembly and sends it to
Kit through `/edmg/nvidia/ffmpeg_path`. Set `EDMG_FFMPEG_PATH` or pass
`-FfmpegPath` only when you need to force a specific FFmpeg binary.

To check extension resolution without opening the UI:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File studio/nvidia-kit/tools/launch_edmg_kit.ps1 `
  -KitSdkRoot "C:\path\to\kit-sdk-public" `
  -ListExtensions
```

Export the starter scene plan into a generated USDA file with:

```powershell
python studio/nvidia-kit/tools/export_scene_plan_usda.py `
  studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json `
  studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_scene_plan.usda
```

The `edmg.ai_director` extension includes a dependency-free backend client and a
Kit UI panel for the first API contracts:

- `GET /v1/nvidia/status`
- `POST /v1/usd/scene-plan`
- `POST /v1/nvidia/scene-plan`
- `GET /v1/projects/{project_id}/nvidia/scene-plan`
- `POST /v1/projects/{project_id}/nvidia/scene-plan`
- `POST /v1/projects/{project_id}/nvidia/scene-plan/export`
- `POST /v1/projects/{project_id}/nvidia/render-job/result`

Smoke those contracts from the Kit workspace with:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:8000 `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_backend.usda
```

To exercise the generated NVIDIA planner path instead of a static
`scene_plan.json`, add `--generate`:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:8000 `
  --generate `
  --title "EDMG NVIDIA Kit Smoke" `
  --duration-s 60 `
  --bpm 128 `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_nim.usda
```

To exercise the same project-scoped path that the Kit panel uses, add a real
Studio project id:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:8000 `
  --project-id YOUR_PROJECT_ID `
  --generate `
  --fetch-persisted `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_project.usda
```
