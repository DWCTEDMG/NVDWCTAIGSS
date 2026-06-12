# AI Providers

EDMG NVIDIA Workshop is NVIDIA-only by default. Planning routes through NVIDIA
NIM or a NVIDIA-compatible OpenAI `/v1` endpoint. Ollama, rule-based planning,
and local CPU fallback providers are disabled in runtime wiring.

## NVIDIA NIM / OpenAI-Compatible

```bash
EDMG_AI_MODE=local
EDMG_NVIDIA_MODE=1
EDMG_NVIDIA_PROFILE=omniverse
EDMG_AI_PROVIDER=openai_compat
EDMG_NVIDIA_NIM_URL=https://integrate.api.nvidia.com/v1
EDMG_AI_OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1
EDMG_AI_OPENAI_COMPAT_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5
EDMG_NVIDIA_RIVA_URL=127.0.0.1:50051
EDMG_NVIDIA_RIVA_ASR=0
EDMG_NVIDIA_API_KEY=...
```

For a local NIM gateway, point both base URLs at the local OpenAI-compatible
endpoint:

```bash
EDMG_NVIDIA_NIM_URL=http://127.0.0.1:8001
EDMG_AI_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:8001/v1
```

Planner authentication may use `EDMG_NVIDIA_API_KEY`, `NVIDIA_API_KEY`,
`EDMG_NVIDIA_NIM_API_KEY`, or `EDMG_AI_OPENAI_COMPAT_API_KEY`, depending on the
target gateway. Keep keys in the shell, OS secret store, or ignored
`deployment/nvidia/.env.local`.

## Speech

Riva ASR is the NVIDIA speech path. Set `EDMG_NVIDIA_RIVA_ASR=1` only after the
Riva Python client is installed and the configured gRPC endpoint is reachable.
If Riva is enabled but unavailable, transcription fails closed instead of
routing to a local ASR engine.

## Unsupported Providers

The following names are treated as retired compatibility inputs and should not
appear in launch configs:

- `ollama`
- `rule_based`
- CPU-only local planners
- hosted Stability image/video providers
- DirectML render devices
