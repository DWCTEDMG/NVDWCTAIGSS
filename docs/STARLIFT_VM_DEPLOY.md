# EDMG NVIDIA Workshop on a Spaceship / Starlift VM

This browser-served deployment is a remote control plane for the same NVIDIA
Workshop backend. It is not a separate product lane.

Use this path for:

- Ubuntu 24.04 GPU VMs
- Docker-based installs
- browser access over one HTTP port
- NVIDIA NIM/OpenAI-compatible planning
- optional NVIDIA-GPU ComfyUI sidecar workflows

CPU-only planning, Ollama fallback, and rule-based fallback are not part of the
NVIDIA-only profile.

## Files

- `studio/edmg-studio/Dockerfile.starlift`
- `studio/edmg-studio/deployment/starlift/nginx.conf`
- `docker-compose.starlift.yml`

The deployment runs:

- `web`: nginx serving the built React control surface on port `8080`
- `backend`: FastAPI backend on internal port `7863`

## Run

```bash
docker compose -f docker-compose.starlift.yml up -d --build
```

Then open:

```text
http://<vm-ip>:8080
```

## NVIDIA Service Endpoints

The backend container can reach host services through `host.docker.internal`.

```bash
EDMG_NVIDIA_MODE=1 \
EDMG_NVIDIA_PROFILE=omniverse \
EDMG_AI_PROVIDER=openai_compat \
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://host.docker.internal:8001/v1 \
EDMG_AI_OPENAI_COMPAT_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5 \
EDMG_NVIDIA_NIM_URL=http://host.docker.internal:8001 \
EDMG_COMFYUI_URL=http://host.docker.internal:8188 \
docker compose -f docker-compose.starlift.yml up -d --build
```

## Data Layout

Persistent runtime data is stored under:

- `./starlift-data/data`
- `./starlift-data/models`
- `./starlift-data/cache`
- `./starlift-data/logs`
- `./starlift-data/external`

## Health

```bash
docker compose -f docker-compose.starlift.yml ps
docker compose -f docker-compose.starlift.yml logs -f edmg-studio-web edmg-studio-backend
curl http://127.0.0.1:8080/health
```
