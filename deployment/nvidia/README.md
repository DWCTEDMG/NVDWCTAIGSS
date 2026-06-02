# NVIDIA Service Profile

This directory is the first deployment lane for the NVIDIA-first EDMG Studio
rework.

For the larger official NVIDIA product map and Windows packaging boundary, see
[`docs/NVIDIA_OFFICIAL_OPTIONS.md`](../../docs/NVIDIA_OFFICIAL_OPTIONS.md).
For the Kit runtime packaging decision, see
[`docs/NVIDIA_KIT_RUNTIME_DECISION.md`](../../docs/NVIDIA_KIT_RUNTIME_DECISION.md).
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

With Kit already running through `start_nvidia_runtime_local.ps1` or
`launch_edmg_kit.ps1 -AutoRunRenderWorker`, smoke the actual RTX render-job
handoff with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/smoke_nvidia_kit_render_job.ps1
```

The script can select the first project returned by `/v1/projects`, or you can
pin one with `-ProjectId`.

To prove the Kit-facing generated-planner path, run the AI Director smoke with
`--generate`. With a reachable NIM-compatible endpoint it uses that provider;
otherwise the backend reports or falls back according to the configured AI
provider:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:7863 `
  --generate `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_nim.usda
```

When the generate request includes an existing Studio `project_id`, the backend
also saves `last_nvidia_scene_plan` and a capped `nvidia_scene_plan_history`
entry in that project metadata. The smoke command uses a standalone sample ID,
so it remains a non-mutating contract check by default.
On this workstation, use backend URL `http://127.0.0.1:7863` and
`--project-id febd7dcbeb464b79bf4db78a7b6151c4` to exercise the
project-scoped route, then
call `/v1/projects/{project_id}/nvidia/scene-plan/export` or use the Workspace
Handoff button to write the USDA stage into `outputs/nvidia/`. Generated USDA
now includes a `Production` scope with render settings, render products, asset
slots, shot cameras, shot lights, and per-shot render product metadata.
The Omniverse Kit `edmg.ai_director` panel uses those same routes for
project-scoped fetch, generate, export, and OpenUSD preview actions.
The Kit `edmg.render_queue` panel reads `/v1/worker/status` and
`/v1/nvidia/diagnostics` from the same backend URL, so it is a quick UI smoke
for local worker plus NVIDIA readiness. With `-AutoRunRenderWorker`, it also
stays alive as the RTX render queue consumer.
Direct optional-service checks are available at `/v1/nvidia/services` and
`/v1/nvidia/services/{service_id}/status` for `nim`, `riva`, `omniverse`,
`nemo`, `triton`, `audio2face`, `ace`, and `cosmos`.
Configured HTTP services can also be invoked through
`/v1/nvidia/services/{service_id}/invoke`; this is the generic functional bridge
for Audio2Face, ACE, Cosmos, Omniverse, NeMo, and NIM-style services. Typed
wrappers are available for `/v1/nvidia/audio2face/generate`,
`/v1/nvidia/ace/generate`, `/v1/nvidia/cosmos/infer`,
`/v1/nvidia/triton/shot-features`, and `/v1/nvidia/triton/infer`. Cosmos uses
the official NIM `/v1/infer` path by default. Audio2Face/ACE gateway paths are
deployment-specific, so pass `path` or set
`EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH` / `EDMG_NVIDIA_ACE_GENERATE_PATH`.
Triton uses the v2 inference protocol. The repository includes a tiny
`edmg_echo` smoke model and the default app-specific `edmg_shot_features`
Triton Python-backend model under `deployment/nvidia/triton-model-repository/`.
`edmg_shot_features` accepts a scene-plan JSON tensor and returns render-facing
shot features, aggregate energy, scene count, and render hints through real
`/v2/models/{model}/infer` calls.
Riva ASR is now represented by `/v1/nvidia/riva/status` and the direct
`/v1/projects/{project_id}/nvidia/riva/transcribe` route. Set
`EDMG_NVIDIA_RIVA_ASR=1` or `EDMG_AI_ASR_PROVIDER=riva` only when the backend
environment has the NVIDIA Riva Python client installed and the Riva gRPC URI is
reachable; otherwise the normal Studio analysis path keeps using the existing
local ASR fallback.
For the default local Riva `2.19.0` lane, install the backend Riva extra and
prepare NVIDIA's Quick Start scripts before enabling ASR:

```powershell
& C:\NVDWCTAIGSS\studio\edmg-studio\python_backend\.venv\Scripts\python.exe -m pip install -e "C:\NVDWCTAIGSS\studio\edmg-studio\python_backend[riva]"

powershell -NoProfile -ExecutionPolicy Bypass `
  -File C:\NVDWCTAIGSS\deployment\nvidia\prepare_nvidia_riva_quickstart.ps1
