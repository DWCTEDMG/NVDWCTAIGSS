# EDMG Studio Model Manager (GUI)

EDMG Studio intentionally **does not bundle large model weights** in the installer.
Instead, it ships a **Model Manager** UI that:

- offers a curated **Recommended defaults** list
- offers an **Advanced / community** list
- supports adding models from **Civitai** (URL import)
- supports **Bring your own** (local file import)
- records license acceptance per model in `data/config/licenses_accepted.json`

## Environment Variables (optional)

Recommended: set tokens via **Studio → Settings → Tokens** (stored in OS keychain when available).
Environment variables are still supported as a fallback.

- `CIVITAI_API_KEY` – optional; required for some gated downloads on Civitai.
- `HF_TOKEN` (or `HUGGINGFACE_TOKEN`) – optional; required for gated Hugging Face downloads.
- `EDMG_AWS_MODEL_CACHE=1` plus `EDMG_AWS_MODEL_CACHE_BUCKET` – optional private AWS S3 cache for single-file model weights. See `docs/AWS_MODEL_CACHE.md`.
- `EDMG_MODEL_STORAGE_MODE=cloud_only` – store supported single-file model installs in the enabled model cache without keeping a local Studio model copy. Use `local_cache` to restore the normal local install plus cache upload behavior.
- `EDMG_AZURE_MODEL_CACHE=1` plus `EDMG_AZURE_MODEL_CONTAINER` – optional Azure Blob cache for single-file model weights. See `docs/AZURE_MODEL_CACHE.md`.

## ComfyUI Model Locations

If ComfyUI Portable is installed via the Setup Wizard, models are copied to:

`data/third_party/ComfyUI_windows_portable/ComfyUI/models/<folder>/`

Otherwise, they are staged in:

`data/models/<folder>/`
