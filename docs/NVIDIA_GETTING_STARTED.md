# NVIDIA Getting Started

This is the local setup path for the NVIDIA-first branch.

The Kit runtime packaging decision is documented in
[`NVIDIA_KIT_RUNTIME_DECISION.md`](NVIDIA_KIT_RUNTIME_DECISION.md): default
distribution is a thin EDMG Kit app bundle that resolves an installed Kit SDK,
while SDK-included bundles are internal/licensed only.

## 1. Stay on the NVIDIA branch

```powershell
git switch codex/nvidia-omniverse-studio
```

## 2. Verify host prerequisites

Run the preflight from the repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/check_nvidia_profile.ps1 -EnvFile deployment/nvidia/.env.example
```

Expected baseline on a ready Windows workstation:

- Docker is found.
- `nvidia-smi` reports the local GPU.
- Docker reports an `nvidia` runtime.
- `ffmpeg` is on `PATH` for MP4 assembly from Kit RTX captures.
- Compose config renders for the base and `nvidia-local` profiles.

This does not pull official NVIDIA images and does not print secrets.

## 3. Create the ignored local env file

```powershell
Copy-Item deployment/nvidia/.env.example deployment/nvidia/.env.local
notepad deployment/nvidia/.env.local
```

Put secrets only in `deployment/nvidia/.env.local`, the shell environment, or an
OS secret store. Do not paste keys into tracked docs, tests, or Compose files.

Important variables:

```dotenv
EDMG_NVIDIA_MODE=1
EDMG_NVIDIA_PROFILE=omniverse
EDMG_AI_PROVIDER=openai_compat
EDMG_NVIDIA_NIM_URL=http://host.docker.internal:8001
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://host.docker.internal:8001/v1
EDMG_AI_OPENAI_COMPAT_MODEL=meta/llama-3.2-1b-instruct
EDMG_NVIDIA_RIVA_URL=host.docker.internal:50051
EDMG_NVIDIA_RIVA_ASR=0
EDMG_NVIDIA_RIVA_LANGUAGE_CODE=en-US
EDMG_NVIDIA_RIVA_ASR_FALLBACK=1
EDMG_NVIDIA_OMNIVERSE_URL=
NGC_API_KEY=
EDMG_AI_OPENAI_COMPAT_API_KEY=
```

`NGC_API_KEY` is for NVIDIA/NGC image and gated asset access. The planner API key
is separate and belongs in `EDMG_AI_OPENAI_COMPAT_API_KEY` only when the target
endpoint requires bearer authentication.

For local development, keep the EDMG backend on `127.0.0.1:8000` and map a local
NIM container to host port `8001`. That keeps the app backend and the NIM
OpenAI-compatible endpoint from fighting over the same port.

Optional helpers:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/login_ngc.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/test_docker_gpu.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_backend_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_runtime_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/stage_nvidia_kit_app.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/run_live_gpu_smoke.ps1
```

The NGC login helper reads `NGC_API_KEY` from the shell or ignored `.env.local`
file and never prints the key value.

The local backend helper uses `studio/edmg-studio/python_backend/.venv` when it
is ready, or bootstraps that virtual environment from an installed Python
3.10-3.13 interpreter. The backend package declares `<3.14`, so do not launch
the backend directly with Python 3.14. If port `8000` is already held by a
backend that was started without NVIDIA mode, the helper refuses to reuse it;
rerun with `-StopExisting` to replace that process or `-ReuseExisting` only when
you intentionally want to keep it.

The local runtime helper wraps the backend helper and the Kit launcher: it waits
for backend health, prints NVIDIA diagnostics readiness, detects FFmpeg, then
launches Kit with the RTX render queue autorun enabled. On this workstation the
known-good command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/start_nvidia_runtime_local.ps1 `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "4af898d1ebb24a0d8ed2d59a337dd965" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -StopExistingKit
```

The Kit app staging helper creates a repeatable local bundle at
`dist/nvidia-kit-app/edmg-nvidia-kit` and a zip beside it. It includes the Kit
app config, EDMG extensions, tools, deployment helpers, `bundle-manifest.json`,
and `Start-EDMG-NVIDIA-Kit.ps1`. It intentionally does not copy the Kit SDK
unless `-IncludeKitSdk` is supplied after confirming redistribution terms for
the selected SDK. Known-good staging command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/stage_nvidia_kit_app.ps1 `
  -PackageMode thin `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "4af898d1ebb24a0d8ed2d59a337dd965" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -Clean