```

After the scripts are downloaded/configured, run `riva_init.sh` and
`riva_start.sh` through the same helper with `-SkipDownload -RunInit` and
`-SkipDownload -RunStart`. NVIDIA documents the Riva 2.19 Quick Start flow as
downloading `nvidia/riva/riva_quickstart:2.19.0`, editing `config.sh`, then
running `bash riva_init.sh` and `bash riva_start.sh`.
The RTX render-worker lane now has a project-scoped handoff route:
`/v1/projects/{project_id}/nvidia/render-job`. It writes a JSON contract to
`outputs/nvidia/render_jobs/`. The Kit `edmg.render_queue` panel now consumes
those local JSON files, opens the referenced USDA stage, captures RTX viewport
frames into `outputs/nvidia/renders/`, assembles MP4 output with FFmpeg, and
reports completion or failure through
`/v1/projects/{project_id}/nvidia/render-job/result`. FFmpeg must be available
on `PATH`, passed to the Kit launcher with `-FfmpegPath`, or set with
`EDMG_FFMPEG_PATH`. Launch Kit with `-AutoRunRenderWorker` to keep the queue
consumer polling for newly created jobs; the default poll interval is five
seconds and can be changed with `-RenderWorkerPollSeconds`.

## Environment knobs

```powershell
$env:EDMG_AI_PROVIDER="openai_compat"
$env:EDMG_NVIDIA_NIM_URL="http://host.docker.internal:8001"
$env:EDMG_AI_OPENAI_COMPAT_BASE_URL="http://host.docker.internal:8001/v1"
$env:EDMG_AI_OPENAI_COMPAT_MODEL="your-nim-model"
$env:EDMG_NVIDIA_RIVA_URL="host.docker.internal:50051"
$env:EDMG_NVIDIA_RIVA_ASR="0"
$env:EDMG_TRITON_IMAGE="nvcr.io/nvidia/tritonserver:26.04-py3"
$env:EDMG_TRITON_MODEL_REPOSITORY="./deployment/nvidia/triton-model-repository"
$env:EDMG_NVIDIA_TRITON_MODEL="edmg_shot_features"
$env:EDMG_NVIDIA_TRITON_SHOT_MODEL="edmg_shot_features"
$env:EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH="/v1/a2f/jobs"
$env:EDMG_NVIDIA_ACE_GENERATE_PATH="/v1/avatar/animation"
$env:EDMG_NVIDIA_COSMOS_INFER_PATH="/v1/infer"
$env:EDMG_NVIDIA_MODE="1"
$env:EDMG_NVIDIA_PROFILE="omniverse"
```

For remote services, use the remote base URL instead of
`host.docker.internal`.
Optional bearer keys are read from service-specific environment variables such
as `EDMG_NVIDIA_AUDIO2FACE_API_KEY`, `EDMG_NVIDIA_ACE_API_KEY`,
`EDMG_NVIDIA_TRITON_API_KEY`, and `EDMG_NVIDIA_COSMOS_API_KEY`. The backend uses
them for outbound requests but does not return them from status or invoke
responses.

Credential split:

- `NGC_API_KEY` is for NVIDIA/NGC image and gated asset access.
- `EDMG_AI_OPENAI_COMPAT_API_KEY` is for the planner endpoint itself when that
  endpoint requires bearer authentication.
- Both values must stay in the shell, OS secret store, or an ignored
  `.env.local` file.

Functional service-call smoke examples:

```powershell
curl -X POST http://127.0.0.1:7863/v1/nvidia/services/audio2face/invoke `
  -H "Content-Type: application/json" `
  -d '{"method":"GET","path":"/v1/health"}'

curl -X POST http://127.0.0.1:7863/v1/nvidia/audio2face/generate `
  -H "Content-Type: application/json" `
  -d '{"path":"/v1/a2f/jobs","audio_path":"outputs/dialogue.wav","character":"metahuman","emotion":"focused"}'

curl -X POST http://127.0.0.1:7863/v1/nvidia/cosmos/infer `
  -H "Content-Type: application/json" `
  -d '{"prompt":"audio-reactive RTX stage world","negative_prompt":"blurry, low quality","seed":4,"guidance_scale":7.5,"steps":50,"video_params":{"height":704,"width":1280,"frames_count":121,"frames_per_sec":24}}'

curl -X POST http://127.0.0.1:7863/v1/nvidia/triton/infer `
  -H "Content-Type: application/json" `
  -d '{"model":"edmg_shot_features","inputs":[{"name":"scene_plan","shape":[1],"datatype":"BYTES","data":["{\"title\":\"RTX stage\",\"bpm\":128,\"scenes\":[{\"title\":\"Intro pulse\",\"duration_s\":8},{\"title\":\"Laser drop\",\"duration_s\":16}]}"]}],"outputs":[{"name":"features_json"},{"name":"energy_score"},{"name":"scene_count"}]}'

curl -X POST http://127.0.0.1:7863/v1/nvidia/triton/shot-features `
  -H "Content-Type: application/json" `
  -d '{"scene_plan":{"title":"RTX stage","bpm":128,"scenes":[{"title":"Intro pulse","duration_s":8},{"title":"Laser drop","duration_s":16}]}}'
```

