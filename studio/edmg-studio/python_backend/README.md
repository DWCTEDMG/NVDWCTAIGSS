# EDMG Studio Backend (v1.1.0)

## Run
```bash
pip install -e ".[studio_bundle]"
edmg-studio-backend serve --host 127.0.0.1 --port 7863
```

## Docker (backend only)

This Docker path runs the FastAPI backend only. The Electron desktop shell still runs natively on the host.

Build from `studio/edmg-studio/python_backend`:

```bash
docker build -t edmg-studio-backend .
```

Run the backend container with persistent Studio storage:

```bash
docker run --rm -it \
  -p 7863:7863 \
  -v "$(pwd)/data:/studio/data" \
  -v "$(pwd)/models:/studio/models" \
  -v "$(pwd)/cache:/studio/cache" \
  -v "$(pwd)/logs:/studio/logs" \
  -v "$(pwd)/external:/studio/external" \
  -e EDMG_AI_OLLAMA_URL=http://host.docker.internal:11434 \
  -e EDMG_COMFYUI_URL=http://host.docker.internal:8188 \
  edmg-studio-backend
```

Notes:

- On Docker Desktop, `host.docker.internal` is usually the easiest way to reach Ollama and ComfyUI running on the host.
- On native Linux Docker installs, use the host IP or `--network=host` instead.
- The image installs the Studio backend bundle plus FFmpeg, `libsndfile`, and OpenMP runtime support for the current analysis/transcription stack.

## Tests
Install the same backend bundle Studio uses, plus the test extra:

```bash
pip install -e ".[studio_bundle,test]"
python -m pytest
```

Run that command from `studio/edmg-studio/python_backend/`. The backend-local
pytest scope covers both:

- `enhanced_deforum_music_generator/tests`
- `edmg_studio_backend/tests`

From the repo root:

- `python -m pytest` runs repo-level tests only
- `python scripts/run_pytest_scopes.py` runs repo-level tests, then backend-local tests

## Compatibility shims

The repo-root `sitecustomize.py` and repo-root `librosa/` package are
source-tree compatibility shims for development and tests. The packaged Studio
backend relies on the declared dependencies in this `pyproject.toml` and does
not package those repo-root shims.

## AI (Ollama by default)

The backend defaults to **EDMG_AI_MODE=local** and will call **Ollama** directly (no separate AI server to run).

Recommended env vars:

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=ollama
EDMG_AI_OLLAMA_URL=http://127.0.0.1:11434
EDMG_AI_OLLAMA_MODEL=qwen3:8b
```

Use `qwen3:4b` instead on lighter CPU-only or low-memory systems.

If you want an external AI service instead:

```bash
EDMG_AI_MODE=http
EDMG_AI_BASE_URL=http://127.0.0.1:7862
```

OpenAI-compatible option (LM Studio / llama.cpp server / vLLM / Groq / Together, etc.):

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=openai_compat
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:1234/v1
EDMG_AI_OPENAI_COMPAT_MODEL=qwen3-8b
EDMG_AI_OPENAI_COMPAT_API_KEY=...  # if required
```

If your OpenAI-compatible gateway exposes a different model alias, override
`EDMG_AI_OPENAI_COMPAT_MODEL` to match that server.

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

## Integrations
- ComfyUI renders are queued locally.
- Planning/transcription run in-process by default through the selected provider; an external AI service on `7862` is optional.
- EDMG Core is bundled into the Studio backend install/build target; Studio Setup can repair or reinstall it if needed.

## S3-backed model hosting

Set these variables before starting the backend to enable S3-backed model storage:

```bash
EDMG_AWS_MODEL_CACHE=1
EDMG_AWS_MODEL_CACHE_BUCKET=your-model-bucket
EDMG_AWS_MODEL_CACHE_PREFIX=models
EDMG_MODEL_STORAGE_MODE=local_cache  # or cloud_only for supported single-file assets
```

AWS credentials are resolved through the standard AWS SDK provider chain. S3-compatible storage can set `EDMG_S3_ENDPOINT_URL`.

Catalog or user-model entries may use `source: "s3"` with `s3_uri: "s3://bucket/key"` or `s3_key: "prefix/model.safetensors"`. When a ComfyUI checkpoint, LoRA, ControlNet, VAE, or similar single-file asset is selected for rendering, Studio restores it from S3 into the configured models directory and returns a local runtime path.
- FFmpeg defaults to the Studio-bundled binary when available; `EDMG_FFMPEG_PATH` remains an override.
