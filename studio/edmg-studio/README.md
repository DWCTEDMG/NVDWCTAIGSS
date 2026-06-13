# EDMG NVIDIA Workshop Control Surface (v1.1.0)

React control surface and Python backend for EDMG NVIDIA Workshop. The primary
desktop shell lives in `../nvidia-kit/` and is launched through
`../../deployment/nvidia/start_nvidia_runtime_local.ps1`.

- **Omniverse Kit** shell + **OpenUSD/RTX** workflow in `studio/nvidia-kit`
- **React** UI retained as the Workshop control surface and dev client
- Local **FastAPI** backend for projects, assets, planning, rendering, and outputs
- Bundled **EDMG Director** MCP sidecar for ChatGPT connector workflows and Studio-native directing handoff
- Integrates with:
  - **NVIDIA NIM** for planning through the OpenAI-compatible `/v1` API
  - **NVIDIA Riva**, **Triton**, **Audio2Face/ACE**, **Cosmos**, and **Omniverse Kit** service profiles
  - **OpenUSD/RTX render jobs** as the primary production handoff
  - **Workshop internal renderer** and **ComfyUI** as secondary render lanes
  - Optional external **AI service** over HTTP when you want to separate that workload
  - **OpenClaw** only as an optional operator/automation shell around Studio, not as a required runtime dependency
  - **EDMG Core** (enhanced-deforum-music-generator) for Deforum template/export (optional but recommended)
  - **AWS** + **Lightning.ai** bundle scaffolding

## Quick start

### Prereqs
- Node.js LTS
- Python `>=3.10,<3.14`
- FFmpeg on PATH for dev checkouts, or the bundled Studio FFmpeg for packaged builds (used for MP4 assembly)
- ComfyUI only if you want ComfyUI-backed still or motion workflows (default `http://127.0.0.1:8188`)
- Planning/transcription run **in-process** by default through the selected provider; no separate AI server is required for the normal Studio path.
- OpenClaw is optional and external to the core Studio stack. Studio setup, packaging, planning, and rendering do not require it.
- EDMG Core is included by the default Studio backend bundle/install target

### Backend
```bash
cd python_backend
python -m venv venv
# Windows PowerShell
venv\Scripts\Activate.ps1
# macOS/Linux
source venv/bin/activate
pip install -U pip
pip install -e ".[studio_bundle]"
edmg-studio-backend serve --host 127.0.0.1 --port 7863
```

### Lightning backend helpers
From `studio/edmg-studio/`:

```bash
bash scripts/start_lightning_backend.sh
```

Detached variant with PID and log files under `EDMG_STUDIO_HOME/logs/lightning-backend`:

```bash
bash scripts/start_lightning_backend_nohup.sh
```

### Primary NVIDIA runtime
From the repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/start_nvidia_runtime_local.ps1
```

This starts or reuses the backend, checks NVIDIA diagnostics, and launches the
Kit app through `studio/nvidia-kit/tools/launch_edmg_kit.ps1`.

### React control surface
```bash
corepack enable
pnpm install
pnpm run check:tooling
pnpm run dev
```

`corepack enable` is only needed once per machine if `pnpm` is not already on `PATH`. The package
manager version is pinned via `packageManager` in `package.json`.

To repoint the desktop/dev frontend at a different backend target without hand-editing the bootstrap
files, run one of these from `studio/edmg-studio/`:

```bash
pnpm backend:use managed 7863
pnpm backend:use external http://HOST:PORT
```

## Versioning

- Canonical shipped desktop version: `studio/edmg-studio/package.json#version`
- Release staging copies that version into `studio/edmg-studio/release/staged-app/package.json`
- Windows installer names include `${version}` via `package.json#build.win.artifactName`
- Linux desktop artifacts are built as `AppImage` via `pnpm run dist:linux`
- Use `pnpm run check:release-metadata` after staging if you want a direct version-propagation check

## NVIDIA Runtime Setup

The React control surface includes **NVIDIA Runtime Setup** (Sidebar -> **Setup**). The primary install/run path is the NVIDIA Kit launcher and diagnostics flow:

