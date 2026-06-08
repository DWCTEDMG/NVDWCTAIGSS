# EDMG Studio on a GCP GPU VM

This path keeps EDMG Studio's existing architecture intact:

- existing FastAPI backend
- existing Studio Home storage split
- optional Ollama
- optional ComfyUI
- optional Vite browser UI
- no permanent hardcoded public IP in packaged defaults

Use this when you want the desktop app or a browser-served frontend to attach to
an Ubuntu GPU VM running the current `codex/Unified` branch.

## What this repo now provides

- Remote VM bootstrap script:
  `studio/edmg-studio/edmg_gcp_gpu_bootstrap.sh`
- Local Windows runner that uploads and executes the remote bootstrap through
  `gcloud compute ssh` and `gcloud compute scp`:
  `studio/edmg-studio/run_gcp_edmg_bootstrap.ps1`
- Local Studio connector script for pointing the desktop/dev frontend at the
  remote backend:
  `studio/edmg-studio/set_studio_gcp_backend.ps1`

The remote bootstrap covers the repo-specific work from the brief:

- install backend bundle on the VM
- install frontend dependencies
- validate `typecheck`, `test:ui`, and `build`
- create `~/bin/edmg-start-backend`, `~/bin/edmg-start-ui`, and `~/bin/edmg-check-cuda`
- create `/mnt/edmg-studio-home/{data,models,cache,logs,external}`
- validate CUDA visibility from PyTorch
- optionally install Ollama
- optionally queue curated model installs
- install a backend `systemd` service so the FastAPI backend survives reboot

The scripts intentionally do not auto-pick a GCP zone or bypass quota checks.
GPU stock and quota are live-account concerns and should stay operator-visible.

## Phase 0: local prerequisites

Run these on the local machine first:

```powershell
gcloud --version
git --version
ssh -V
```

Authenticate and choose the project:

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
```

Enable required APIs:

```powershell
gcloud services enable compute.googleapis.com
gcloud services enable storage.googleapis.com
```

Check quota before creating anything:

```powershell
gcloud compute regions describe us-central1
gcloud compute project-info describe
```

If GPU quota is zero, stop. Do not silently fall back to a CPU-only VM.

## Phase 1: choose a zone and machine

Check live accelerator availability:

```powershell
gcloud compute accelerator-types list --filter="name~'nvidia-l4|nvidia-tesla-a100|nvidia-h100|nvidia-rtx'"
```

Recommended starting zones from the brief:

- `us-central1-a`
- `us-central1-b`
- `us-east4-a`
- `us-east4-b`
- `us-west1-a`
- `us-west4-a`

Recommended starter shape:

- machine type: `g2-standard-24`
- GPU: `1 x NVIDIA L4`
- image: `ubuntu-2204-lts`
- disk: `500 GB pd-balanced`

## Phase 2: create and register the SSH key

```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\gcp_edmg_studio -C "gcp-edmg-studio"
gcloud compute os-login ssh-keys add --key-file $HOME\.ssh\gcp_edmg_studio.pub
```

## Phase 3: create the VM

Example L4 / G2 VM:

```powershell
gcloud compute instances create edmg-gpu-studio `
  --zone=us-central1-a `
  --machine-type=g2-standard-24 `
  --maintenance-policy=TERMINATE `
  --restart-on-failure `
  --boot-disk-size=500GB `
  --boot-disk-type=pd-balanced `
  --image-family=ubuntu-2204-lts `
  --image-project=ubuntu-os-cloud `
  --metadata=enable-oslogin=TRUE `
  --tags=edmg-studio
```

If the project only supports an N1 fallback:

```powershell
gcloud compute instances create edmg-gpu-studio `
  --zone=us-central1-a `
  --machine-type=n1-standard-16 `
  --accelerator=type=nvidia-tesla-t4,count=1 `
  --maintenance-policy=TERMINATE `
  --restart-on-failure `
  --boot-disk-size=500GB `
  --boot-disk-type=pd-balanced `
  --image-family=ubuntu-2204-lts `
  --image-project=ubuntu-os-cloud `
  --metadata=enable-oslogin=TRUE `
  --tags=edmg-studio
```

## Phase 4: open only the required ports

```powershell
gcloud compute firewall-rules create edmg-backend-7863 `
  --allow=tcp:7863 `
  --target-tags=edmg-studio `
  --source-ranges=0.0.0.0/0

gcloud compute firewall-rules create edmg-ui-5173 `
  --allow=tcp:5173 `
  --target-tags=edmg-studio `
  --source-ranges=0.0.0.0/0
```

