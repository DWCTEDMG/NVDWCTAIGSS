# AI Providers (NVIDIA-first, fallback-capable)

EDMG Studio defaults to **NVIDIA NIM / OpenAI-compatible** planning. Provider
selection is available directly in Studio `Settings` instead of only through
environment variables.

## Default (recommended): NVIDIA NIM / Omniverse profile

```bash
EDMG_AI_MODE=local
EDMG_NVIDIA_MODE=1
EDMG_NVIDIA_PROFILE=omniverse
EDMG_AI_PROVIDER=openai_compat
EDMG_NVIDIA_NIM_URL=http://127.0.0.1:8001
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:8001/v1
EDMG_AI_OPENAI_COMPAT_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5
EDMG_NVIDIA_RIVA_URL=127.0.0.1:50051
EDMG_NVIDIA_RIVA_ASR=0
NGC_API_KEY=...  # load from shell or ignored local env file, never commit it
```

For hosted NVIDIA API planning, point both planner URLs at the hosted
OpenAI-compatible base:

```bash
EDMG_NVIDIA_NIM_URL=https://integrate.api.nvidia.com/v1
EDMG_AI_OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1
EDMG_AI_OPENAI_COMPAT_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5
EDMG_NVIDIA_API_KEY=...  # or NVIDIA_API_KEY / EDMG_AI_OPENAI_COMPAT_API_KEY
```

For the Compose-based starter, see `deployment/nvidia/`. The Windows desktop
package remains a client/workstation shell; NIM, Riva, NeMo, and related GPU
services should run as local or remote service endpoints. If the inference
endpoint itself requires bearer auth, set `EDMG_AI_OPENAI_COMPAT_API_KEY` too;
do not assume it is always the same value as `NGC_API_KEY`. Hosted NVIDIA
planner auth also accepts `NVIDIA_API_KEY`, `EDMG_NVIDIA_API_KEY`,
`EDMG_NVIDIA_NIM_API_KEY`, or an existing service-specific `nvapi` key alias.

Riva ASR stays opt-in. Set `EDMG_NVIDIA_RIVA_ASR=1` or
`EDMG_AI_ASR_PROVIDER=riva` only after the backend environment has the NVIDIA
Riva Python client and the Riva gRPC endpoint is reachable. With
`EDMG_NVIDIA_RIVA_ASR_FALLBACK=1`, failed Riva transcription falls back to the
existing local faster-whisper path.

## Local fallback: Ollama

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=ollama
EDMG_AI_OLLAMA_URL=http://127.0.0.1:11434
EDMG_AI_OLLAMA_MODEL=qwen3:8b
```

Use `qwen3:4b` on lower-spec CPU-only or low-memory systems.

## OpenAI-compatible (local or cloud)

Use this when pointing EDMG to an OpenAI-compatible server such as:

- LM Studio local server
- llama.cpp server
- LocalAI
- vLLM / TGI / OpenWebUI / etc.
- a cloud provider with OpenAI-compatible endpoints

Common base URLs:

- LM Studio: `http://127.0.0.1:1234/v1`
- llama.cpp server: `http://127.0.0.1:8080/v1`
- Groq: `https://api.groq.com/openai/v1`
- Together: `https://api.together.xyz/v1`

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=openai_compat
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:1234/v1
EDMG_AI_OPENAI_COMPAT_MODEL=your-model-id
EDMG_AI_OPENAI_COMPAT_API_KEY=...  # if required
```

Some gateways mirror Ollama tags instead of OpenAI-style aliases. If your server expects
`qwen3:8b` or another identifier, override `EDMG_AI_OPENAI_COMPAT_MODEL` accordingly.

Studio stores the OpenAI-compatible API key through its secret-storage path so
you do not have to keep it in plain-text environment variables.

## External AI service (advanced)

If you deploy the optional EDMG AI service as a separate FastAPI process, set:

```bash
EDMG_AI_MODE=http
EDMG_AI_BASE_URL=http://127.0.0.1:7862
```

## Rule-based fallback

If you want to avoid a model dependency for planning, Studio also supports:

```bash
EDMG_AI_MODE=local
EDMG_AI_PROVIDER=rule_based
```

## Recommended local stack

- Planner default: NVIDIA NIM / OpenAI-compatible `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- Local fallback planner: `qwen3:8b`
- Low-resource fallback planner: `qwen3:4b`
- Broad still-image default: SDXL Base 1.0
- Fast still-image option: SD3.5 Large Turbo
- Reference still guidance: SD3.5 ControlNet Blur, Canny, and Depth
- Primary HF video backend: Wan2.2 TI2V 5B
- Short image-to-video fallback: SVD XT Img2Vid

## Hardware tiers

- Low-spec: `qwen3:4b` + SDXL Base 1.0
- Mid-range: NVIDIA NIM + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny
- High-end: NVIDIA NIM + SDXL Base 1.0 + SD3.5 Large Turbo + SD3.5 Blur/Canny/Depth + Wan2.2 TI2V 5B