- Uses an assisted Windows installer, so you can choose the **app install directory** instead of being forced into the default `C:\` path
- Lets you choose a **Workshop Home** folder before large downloads, so project data, NVIDIA session data, UI data, portable tools, and caches can live on `D:\...`
- Checks **NVIDIA NIM**, **Riva**, **Triton**, and **Omniverse/Audio2Face** service readiness
- Supports local or hosted **NVIDIA NIM/OpenAI-compatible** servers through Studio Settings
- Uses `nvidia/llama-3.3-nemotron-super-49b-v1.5` as the default NIM planning model
- Installs the **backend runtime bundle** that powers the internal renderer
- Keeps **ComfyUI** as a secondary NVIDIA-GPU workflow lane
- Verifies **FFmpeg** for MP4 assembly, preferring the Studio-bundled binary when present

This keeps the runtime UX like a DAW/game installer: click buttons, no terminal required.

On Linux, use the NVIDIA backend/service deployment paths first. The AppImage remains available only for React control-surface compatibility work.

OpenClaw is not part of the required Studio install path. If you use it, treat it as an optional operator shell layered around Studio for automation or monitoring rather than as a dependency of the app itself.

Release/operator runbook:
- [Studio release runbook](../../docs/STUDIO_RELEASE_RUNBOOK.md)
- [Release checklist](../../RELEASE.md)
- [Linux packaging notes](./packaging/linux/README.md)
- [GCP GPU VM deploy](../../docs/GCP_GPU_VM_DEPLOY.md)

Install/storage split:
- **Install directory**: where the packaged app itself is installed
- **Workshop Home**: where projects, caches, NVIDIA runtime data, UI session data, portable tools, and large runtime payloads live

## Ports
- Studio backend: **7863**
- Managed EDMG Director MCP app: **3001**
- External AI service (optional): **7862**
- ComfyUI: **8188**

## EDMG Director with Studio

Studio now has a native **Labs → EDMG Director** page that combines planner and reactive workbenches for the selected project. In addition, the desktop app can launch the ChatGPT-oriented `edmg-director` MCP server as a managed sidecar on `127.0.0.1:3001`.

Useful env vars:

- `EDMG_DIRECTOR_HOST` (default: `127.0.0.1`)
- `EDMG_DIRECTOR_PORT` (default: `3001`)
- `EDMG_DIRECTOR_BASE_URL` (optional public/tunneled URL advertised to ChatGPT for widget asset loading)
- `EDMG_DIRECTOR_SPAWN` (`0` disables the managed sidecar)

For local Studio use, the native Director page does not require ChatGPT. If you want the ChatGPT app flow too, expose the managed sidecar over HTTPS and point ChatGPT at `/mcp`.

## Environment variables (Backend)
- `EDMG_STUDIO_HOME` (optional; preferred root for Studio storage)
- `EDMG_STUDIO_DATA_DIR` (default: `./data`)
- `EDMG_AI_MODE` (default: `local`)
- `EDMG_AI_PROVIDER` (default: `openai_compat`)
- `EDMG_AI_OPENAI_COMPAT_BASE_URL` (default: `http://127.0.0.1:8001/v1`)
- `EDMG_AI_OPENAI_COMPAT_MODEL` (default: `nvidia/llama-3.3-nemotron-super-49b-v1.5`)
- `EDMG_AI_OPENAI_COMPAT_API_KEY` (optional)
- `EDMG_NVIDIA_MODE` (default: `1`)
- `EDMG_NVIDIA_PROFILE` (default: `omniverse`)
- `EDMG_NVIDIA_NIM_URL` (default: `http://127.0.0.1:8001`)
- `EDMG_NVIDIA_RIVA_URL` (default: `127.0.0.1:50051`)
- `EDMG_NVIDIA_TRITON_URL` (default: `http://127.0.0.1:8002`)
- `EDMG_NVIDIA_OMNIVERSE_URL` (default: `http://127.0.0.1:8011`)
- `EDMG_COMFYUI_URL` (default: `http://127.0.0.1:8188`)
- `EDMG_COMFYUI_CHECKPOINT` (default: `sd_xl_base_1.0.safetensors`)
- `EDMG_FFMPEG_PATH` (optional override; packaged Studio prefers its bundled FFmpeg, dev falls back to `ffmpeg` on PATH)
- `EDMG_AWS_MODEL_CACHE` (`1` enables S3-backed model cache/hosting)
- `EDMG_AWS_MODEL_CACHE_BUCKET` and `EDMG_AWS_MODEL_CACHE_PREFIX` (S3 bucket/prefix for model objects)
- `EDMG_MODEL_STORAGE_MODE` (`local_cache` keeps local files and mirrors them to S3; `cloud_only` stores supported model files and Diffusers snapshot directories in S3 and restores them on demand)
- `EDMG_S3_ENDPOINT_URL` (optional S3-compatible endpoint)