For `edmg_shot_features`, the Triton response includes `features_json`,
`energy_score`, and `scene_count` outputs. The committed `edmg_echo` model is
kept only for low-level connectivity debugging. Use the full `26.04-py3` Triton
server image; the `py3-min` image is not a runnable server image for this
profile. Replace `EDMG_NVIDIA_TRITON_MODEL`, `EDMG_NVIDIA_TRITON_SHOT_MODEL`,
and `EDMG_TRITON_MODEL_REPOSITORY` when swapping in heavier production Triton
models.

The Omniverse Kit `edmg.ai_director` panel includes the same typed bridge for
`audio2face`, `ace`, `cosmos`, and `triton`; it sends the edited JSON payload to
the typed backend route instead of the generic invoke endpoint.

## Local helper scripts

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/login_ngc.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/test_docker_gpu.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_backend_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_runtime_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_optional_service.ps1 -Service triton -ConfigureEnv
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/prepare_nvidia_riva_quickstart.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/stage_nvidia_kit_app.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/run_live_gpu_smoke.ps1
```

- `login_ngc.ps1` logs Docker into `nvcr.io` with `$oauthtoken` and a masked
  `NGC_API_KEY`.
- `test_docker_gpu.ps1` runs a configurable CUDA container probe with
  `--gpus all`.
- `start_nvidia_backend_local.ps1` starts the local dev backend on port `8000`
  with the NVIDIA env file loaded, while leaving NIM free to use host port
  `8001`. It reuses an existing listener, otherwise it uses or bootstraps
  `studio/edmg-studio/python_backend/.venv` from Python 3.10-3.13 so the backend
  does not accidentally start with unsupported Python 3.14. If port `8000` is
  already occupied by a backend that is not running with NVIDIA mode enabled,
  the helper stops with a clear message unless you pass `-StopExisting` or
  explicitly allow reuse with `-ReuseExisting`.
- `start_nvidia_runtime_local.ps1` wraps the backend helper and Kit launcher
  into one local runtime command. It waits for `/health`, prints NVIDIA
  diagnostics readiness, detects FFmpeg, and launches Kit with
  `-AutoRunRenderWorker`.
- `start_nvidia_optional_service.ps1` starts one optional NVIDIA Compose
  service profile at a time, can write the local service URL/path into the
  ignored `.env.local`, and probes the exposed endpoint without printing
  secrets. Use `-ConfigureOnly` to update `.env.local` without pulling or
  starting the service. Use it service-by-service on this 6 GB RTX workstation.
- `stage_nvidia_kit_app.ps1` creates a local distributable source bundle for
  the EDMG NVIDIA Kit app under `dist/nvidia-kit-app/`, including app config,
  extensions, tools, deployment helpers, a manifest, and a generated launch
  script. It defaults to `-PackageMode thin` and does not copy the Kit SDK.
  `-PackageMode internal-fat` or legacy `-IncludeKitSdk` copies the SDK only for
  licensed/internal testing.
- `run_live_gpu_smoke.ps1` wraps preflight, runtime startup, and the Kit RTX
  render-job smoke. The matching GitHub Actions workflow is
  `.github/workflows/nvidia-live-smoke.yml` and requires a self-hosted Windows
  runner labeled `nvidia`. Pass `-SeedProjectIfMissing` for clean runner data
  directories.

Known-good staging command for this workstation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/stage_nvidia_kit_app.ps1 `
  -PackageMode thin `
  -BackendUrl "http://127.0.0.1:7863" `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "febd7dcbeb464b79bf4db78a7b6151c4" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -Clean
```

Known-good live smoke command for this workstation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/run_live_gpu_smoke.ps1 `
  -BackendUrl "http://127.0.0.1:7863" `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "febd7dcbeb464b79bf4db78a7b6151c4" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -FfprobePath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe" `
  -SeedProjectIfMissing
```

## First validation target

1. Start the existing backend with this override.
2. Confirm `/health` returns the configured OpenAI-compatible provider.
3. Send a small `/v1/plan` request through the current AI service contract.
4. Send `/v1/nvidia/scene-plan` with a real Studio `project_id` or save a
   deterministic scene plan through
   `/v1/projects/{project_id}/nvidia/scene-plan/save`, then confirm
   `last_nvidia_scene_plan` is saved in that project metadata.
5. Confirm `/v1/nvidia/riva/status` reports the expected Riva URI and enable
   `EDMG_NVIDIA_RIVA_ASR=1` only after the Riva Python client and service are
   available.
6. Launch the Kit app and use the AI Director panel to fetch, regenerate,
   export, and open the same project-scoped production USDA. Keep
   `-AutoRunRenderWorker` enabled for unattended RTX queue consumption.
7. Add service-specific Audio2Face/ACE panels and payload templates on top of
   the generic invoke bridge after the planner, USD, and speech paths are
   stable.

## Official image policy

Do not commit private registry credentials, pulled model weights, or accepted
license artifacts. Keep image names configurable because official NVIDIA service
images vary by product, release channel, access level, and model family.
