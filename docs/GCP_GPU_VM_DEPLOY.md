# EDMG NVIDIA Workshop on a GCP GPU VM

This path runs the NVIDIA Workshop backend and optional browser control surface
on an Ubuntu GPU VM. It keeps the app on the NVIDIA-only runtime profile:
NIM/OpenAI-compatible planning, CUDA-capable rendering, and no Ollama or CPU
fallback.

## Local Prerequisites

```powershell
gcloud --version
git --version
ssh -V
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud services enable compute.googleapis.com
gcloud services enable storage.googleapis.com
```

Check quota before creating anything. If GPU quota is zero, stop; do not fall
back to a CPU-only VM.

## VM Shape

Recommended starter:

- machine type: `g2-standard-24`
- GPU: `1 x NVIDIA L4`
- image: `ubuntu-2204-lts`
- disk: `500 GB pd-balanced`

Example:

```powershell
gcloud compute instances create edmg-nvidia-workshop `
  --zone=us-central1-a `
  --machine-type=g2-standard-24 `
  --maintenance-policy=TERMINATE `
  --restart-on-failure `
  --boot-disk-size=500GB `
  --boot-disk-type=pd-balanced `
  --image-family=ubuntu-2204-lts `
  --image-project=ubuntu-os-cloud `
  --metadata=enable-oslogin=TRUE `
  --tags=edmg-nvidia-workshop
```

Open only the required ports:

```powershell
gcloud compute firewall-rules create edmg-workshop-backend-7863 `
  --allow=tcp:7863 `
  --target-tags=edmg-nvidia-workshop `
  --source-ranges=YOUR_IP_CIDR

gcloud compute firewall-rules create edmg-workshop-ui-5173 `
  --allow=tcp:5173 `
  --target-tags=edmg-nvidia-workshop `
  --source-ranges=YOUR_IP_CIDR
```

## Bootstrap

Finish NVIDIA driver setup on the VM and verify `nvidia-smi` first. Then run:

```powershell
cd studio\edmg-studio
.\run_gcp_edmg_bootstrap.ps1 `
  -ProjectId YOUR_GCP_PROJECT_ID `
  -Zone us-central1-a `
  -InstanceName edmg-nvidia-workshop
```

Do not use `-InstallOllama`; the NVIDIA Workshop profile does not route through
Ollama. Queue model installs only when the backend is healthy and the selected
models are NVIDIA/CUDA-compatible.

Runtime data lives under:

- `/mnt/edmg-nvidia-workshop/data`
- `/mnt/edmg-nvidia-workshop/models`
- `/mnt/edmg-nvidia-workshop/cache`
- `/mnt/edmg-nvidia-workshop/logs`
- `/mnt/edmg-nvidia-workshop/external`

## Connect

After the VM bootstrap prints the backend URL:

```powershell
cd studio\edmg-studio
.\set_studio_gcp_backend.ps1 -BackendUrl http://YOUR_VM_IP:7863
```

That connector writes the EDMG NVIDIA Workshop bootstrap config under the new
app name. For browser use:

```text
http://YOUR_VM_IP:5173/?backendUrl=http://YOUR_VM_IP:7863
```

## Validation

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

Definition of done:

- GPU VM exists and `nvidia-smi` works
- PyTorch sees CUDA
- backend runs on `0.0.0.0:7863`
- `/health` is green locally and externally
- frontend can attach through `?backendUrl=...`
- Workshop storage uses `/mnt/edmg-nvidia-workshop`
- local packaging defaults are not hardcoded to a permanent public IP
