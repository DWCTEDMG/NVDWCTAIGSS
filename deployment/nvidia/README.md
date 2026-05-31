# NVIDIA Service Profile

This directory is the first deployment lane for the NVIDIA-first EDMG Studio
rework.

For the larger official NVIDIA product map and Windows packaging boundary, see
[`docs/NVIDIA_OFFICIAL_OPTIONS.md`](../../docs/NVIDIA_OFFICIAL_OPTIONS.md).
For the step-by-step local setup path, see
[`docs/NVIDIA_GETTING_STARTED.md`](../../docs/NVIDIA_GETTING_STARTED.md).

The immediate goal is to keep the existing backend usable while pointing its AI
routes at NVIDIA-hosted or locally hosted services. The longer-term goal is a
dedicated Omniverse Kit app plus a GPU service stack.

## Intended runtime split

```text
Windows package
|- Omniverse Kit app
|- USD editor and RTX viewport
|- project browser
`- service connection profiles

GPU service stack
|- EDMG backend/API gateway
|- NIM-compatible LLM endpoint
|- Riva-compatible ASR/TTS endpoint
|- NeMo training/customization jobs
|- optional Cosmos generation endpoint
`- optional ComfyUI/render workers
```

## Compose starter

Copy the example environment file and put local secrets in the ignored copy:

```powershell
Copy-Item deployment/nvidia/.env.example deployment/nvidia/.env.local
notepad deployment/nvidia/.env.local
```

Use this profile as an override with the existing Starlift Compose file:

```powershell
docker compose `
  --env-file deployment/nvidia/.env.local `
  -f docker-compose.starlift.yml `
  -f deployment/nvidia/docker-compose.nvidia.yml `
  up --build
```

By default this only reconfigures the existing backend for an OpenAI-compatible
NIM-style endpoint and NVIDIA mode metadata. Optional placeholder services live
behind Compose profiles because official NIM/Riva/NeMo images and model choices
depend on NVIDIA account access, EULAs, and target hardware.
The starter also reserves optional profile slots for Triton, Audio2Face/ACE, and
Cosmos so the repo can grow into the full NVIDIA lane without pretending those
private image names and model choices are universal.
The NeMo profile uses the official `nvcr.io/nvidia/nemo:26.04.00` framework
container as a workspace/job container; it is not an HTTP planner endpoint.

## Required host assumptions

- NVIDIA GPU and current driver.
- Docker with NVIDIA Container Toolkit for Linux containers, or a remote Linux
  GPU host.
- Access to any required NGC images and model licenses.
- Enough disk for model caches and render artifacts.

## Windows preflight

Run the local preflight after Docker is installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/check_nvidia_profile.ps1
```

The preflight checks Docker, visible NVIDIA GPUs, Docker NVIDIA runtime support,
env-file presence, masked NGC credential presence, and Compose syntax for both
the base override and optional `nvidia-local` profile. It never prints the key
value.

After the backend is running, smoke the NVIDIA API surface. This checks the
masked profile, host GPU, Docker NVIDIA runtime, NIM endpoint reachability, and
scene-plan-to-USDA contract:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/smoke_nvidia_backend.ps1
```

To prove the Kit-facing generated-planner path, run the AI Director smoke with
`--generate`. With a reachable NIM-compatible endpoint it uses that provider;
otherwise the backend reports or falls back according to the configured AI
provider:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:8000 `
  --generate `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_nim.usda
