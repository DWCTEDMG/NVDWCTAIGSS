# NVIDIA Official Options Map

This is the practical map for an NVIDIA-first EDMG Studio branch. It is not a
promise to use every NVIDIA product at once. It separates the official NVIDIA
pieces that fit this repo from the pieces that should remain optional service
profiles because they require specific GPUs, licenses, gated images, or cloud
infrastructure.

## Current branch stance

Use NVIDIA as the product runtime, with Omniverse Kit owning the desktop shell
and viewport:

- Windows desktop package: Omniverse Kit app, EDMG extensions, connection
  profiles, sample USD projects, and lightweight local validation tools.
- Local or remote GPU service stack: NIM, Riva, NeMo, Audio2Face/ACE, Cosmos,
  Triton, TensorRT, and render workers.
- Existing Electron app: compatibility client only while remaining panels move
  into Kit extensions or backend-served NVIDIA tools.

The package should not embed model weights, accepted license artifacts, or
private registry credentials.

## Product lanes

| Lane | NVIDIA option | Repo role | Packaging decision |
| --- | --- | --- | --- |
| Desktop shell and viewport | Omniverse Kit SDK | Build the NVIDIA Studio desktop app, USD stage editor, RTX preview, and extension UI. | Package in the Windows app if redistribution terms and selected Kit release allow it. |
| Durable scene format | OpenUSD and OpenUSD Exchange SDK | Store scene, camera, lighting, render variants, timeline markers, and generated plan state. | Package schemas, templates, validators, and sample stages. |
| Web/remote viewing | Omniverse Web SDK and Kit App Streaming | Stream a Kit app to browser clients or remote operators. | Keep as optional deployment profile, not required for desktop MVP. |
| LLM planning | NVIDIA NIM for LLMs | Serve the AI Director through OpenAI-compatible `/v1` endpoints where possible. | Configure as local WSL2/Linux container or remote endpoint. Do not package model weights. |
| Training/customization | NeMo Framework container and NeMo microservices | Fine-tune or adapt models outside the desktop app. | External GPU job profile only. |
| Speech | Riva or Speech NIM microservices | ASR/TTS for audio transcription, voice commands, and narration workflows. | External service profile; fallback to current local whisper path until stable. |
| Character animation | Audio2Face/ACE | Convert voice/emotion into facial animation for digital human or performer workflows. | Optional service profile with USD/animation import/export. |
| World/video generation | Cosmos and related NGC assets | Physics-aware/video/world-state generation experiments. | Research or lab profile until model access, costs, and UX are proven. |
| Serving optimization | Triton, TensorRT, TensorRT-LLM | Production inference serving and optimized model runtime. | Service infrastructure, not Windows app payload. |
| GPU base stack | CUDA, cuDNN, NCCL, NVIDIA Container Toolkit | Enables accelerated Docker/WSL2/remote Linux services. | Host prerequisite and preflight check. |
| Registry and gated assets | NGC Catalog | Official containers, models, Helm charts, and SDK assets. | User supplies credentials in ignored env/secret store. |

## Infrastructure lanes

These are official NVIDIA options that matter after the local workstation path
works:

| Lane | NVIDIA option | When to use it | Repo decision |
| --- | --- | --- | --- |
| Reproducible development | NVIDIA AI Workbench | Team development across local RTX, remote workstations, data center, or cloud GPU machines. | Optional dev environment, not required for the Windows app. |
| Kubernetes inference | NVIDIA NIM Operator | Cluster-managed NIM, NeMo microservices, model caching, GPU scheduling, and upgrades. | Later deployment profile after Compose is proven. |
| Kubernetes GPU enablement | NVIDIA GPU Operator | Cluster-level driver, runtime, device plugin, and monitoring management. | Later cluster prerequisite, not desktop installer content. |
| Profiling | Nsight Systems, Nsight Compute, Nsight Graphics | Performance tuning for Kit extensions, render workers, CUDA kernels, and inference paths. | Developer tooling only. |
| Registry automation | NGC CLI and NGC Catalog APIs | Scripted image/model access once licenses and entitlements are settled. | Use in setup docs or CI only with secrets injected at runtime. |

Non-goals for the first NVIDIA branch:

- robotics-specific Isaac workflows;
- video analytics-specific DeepStream workflows;
- healthcare-specific Clara/MONAI deployment surfaces;
- networking-specific DOCA workflows;
- cluster-only deployment before a single-workstation path works.

## What "official" should mean here

Official should mean:

- use NVIDIA-published SDKs, docs, containers, API contracts, and NGC artifacts;
- keep image names and versions explicit in deployment profiles;
- do not scrape keys or bake credentials into the repo;
- document product-specific license and access requirements before enabling a
  profile by default;
- keep fallbacks for users without the required NVIDIA hardware or entitlements.

Official should not mean bundling every NVIDIA service into one installer. That
would make the Windows app huge, fragile, license-sensitive, and hard to update.

## Windows package shape

The packaged Windows app can still exist. The right boundary is:

```text
EDMG NVIDIA Studio for Windows
|- Omniverse Kit runtime or launcher-managed Kit dependency
|- EDMG Kit app configuration
|- EDMG Kit extensions
|- OpenUSD schemas/templates/sample projects
|- service connection manager
|- local smoke tests and preflight UI
`- optional link-outs to WSL2/Docker or remote GPU service setup
```

The installer should connect to GPU services. It should not try to become the
GPU service fleet.

## Service profile order

Build profiles in this order:

1. `nvidia-nim-planner`: point the existing planner at an OpenAI-compatible NIM
   endpoint and expose masked status in the app.
2. `nvidia-usd-export`: normalize generated scene plans into USD metadata and
   validate sample stages.
3. `nvidia-riva-speech`: add ASR/TTS adapter behind the existing transcription
   contract.
4. `nvidia-kit-preview`: open the sample USD stage in a minimal Kit app.
5. `nvidia-audio2face`: generate character animation from voice/emotion assets.
6. `nvidia-render-worker`: submit RTX render jobs from the Kit app or API.
7. `nvidia-nemo-customization`: run training/customization jobs outside the
   desktop app.

## Links

- Omniverse docs: https://docs.nvidia.com/omniverse/index.html
- NIM docs: https://docs.nvidia.com/nim/
- NIM LLM API reference: https://docs.nvidia.com/nim/large-language-models/2.0.3/reference/api-reference.html
- Riva docs: https://docs.nvidia.com/deeplearning/riva/user-guide/docs/public/index.html
- Audio2Face/ACE microservice overview: https://docs.nvidia.com/ace/audio2face-3d-microservice/1.0/text/getting-started/overview.html
- NeMo Framework container tags: https://catalog.ngc.nvidia.com/orgs/nvidia/containers/nemo/tags
- NVIDIA AI Workbench docs: https://docs.nvidia.com/ai-workbench/index.html
- NVIDIA NIM Operator docs: https://docs.nvidia.com/nim-operator/latest/index.html
- NVIDIA Container Toolkit install guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- CUDA on WSL guide: https://docs.nvidia.com/cuda/pdf/CUDA_on_WSL_User_Guide.pdf
- NVIDIA Nsight Systems docs: https://docs.nvidia.com/cuda/nsight-systems/index.html
