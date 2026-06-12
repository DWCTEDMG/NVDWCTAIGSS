# EDMG NVIDIA Workshop Backend

This backend runs the Workshop API behind the NVIDIA profile. Planning is routed
to NVIDIA NIM or an OpenAI-compatible NVIDIA endpoint, image/video generation
requires CUDA-capable render services, and non-NVIDIA fallback providers are
disabled.

## Runtime

- Launch through `deployment/nvidia/start_nvidia_runtime_local.ps1`.
- Set `EDMG_AI_MODE=nvidia_nim`.
- Set `EDMG_NVIDIA_NIM_URL` to your NVIDIA-compatible `/v1` endpoint.
- Set `EDMG_NVIDIA_NIM_API_KEY` or `NVIDIA_API_KEY`.
- Use CUDA-capable internal diffusion, ComfyUI on NVIDIA GPUs, or NVIDIA
  RTX/OpenUSD render jobs for visual output.

The backend intentionally does not start or call Ollama, hosted Stability
providers, DirectML, MPS, or CPU render fallbacks. If the selected NVIDIA
service or CUDA model is unavailable, requests fail with setup guidance.

## Development

Run focused backend checks from the repository root:

```powershell
.\studio\edmg-studio\python_backend\.venv\Scripts\python.exe -m pytest -q
```

For desktop validation, run the Electron scripts from `studio/edmg-studio` after
the NVIDIA runtime is reachable.