If your NIM/OpenAI-compatible gateway exposes a different model alias, override
`EDMG_AI_OPENAI_COMPAT_MODEL` to match that server.

S3-hosted model entries can use `source: "s3"` with either `s3_uri: "s3://bucket/key-or-prefix"` or `s3_key: "prefix/model-or-snapshot"` plus the configured model-cache bucket. Runtime resolution materializes supported ComfyUI files and internal Diffusers snapshots into the Studio models directory before rendering.

## Recommended local model stack

- Planner default: NVIDIA NIM `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- Broad still-image default: SDXL Base 1.0
- Fast still-image option: SD3.5 Large Turbo
- Reference still guidance: SD3.5 ControlNet Blur, Canny, and Depth
- Primary HF video backend: Wan2.2 TI2V 5B
- Short image-to-video option: SVD XT Img2Vid

## Hardware tiers

- Mid-range: NVIDIA NIM + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny
- High-end: NVIDIA NIM + RTX/OpenUSD Kit render jobs + SD3.5 Blur/Canny/Depth + Wan2.2 TI2V 5B

If `EDMG_STUDIO_HOME` is set, Studio uses it as the root for:
- backend project data (`<studio-home>/data`)
- models (`<studio-home>/models`)
- UI user/session data (`<studio-home>/electron`)
- caches and temporary files (`<studio-home>/cache`)
- logs (`<studio-home>/logs`)
- external tools (`<studio-home>/external`)

EDMG Core integration:
- If EDMG Core is installed in the same environment, Studio can:
  - Verify the core install
  - Export Deforum settings JSON per variant
  - Fetch the Deforum template

## Workflow
1. Create a project
2. Upload audio
3. Analyze + transcribe with NVIDIA services where configured
4. Generate NVIDIA/OpenUSD scene-plan variants with NIM
5. Use **Render -> AI Storyboard Movie** to apply the selected AI storyboard, write audio-reactive motion schedules, and queue a full MP4 job
6. Queue RTX render handoff jobs for Kit when you need OpenUSD/RTX staging
7. Export Deforum settings (optional)


## Default rendering path

The Workshop default movie path is **AI Storyboard Movie**: NIM creates the story pass, the backend writes prompt and audio-reactive motion tracks, the internal renderer produces a CUDA frame sequence, and FFmpeg assembles/muxes the final MP4. If no CUDA diffusion model is available, the command fails closed with a setup error instead of routing through proxy or CPU fallback.

The NVIDIA Kit/OpenUSD/RTX handoff remains the default path for inspectable 3D stage work and RTX render jobs. Use ComfyUI only when you explicitly want its optional still or motion workflows.

## Optional OpenClaw operator shell

If you want an external automation or operator surface, you can run OpenClaw alongside Studio.

Use it for things like queue triage, operator workflows, or sidecar automation against the Studio environment. Do not treat it as part of the required Studio runtime: Studio startup, setup, backend spawning, packaging, and rendering should all work without OpenClaw present.

## Optional ComfyUI motion rendering

Studio also supports **motion clips per scene** via two optional, local-friendly ComfyUI paths:

- **AnimateDiff (recommended for longer sequences)**  
  Requires `ComfyUI-AnimateDiff-Evolved` nodes. AnimateDiff supports *unlimited* animation length when you pass Context Options (sliding context windows). 

- **Stable Video Diffusion (SVD) img2vid (best for short clips / transitions)**  
  Requires `ComfyUI-Stable-Video-Diffusion` nodes (e.g. `SVDSimpleImg2Vid`). 

### Verify ComfyUI capabilities

From the Studio UI (Workspace), you’ll see availability checks (✓/×).  
Backend endpoint: `GET /v1/comfyui/capabilities` (uses ComfyUI’s `/object_info`). 

### Rendering motion

- Workspace → Render → Mode → **Motion (AnimateDiff)** or **Motion (SVD)**  
- Click **Enqueue motion scenes**, then use **Tick worker** repeatedly (or run a simple loop).

Outputs:
- Frames: `data/<project>/outputs/frames/...`
- Per-scene clips: `data/<project>/outputs/clips/...`
- Final concatenated video: `data/<project>/outputs/videos/variant_XX.mp4`
