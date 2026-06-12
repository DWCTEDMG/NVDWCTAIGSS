# Windows Packaging

The Windows desktop package is `EDMG NVIDIA Workshop`. Build it from the single
NVIDIA profile path:

```powershell
pnpm run dist:win
```

The optional Inno wrapper uses `build_inno_external.ps1` and expects the
Electron `win-unpacked` output. It creates an `EDMG NVIDIA Workshop` installer
and launches `EDMG NVIDIA Workshop.exe`.

Runtime assumptions:

- NVIDIA NIM or an NVIDIA-compatible `/v1` endpoint is configured.
- CUDA-capable internal diffusion, ComfyUI on NVIDIA GPUs, or NVIDIA RTX/OpenUSD
  render jobs are available for image/video work.
- Ollama, hosted Stability providers, DirectML, MPS, and CPU render fallbacks are
  not packaged or configured by this profile.