```

## 4. Start the NVIDIA-aware stack

Base backend override:

```powershell
docker compose `
  --env-file deployment/nvidia/.env.local `
  -f docker-compose.starlift.yml `
  -f deployment/nvidia/docker-compose.nvidia.yml `
  up --build backend
```

Optional local NVIDIA services:

```powershell
docker compose `
  --profile nvidia-local `
  --env-file deployment/nvidia/.env.local `
  -f docker-compose.starlift.yml `
  -f deployment/nvidia/docker-compose.nvidia.yml `
  up
```

The example profile uses explicit NGC image tags for a first local workstation
pass:

```dotenv
EDMG_NIM_LLM_IMAGE=nvcr.io/nim/meta/llama-3.2-1b-instruct:latest
EDMG_NIM_MAX_MODEL_LEN=8192
EDMG_RIVA_IMAGE=nvcr.io/nvidia/riva/riva-speech:2.19.0
EDMG_NEMO_IMAGE=nvcr.io/nvidia/nemo:26.04.01
EDMG_TRITON_IMAGE=nvcr.io/nvidia/tritonserver:26.04-py3-min
EDMG_AUDIO2FACE_IMAGE=nvcr.io/nim/nvidia/audio2face-3d:2.0
EDMG_COSMOS_IMAGE=nvcr.io/nim/nvidia/cosmos-reason2-2b:latest
```

You can narrow the stack with specific profiles such as `nim`, `riva`, `nemo`,
`triton`, `audio2face`, `ace`, or `cosmos` once the image and model choices are
known for your NGC account.

The NeMo profile is different from NIM: it is a framework/workspace container
for training, customization, and batch jobs. It does not replace the
OpenAI-compatible NIM endpoint used by the Studio planner.

## 5. Confirm the app sees the profile

From the backend:

```powershell
curl http://127.0.0.1:8000/v1/nvidia/status
curl http://127.0.0.1:8000/v1/nvidia/diagnostics
curl http://127.0.0.1:8000/v1/nvidia/services
curl http://127.0.0.1:8000/v1/nvidia/services/audio2face/status
curl http://127.0.0.1:8000/v1/config
```

From the desktop app:

- Setup page: active AI path includes NVIDIA enabled/disabled when profile data
  is available.
- Settings page: Live AI / NVIDIA Status shows NGC, NIM, Riva, and Omniverse
  configuration state plus host GPU, Docker NVIDIA runtime, and NIM endpoint
  reachability without printing key values.
- `/v1/nvidia/riva/status` reports the configured Riva ASR URI, language,
  model, SSL flag, and fallback behavior without secrets.
- `/v1/nvidia/services` and `/v1/nvidia/services/{service_id}/status` expose
  direct no-secret status checks for NIM, Riva, Omniverse, NeMo, Triton,
  Audio2Face, ACE, and Cosmos.
- `/v1/nvidia/services/{service_id}/invoke` sends controlled HTTP requests to
  configured Audio2Face, ACE, Cosmos, Omniverse, NeMo, or NIM-style services
  through relative paths only.
- `/v1/nvidia/triton/infer` posts a Triton v2 inference request to the
  configured `EDMG_NVIDIA_TRITON_URL` and model.
- `/v1/projects/{project_id}/nvidia/scene-plan/save` persists a supplied
  validated NVIDIA scene plan without calling the planner. The live GPU smoke
  uses this to seed clean runners when no project exists.
- Dashboard and Setup page: NVIDIA Runtime Readiness shows a single ready,
  partial, blocked, or disabled state plus the next actions needed for the
  official local stack.

## 6. Validate the starter USD project

```powershell
python studio/nvidia-kit/tools/validate_scene_plan.py studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json
python studio/nvidia-kit/tools/validate_kit_layout.py
python studio/nvidia-kit/tools/export_scene_plan_usda.py `
  studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json `
  studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_scene_plan.usda
```

The sample stage lives at:

```text
studio/nvidia-kit/sample_projects/audio_reactive_stage/stage.usda
```

The backend also exposes the same first contract as an API:

```powershell
curl -X POST http://127.0.0.1:8000/v1/usd/scene-plan `
  -H "Content-Type: application/json" `
  --data-binary "@studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json"
```

The API response includes `usd_stage.text`, a generated USDA text preview that
matches the CLI exporter.

Or run the PowerShell smoke script:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/smoke_nvidia_backend.ps1
```

With the Kit app running in `-AutoRunRenderWorker` mode, run the full RTX queue
handoff smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/smoke_nvidia_kit_render_job.ps1 `
  -ProjectId "4af898d1ebb24a0d8ed2d59a337dd965" `
  -FfprobePath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe" `
  -SeedProjectIfMissing