```

When the generate request includes an existing Studio `project_id`, the backend
also saves `last_nvidia_scene_plan` and a capped `nvidia_scene_plan_history`
entry in that project metadata. The smoke command uses a standalone sample ID,
so it remains a non-mutating contract check by default.
Use `--project-id YOUR_PROJECT_ID` to exercise the project-scoped route, then
call `/v1/projects/{project_id}/nvidia/scene-plan/export` or use the Workspace
Handoff button to write the USDA preview into `outputs/nvidia/`.
The Omniverse Kit `edmg.ai_director` panel uses those same routes for
project-scoped fetch, generate, export, and OpenUSD preview actions.
The Kit `edmg.render_queue` panel reads `/v1/worker/status` and
`/v1/nvidia/diagnostics` from the same backend URL, so it is a quick UI smoke
for local worker plus NVIDIA readiness before a dedicated RTX render worker is
added.
Riva ASR is now represented by `/v1/nvidia/riva/status` and the direct
`/v1/projects/{project_id}/nvidia/riva/transcribe` route. Set
`EDMG_NVIDIA_RIVA_ASR=1` or `EDMG_AI_ASR_PROVIDER=riva` only when the backend
environment has the NVIDIA Riva Python client installed and the Riva gRPC URI is
reachable; otherwise the normal Studio analysis path keeps using the existing
local ASR fallback.
The RTX render-worker lane now has a project-scoped handoff route:
`/v1/projects/{project_id}/nvidia/render-job`. It writes a JSON contract to
`outputs/nvidia/render_jobs/`. The Kit `edmg.render_queue` panel now consumes
those local JSON files, opens the referenced USDA stage, captures RTX viewport
frames into `outputs/nvidia/renders/`, assembles MP4 output with FFmpeg, and
reports completion or failure through
`/v1/projects/{project_id}/nvidia/render-job/result`. FFmpeg must be available
on `PATH`, passed to the Kit launcher with `-FfmpegPath`, or set with
`EDMG_FFMPEG_PATH`.

## Environment knobs

```powershell
$env:EDMG_AI_PROVIDER="openai_compat"
$env:EDMG_NVIDIA_NIM_URL="http://host.docker.internal:8001"
$env:EDMG_AI_OPENAI_COMPAT_BASE_URL="http://host.docker.internal:8001/v1"
$env:EDMG_AI_OPENAI_COMPAT_MODEL="your-nim-model"
$env:EDMG_NVIDIA_RIVA_URL="host.docker.internal:50051"
$env:EDMG_NVIDIA_RIVA_ASR="0"
$env:EDMG_NVIDIA_MODE="1"
$env:EDMG_NVIDIA_PROFILE="omniverse"
```

For remote services, use the remote base URL instead of
`host.docker.internal`.

Credential split:

- `NGC_API_KEY` is for NVIDIA/NGC image and gated asset access.
- `EDMG_AI_OPENAI_COMPAT_API_KEY` is for the planner endpoint itself when that
  endpoint requires bearer authentication.
- Both values must stay in the shell, OS secret store, or an ignored
  `.env.local` file.

## Local helper scripts

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/login_ngc.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/test_docker_gpu.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_backend_local.ps1
```

- `login_ngc.ps1` logs Docker into `nvcr.io` with `$oauthtoken` and a masked
  `NGC_API_KEY`.
- `test_docker_gpu.ps1` runs a configurable CUDA container probe with
  `--gpus all`.
- `start_nvidia_backend_local.ps1` starts the local dev backend on port `8000`
  with the NVIDIA env file loaded, while leaving NIM free to use host port
  `8001`.

## First validation target

1. Start the existing backend with this override.
2. Confirm `/health` returns the configured OpenAI-compatible provider.
3. Send a small `/v1/plan` request through the current AI service contract.
4. Send `/v1/nvidia/scene-plan` with a real Studio `project_id` and confirm
   `last_nvidia_scene_plan` is saved in that project metadata.
5. Confirm `/v1/nvidia/riva/status` reports the expected Riva URI and enable
   `EDMG_NVIDIA_RIVA_ASR=1` only after the Riva Python client and service are
   available.
6. Launch the Kit app and use the AI Director panel to fetch, regenerate,
   export, and open the same project-scoped USDA preview.
7. Wire Audio2Face/ACE after planner, USD, and speech paths are stable.

## Official image policy

Do not commit private registry credentials, pulled model weights, or accepted
license artifacts. Keep image names configurable because official NVIDIA service
images vary by product, release channel, access level, and model family.
