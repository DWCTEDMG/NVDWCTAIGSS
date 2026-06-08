# EDMG Studio (v1.1.0)

A desktop-style "studio" application:

- **Electron** shell + **React** UI
- Local **FastAPI** backend for projects, assets, planning, rendering, and outputs
- Bundled **EDMG Director** MCP sidecar for ChatGPT connector workflows and Studio-native directing handoff
- Integrates with:
  - **Studio internal renderer** as the default built-in render path
  - **ComfyUI** as an optional still/motion render sidecar (local or remote)
  - In-process **AI providers** for planning/transcription/features (Ollama by default)
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

### UI
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

## Setup Wizard (no command line)

When you install the packaged app, EDMG Studio includes an in-app **Setup Wizard** (Sidebar → **Setup**) that:

- Uses an assisted Windows installer, so you can choose the **app install directory** instead of being forced into the default `C:\` path
- Lets you choose a **Studio Home** folder before large downloads, so project data, Electron data, ComfyUI Portable, and caches can live on `D:\...`
- Checks **Ollama** availability (local AI)
- Supports local **OpenAI-compatible** servers such as LM Studio or `llama.cpp` server through Studio Settings
- Lets you **pull the default model** (`qwen3:8b`) via a button
- Installs the **backend runtime bundle** that powers the internal renderer
- Checks **ComfyUI** availability and can **download + extract ComfyUI Portable** on Windows when you want the optional ComfyUI path
- Verifies **FFmpeg** for MP4 assembly, preferring the Studio-bundled binary when present

This keeps the runtime UX like a DAW/game installer: click buttons, no terminal required.

On Linux, use the packaged `AppImage` or `pnpm run dist:linux` on a Linux host. The same Setup Wizard still manages Studio Home paths, but Ollama and ComfyUI are expected to be installed manually or pointed at existing services instead of using the Windows-only managed installers.

OpenClaw is not part of the required Studio install path. If you use it, treat it as an optional operator shell layered around Studio for automation or monitoring rather than as a dependency of the app itself.

Release/operator runbook:
- [Studio release runbook](../../docs/STUDIO_RELEASE_RUNBOOK.md)
- [Release checklist](../../RELEASE.md)
- [Linux packaging notes](./packaging/linux/README.md)
- [GCP GPU VM deploy](../../docs/GCP_GPU_VM_DEPLOY.md)

Install/storage split:
- **Install directory**: where the packaged app itself is installed
- **Studio Home**: where projects, caches, Electron session data, portable tools, and large runtime payloads live

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
- `EDMG_AI_PROVIDER` (default: `ollama`)
- `EDMG_AI_OLLAMA_URL` (default: `http://127.0.0.1:11434`)
- `EDMG_AI_OLLAMA_MODEL` (default: `qwen3:8b`)
- `EDMG_AI_OPENAI_COMPAT_BASE_URL` (default: `http://127.0.0.1:8000`)
- `EDMG_AI_OPENAI_COMPAT_MODEL` (default: `qwen3-8b`)
- `EDMG_AI_OPENAI_COMPAT_API_KEY` (optional)
- `EDMG_COMFYUI_URL` (default: `http://127.0.0.1:8188`)
- `EDMG_COMFYUI_CHECKPOINT` (default: `sd_xl_base_1.0.safetensors`)
- `EDMG_FFMPEG_PATH` (optional override; packaged Studio prefers its bundled FFmpeg, dev falls back to `ffmpeg` on PATH)
- `EDMG_AWS_MODEL_CACHE` (`1` enables S3-backed model cache/hosting)
- `EDMG_AWS_MODEL_CACHE_BUCKET` and `EDMG_AWS_MODEL_CACHE_PREFIX` (S3 bucket/prefix for model objects)
- `EDMG_MODEL_STORAGE_MODE` (`local_cache` keeps local files and mirrors them to S3; `cloud_only` stores supported single-file models in S3 and restores them on demand)
- `EDMG_S3_ENDPOINT_URL` (optional S3-compatible endpoint)

If you need a lighter local planner for weaker CPUs or low-memory systems, set `EDMG_AI_OLLAMA_MODEL=qwen3:4b`.

If you use an OpenAI-compatible gateway that exposes a different model alias than `qwen3-8b`,
override `EDMG_AI_OPENAI_COMPAT_MODEL` to match that server.

S3-hosted model entries can use `source: "s3"` with either `s3_uri: "s3://bucket/key"` or `s3_key: "prefix/model.safetensors"` plus the configured model-cache bucket. Runtime resolution materializes supported single-file ComfyUI assets into the Studio models directory before rendering.

## Recommended local model stack

- Planner default: `qwen3:8b`
- Low-resource planner: `qwen3:4b`
- Broad still-image default: SDXL Base 1.0
- Fast still-image option: SD3.5 Large Turbo
- Reference still guidance: SD3.5 ControlNet Blur, Canny, and Depth
- Primary HF video backend: Wan2.2 TI2V 5B
- Short image-to-video fallback: SVD XT Img2Vid

## Hardware tiers

- Low-spec: `qwen3:4b` + SDXL Base 1.0
- Mid-range: `qwen3:8b` + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny
- High-end: `qwen3:8b` + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny/Depth + Wan2.2 TI2V 5B

If `EDMG_STUDIO_HOME` is set, Studio uses it as the root for:
- backend project data (`<studio-home>/data`)
- models (`<studio-home>/models`)
- Electron user/session data (`<studio-home>/electron`)
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
3. Analyze + transcribe (in-process provider by default; optional external AI service)
4. Generate plan variants
5. Render with the internal renderer by default, or use ComfyUI optionally for supported still/motion workflows
6. Assemble MP4 (FFmpeg slideshow + audio)
7. Export Deforum settings (optional)


## Default rendering path

Studio's default render path is the **internal renderer** backed by the Studio backend runtime, local model installs, cache/history, and FFmpeg assembly.

Use ComfyUI only when you explicitly want one of the supported ComfyUI-backed still or motion workflows.

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