```

That command exports the saved NVIDIA scene plan to USDA, writes a render-job
JSON file under the project `outputs/nvidia/render_jobs/` folder, waits for Kit
to report completion, and validates the returned MP4 metadata.

The CI/live GPU wrapper runs preflight, starts/restarts the backend and Kit, and
then runs the same RTX handoff smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/run_live_gpu_smoke.ps1 `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "4af898d1ebb24a0d8ed2d59a337dd965" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -FfprobePath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe" `
  -SeedProjectIfMissing
```

The same command is available as the manual
`.github/workflows/nvidia-live-smoke.yml` workflow on a self-hosted Windows GPU
runner labeled `nvidia`. If no project id is supplied, the smoke creates a
temporary Studio project and saves a deterministic production NVIDIA scene plan
through `/v1/projects/{project_id}/nvidia/scene-plan/save`.

The Kit workspace has an equivalent AI Director smoke:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py --backend-url http://127.0.0.1:8000
```

With a real Studio project id, the same smoke can verify the project-scoped
generate-and-persist path:

```powershell
python studio/nvidia-kit/tools/smoke_ai_director_backend.py `
  --backend-url http://127.0.0.1:8000 `
  --generate `
  --project-id 4af898d1ebb24a0d8ed2d59a337dd965 `
  --output-usda studio/nvidia-kit/sample_projects/audio_reactive_stage/generated_from_project.usda
```

The Studio Workspace Handoff section also shows the saved NVIDIA/OpenUSD plan
and can export the persisted production USDA artifact into the project's
`outputs/nvidia/` folder. The generated USDA includes a `Production` scope with
render settings, per-shot cameras/lights, asset slots, render products, and
scene-level render output metadata. It can also build an RTX render-job JSON handoff in
`outputs/nvidia/render_jobs/` for the Omniverse Kit RTX worker.

If Riva is running and you want it to handle project transcription, set
`EDMG_NVIDIA_RIVA_ASR=1` or `EDMG_AI_ASR_PROVIDER=riva`. The backend will try
Riva first through the NVIDIA Riva Python client, then fall back to the existing
local faster-whisper path when `EDMG_NVIDIA_RIVA_ASR_FALLBACK=1`. You can also
call the direct project route without changing the default ASR provider:

```powershell
curl -X POST http://127.0.0.1:8000/v1/projects/4af898d1ebb24a0d8ed2d59a337dd965/nvidia/riva/transcribe `
  -H "Content-Type: application/json" `
  -d '{"language_code":"en-US"}'
```

For HTTP optional services, configure the service URL first. For example:

```powershell
$env:EDMG_NVIDIA_AUDIO2FACE_URL="http://127.0.0.1:8011"
$env:EDMG_NVIDIA_TRITON_URL="http://127.0.0.1:8002"
$env:EDMG_NVIDIA_TRITON_MODEL="edmg_ensemble"
```

Then call the functional bridges:

```powershell
curl -X POST http://127.0.0.1:8000/v1/nvidia/services/audio2face/invoke `
  -H "Content-Type: application/json" `
  -d '{"method":"GET","path":"/v1/health"}'

curl -X POST http://127.0.0.1:8000/v1/nvidia/audio2face/generate `
  -H "Content-Type: application/json" `
  -d '{"path":"/v1/a2f/jobs","audio_path":"outputs/dialogue.wav","character":"metahuman","emotion":"focused"}'

curl -X POST http://127.0.0.1:8000/v1/nvidia/ace/generate `
  -H "Content-Type: application/json" `
  -d '{"path":"/v1/avatar/animation","avatar_id":"performer","text":"Welcome to the RTX stage."}'

curl -X POST http://127.0.0.1:8000/v1/nvidia/cosmos/infer `
  -H "Content-Type: application/json" `
  -d '{"prompt":"audio-reactive RTX stage world","negative_prompt":"blurry, low quality","seed":4,"guidance_scale":7.5,"steps":50,"video_params":{"height":704,"width":1280,"frames_count":121,"frames_per_sec":24}}'

curl -X POST http://127.0.0.1:8000/v1/nvidia/triton/infer `
  -H "Content-Type: application/json" `
  -d '{"inputs":[{"name":"prompt","shape":[1],"datatype":"BYTES","data":["neon intro"]}]}'
```

To generate the render-worker handoff from the saved NVIDIA scene plan:

```powershell
curl -X POST http://127.0.0.1:8000/v1/projects/4af898d1ebb24a0d8ed2d59a337dd965/nvidia/render-job `
  -H "Content-Type: application/json" `
  -d '{"fps":24,"width":1920,"height":1080}'
```

The Kit `edmg.render_queue` panel consumes that job from the local projects
folder, opens the referenced USDA stage, captures RTX viewport frames into
`outputs/nvidia/renders/`, assembles the requested MP4 with FFmpeg, and reports
completion or failure to:

```text
POST /v1/projects/{project_id}/nvidia/render-job/result
```

## 7. Launch the Kit app against a local Kit SDK

Download the Windows Kit SDK resource from NGC, extract the zip, and point the
launcher at the extracted folder containing `kit.exe`:

```powershell
$env:OMNI_KIT_SDK_ROOT="D:\NVIDIA\Omniverse\kit-sdk-110.1.1"
powershell -NoProfile -ExecutionPolicy Bypass `
  -File studio/nvidia-kit/tools/launch_edmg_kit.ps1 `
  -KitSdkRoot $env:OMNI_KIT_SDK_ROOT `
  -ListExtensions
```

`-ListExtensions` is the fast non-UI smoke test. Remove it to open the Kit UI
and the starter stage. The first panels are loaded by the EDMG extensions:

- `edmg.ai_director` uses the backend URL from `/edmg/nvidia/backend_url`.
- `edmg.timeline` reads `/edmg/nvidia/sample_scene_plan` and summarizes scene
  cues.
- `edmg.render_queue` checks `/v1/worker/status` and
  `/v1/nvidia/diagnostics`, scans pending RTX render jobs, and can either run
  the next job from the Kit UI or continuously consume jobs when launched with
  `-AutoRunRenderWorker`.

The launcher auto-detects `ffmpeg` from `PATH` and passes it to Kit as
`/edmg/nvidia/ffmpeg_path`. You can also set `EDMG_FFMPEG_PATH` before launching
Kit when you need to pin a specific FFmpeg binary. The autorun queue consumer
polls every five seconds by default; pass `-RenderWorkerPollSeconds 1` when you
want a tighter local smoke-test loop.

In the AI Director panel:

1. Enter a real Studio `project_id` or the visible project name from the Studio
   project list. The Kit panel resolves project names to ids before calling the
   project-scoped backend routes.
2. Use `Fetch Project Plan` to load the saved NVIDIA/OpenUSD plan, or
   `Generate Plan` to call the configured NVIDIA/NIM-compatible planner through
   the project-scoped backend route.
3. Use `Export USDA` to persist the saved production USDA into the project
   `outputs/nvidia/` folder.
4. Use `Open Preview` to write the USDA text to a temporary local file and open
   it as the active Kit stage.

In the Render Queue panel:

1. Use `Scan RTX Jobs` to find pending `outputs/nvidia/render_jobs/*.json`
   contracts.
2. Use `Run Next RTX Job` to open the USDA stage and capture one RTX viewport
   frame per timeline scene, then assemble the MP4 requested by the render-job
   contract.
3. Check the Studio Outputs page for the job status, result sidecar, and
   rendered frame and movie paths.

For unattended local handoff testing, launch Kit with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File studio/nvidia-kit/tools/launch_edmg_kit.ps1 `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "4af898d1ebb24a0d8ed2d59a337dd965" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -AutoRunRenderWorker `
  -RenderWorkerPollSeconds 5
```

## Current local status

The `nvidia-nim-planner` backend path is wired: `/v1/nvidia/scene-plan` calls the
configured OpenAI-compatible planner, converts the selected variant into the
normalized NVIDIA scene-plan contract, returns a production USDA shot graph, and
saves the normalized scene plan into existing Studio project metadata when
`project_id` matches a project.

On this Windows workstation, the local NVIDIA lane has been validated end to
end: backend health, NVIDIA diagnostics readiness, NIM/OpenAI-compatible
planning, project-scoped USDA export, Kit queue consumption, RTX viewport frame
capture, FFmpeg MP4 assembly, and backend result reporting. The repeatable smoke
command is `deployment/nvidia/smoke_nvidia_kit_render_job.ps1`. A repeatable
Kit source bundle can now be staged with
`deployment/nvidia/stage_nvidia_kit_app.ps1`.

The remaining build work is service-specific UI/payload templates for
Audio2Face/ACE/Cosmos, richer asset generation from the production USD shot
graph, and live GPU runner hardening for release promotion.
Audio2Face/ACE, Triton, Cosmos, and remote GPU service profiles.
