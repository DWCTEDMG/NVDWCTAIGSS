# Azure Model Cache

EDMG Studio keeps large model weights out of the repo and installer. This Azure path adds an optional shared Blob Storage cache for single-file model artifacts such as SD3.5 `.safetensors` checkpoints and ControlNets.

Blob Storage is the right home for model weights. SQL databases, Cosmos DB, and similar stores should hold metadata, user state, audit records, or job history, not multi-GB model files.

## What It Does

- Checks Azure Blob Storage before downloading a single-file Hugging Face or Civitai model.
- Restores the file into the normal Studio-managed model folder when the blob exists.
- Uploads newly downloaded/copied single-file model artifacts back to Azure when the cache is enabled.
- Leaves diffusers snapshot directories on the normal Hugging Face/on-demand path for now.

## Local Prerequisites

Install Azure CLI and sign in:

```powershell
az login
az account set --subscription YOUR_SUBSCRIPTION_ID
```

Install the optional backend dependencies:

```powershell
cd studio/edmg-studio/python_backend
pip install -e ".[azure]"
```

## Create Storage

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\deployment\azure\setup_model_cache.ps1 `
  -SubscriptionId YOUR_SUBSCRIPTION_ID `
  -ResourceGroup rg-edmg-model-cache `
  -Location eastus `
  -Container edmg-model-cache
```

The script creates or reuses:

- an Azure resource group
- a private StorageV2 account
- a Blob container
- a Storage Blob Data Contributor role assignment for your signed-in user when permissions allow it

## Enable The Cache

Set these for the backend process:

```powershell
$env:EDMG_AZURE_MODEL_CACHE='1'
$env:EDMG_AZURE_STORAGE_ACCOUNT='yourstorageaccount'
$env:EDMG_AZURE_STORAGE_ACCOUNT_URL='https://yourstorageaccount.blob.core.windows.net'
$env:EDMG_AZURE_MODEL_CONTAINER='edmg-model-cache'
$env:EDMG_AZURE_MODEL_CACHE_PREFIX='models'
```

If the backend cannot use Azure CLI/AAD auth, use a storage connection string instead:

```powershell
$env:AZURE_STORAGE_CONNECTION_STRING='DefaultEndpointsProtocol=...'
```

When both AWS S3 and Azure Blob model caches are enabled, Studio uses the AWS S3 cache first.

## Use With Stability 3.5

In Studio:

1. Open **Models**.
2. Accept the license for the SD3.5 checkpoint you want.
3. Install **Stable Diffusion 3.5 Large Turbo**, **Large**, or the SD3.5 ControlNet files.

On the first machine, Studio downloads from the model source and uploads the file to Azure. On the next machine with the same env vars, Studio restores from Blob Storage instead of downloading from the public source again.

## Verify

Open **Cloud** in Studio and use the Azure panel to test the Blob container. From CLI, you can also check:

```powershell
az storage blob list `
  --account-name yourstorageaccount `
  --container-name edmg-model-cache `
  --auth-mode login `
  --prefix models `
  -o table
```
