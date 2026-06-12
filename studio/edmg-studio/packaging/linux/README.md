# Linux Packaging

The Linux desktop target is the `EDMG NVIDIA Workshop` AppImage. Build from the
single NVIDIA profile:

```bash
pnpm run dist:linux
```

Use `deployment/nvidia/start_nvidia_runtime_local.ps1` for the local runtime
when developing on Windows, or configure the same environment variables on the
Linux host before launching the packaged app:

- `EDMG_AI_MODE=nvidia_nim`
- `EDMG_NVIDIA_NIM_URL`
- `EDMG_NVIDIA_NIM_API_KEY` or `NVIDIA_API_KEY`
- CUDA-capable model and render service paths

The Linux package does not depend on Ollama and does not enable CPU, MPS,
DirectML, or hosted Stability fallbacks.