Keep Ollama private unless you intentionally lock it to a narrow source range.

## Phase 5: install the VM runtime with the repo bootstrap

Before using the repo bootstrap, finish NVIDIA driver setup on the VM and make
sure `nvidia-smi` works. Driver install steps change over time, so keep that
step operator-driven against the current Google Compute Engine guidance.

Once the VM is reachable and `nvidia-smi` is healthy, run the repo helper from
the local machine:

```powershell
cd studio\edmg-studio
.\run_gcp_edmg_bootstrap.ps1 `
  -ProjectId YOUR_GCP_PROJECT_ID `
  -Zone us-central1-a `
  -InstanceName edmg-gpu-studio
```

Useful switches:

- `-InstallOllama`
  installs and starts Ollama on the VM, then pulls `qwen3:8b`
- `-QueueDefaultModels`
  asks the remote bootstrap to queue curated model installs after the backend is healthy
- `-SkipUi`
  skips the Vite dev server if you only want the backend

What the remote bootstrap writes on the VM:

- `~/.edmg-env`
- `~/bin/edmg-start-backend`
- `~/bin/edmg-start-ui`
- `~/bin/edmg-check-cuda`
- `~/bin/edmg-queue-default-models`

Runtime data stays under:

- `/mnt/edmg-studio-home/data`
- `/mnt/edmg-studio-home/models`
- `/mnt/edmg-studio-home/cache`
- `/mnt/edmg-studio-home/logs`
- `/mnt/edmg-studio-home/external`

## Phase 6: point the local Studio app at the GCP backend

After the VM bootstrap prints the backend URL, update the local desktop/dev
config:

```powershell
cd studio\edmg-studio
.\set_studio_gcp_backend.ps1 -BackendUrl http://YOUR_VM_IP:7863
```

That script updates:

- `.env`
- `.env.local`
- `launcher_env.json`
- `electron-resources/runtime-defaults.json`
- `%APPDATA%\EDMG Studio\bootstrap.json`

It stores the public GCP URL as the external connection target while keeping the
managed local bind host and port at `127.0.0.1:7863`.

For the browser frontend, use:

```text
http://YOUR_VM_IP:5173/?backendUrl=http://YOUR_VM_IP:7863
```

For the desktop app:

- Settings → Desktop Backend → Mode: `external`
- Desktop backend URL: `http://YOUR_VM_IP:7863`

## Optional Ollama

If you want Ollama on the VM:

```powershell
.\run_gcp_edmg_bootstrap.ps1 `
  -ProjectId YOUR_GCP_PROJECT_ID `
  -Zone us-central1-a `
  -InstanceName edmg-gpu-studio `
  -InstallOllama
```

If you skip Ollama, point the backend at a NVIDIA NIM/OpenAI-compatible endpoint.
Use the rule-based planner only as the no-endpoint fallback so the app still
boots cleanly.

## Optional model installs

The bootstrap creates `~/bin/edmg-queue-default-models`, but it does not queue
models unless you explicitly request it.

Manual model queue example on the VM:

```bash
source ~/.edmg-env
~/bin/edmg-queue-default-models
```

If you need gated Hugging Face repos, export a token first:

```bash
export HF_TOKEN=...
export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
```

## Optional Cloud Storage backup

Use GCS as backup or sync, not as the hot runtime model path:

```powershell
gcloud storage buckets create gs://YOUR_EDMG_BUCKET --location=us-central1
gcloud storage rsync -r /mnt/edmg-studio-home/models gs://YOUR_EDMG_BUCKET/models
gcloud storage rsync -r /mnt/edmg-studio-home/data gs://YOUR_EDMG_BUCKET/data
```

## Validation checklist

On the VM:

```bash
source ~/.edmg-env
~/bin/edmg-check-cuda
curl http://127.0.0.1:7863/health
curl http://127.0.0.1:5173
```

From the local machine:

```powershell
curl http://YOUR_VM_IP:7863/health
```

Open:

```text
http://YOUR_VM_IP:5173/?backendUrl=http://YOUR_VM_IP:7863
```

Definition of done:

- GPU VM exists and `nvidia-smi` works
- PyTorch sees CUDA
- `codex/Unified` is deployed on the VM
- backend runs on `0.0.0.0:7863`
- `/health` is green locally and externally
- frontend can attach through `?backendUrl=...`
- Studio Home uses `/mnt/edmg-studio-home`
- local packaging defaults were not hardcoded to a permanent public IP
