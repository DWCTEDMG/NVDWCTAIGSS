# AWS S3 Model Cache

EDMG NVIDIA Workshop keeps large model weights out of the repo and installer. This AWS path adds an optional private S3 cache for single-file model artifacts such as SD3.5 `.safetensors` checkpoints and ControlNets.

S3 is used as object storage for model files. Keep the bucket private and use IAM credentials only for machines that should install or restore Workshop models.

## What It Does

- Checks S3 before downloading a single-file Hugging Face or Civitai model.
- Restores the file into the normal Workshop-managed model folder when the object exists.
- Uploads newly downloaded/copied single-file model artifacts back to S3 when the cache is enabled.
- Supports `EDMG_MODEL_STORAGE_MODE=cloud_only` for single-file assets, which uploads to S3 and removes the temporary transfer file instead of keeping a local Workshop model copy.
- Leaves diffusers snapshot directories on the normal Hugging Face/on-demand path for now.

## Local Prerequisites

Install AWS CLI v2 and configure credentials:

```powershell
aws configure
# or use your organization's AWS SSO/login flow
aws sts get-caller-identity
```

Install the optional backend dependencies:

```powershell
cd studio/edmg-studio/python_backend
pip install -e ".[aws]"
```

## Create Storage

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\deployment\aws\setup_model_cache.ps1 `
  -Region us-east-1 `
  -Prefix models
```

The script creates or reuses:

- a private S3 bucket
- S3 public access block settings
- bucket-owner enforced object ownership
- AES256 default encryption
- versioning
- a lifecycle rule that removes old noncurrent object versions under the model prefix after 30 days

Pass `-BucketName your-globally-unique-bucket` when you want to choose the bucket name. Pass `-Profile your-aws-profile` when you use named AWS profiles.

## Enable The Cache

Set these for the backend process:

```powershell
$env:EDMG_AWS_MODEL_CACHE='1'
$env:EDMG_AWS_MODEL_CACHE_BUCKET='your-private-bucket'
$env:EDMG_AWS_MODEL_CACHE_PREFIX='models'
$env:EDMG_AWS_MODEL_CACHE_STORAGE_CLASS='STANDARD'
$env:AWS_REGION='us-east-1'
```

When both AWS S3 and Azure Blob model caches are enabled, the Workshop uses the AWS S3 cache first.

To default supported model installs to S3-only storage:

```powershell
$env:EDMG_MODEL_STORAGE_MODE='cloud_only'
```

Use `local_cache` to return to the normal behavior where the Workshop keeps a local copy and also uploads to S3.

For S3-compatible providers, also set:

```powershell
$env:EDMG_S3_ENDPOINT_URL='https://your-s3-compatible-endpoint'
```

## Use With The Workshop

1. Start EDMG NVIDIA Workshop with the environment variables above.
2. Open **Cloud**.
3. Enter the S3 bucket and model prefix.
4. Click **Test credentials**.
5. Open **Models** and install the model weights you want hosted in the cache.

On the first machine, the Workshop downloads from the model source and uploads the file to S3. On the next machine with the same env vars, the Workshop restores from S3 instead of downloading from the public source again.

In `cloud_only` mode, Studio uses a temporary transfer file during upload and deletes it after the S3 object is written. Local ComfyUI and internal Diffusers render paths still need a filesystem model path at runtime. Use **Restore local** in Model Manager when you want to run those local paths, or use a remote worker that mounts/downloads the S3 cache near the GPU.

## Mount S3 As The Workshop Model Root

On this Windows workstation, rclone can mount the S3 model prefix as `X:\`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\aws\mount_model_cache_rclone.ps1
```

Launch the Workshop with the S3 mount as the model root:

```powershell
$env:EDMG_STUDIO_MODELS_DIR='X:\'
$env:EDMG_MODEL_STORAGE_MODE='local_cache'
.\RUN_ME.bat
```

This sets `EDMG_STUDIO_MODELS_DIR=X:\` only for the Workshop session and uses `EDMG_MODEL_STORAGE_MODE=local_cache`. In this mode, `local_cache` means "write to the mounted model filesystem"; because the model filesystem is `X:\`, the files live under the S3-backed rclone mount instead of the normal Workshop model folder.

Do not combine `EDMG_STUDIO_MODELS_DIR=X:\` with `EDMG_MODEL_STORAGE_MODE=cloud_only`. `cloud_only` is upload-only storage and blocks directory-based Diffusers snapshots; the mounted-S3 runtime path needs normal filesystem writes to `X:\`.

Do not set `EDMG_STUDIO_MODELS_DIR=X:\` globally unless the rclone mount is always started before the backend, because the Workshop expects the configured model root to exist at startup.

## Verify

From CLI:

```powershell
aws s3 ls s3://your-private-bucket/models --recursive --human-readable --summarize
```
