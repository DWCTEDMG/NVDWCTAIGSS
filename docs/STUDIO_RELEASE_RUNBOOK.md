# EDMG NVIDIA Workshop Release Runbook

This runbook is for operators and end users installing EDMG NVIDIA Workshop
without terminal work.

## 1. First install

Windows:

1. Run the packaged Windows installer.
2. Choose the app install directory if you do not want the default location.
3. Launch EDMG NVIDIA Workshop.
4. Open NVIDIA runtime setup or diagnostics.
5. Pick a `Workshop Home` on the drive you want for heavy runtime storage, such as
   `D:\EDMG-NVIDIA-Workshop`.

Linux:

1. Use the NVIDIA backend/service deployment paths first.
2. Use the AppImage only for React control-surface compatibility work.
3. Configure NVIDIA NIM/OpenAI-compatible planning before running production jobs.
4. Open NVIDIA runtime setup if you are using the React control surface.
5. Pick a `Workshop Home` on the volume you want for heavy runtime storage, such as
   `/mnt/media/EDMG-NVIDIA-Workshop`.

The packaged app install directory and `Workshop Home` are intentionally separate:

- install directory: the app itself
- Workshop Home: projects, models, cache, logs, external tools, NVIDIA runtime data, and UI data

## 2. Choose storage roots

The Workshop manages separate roots under `Workshop Home` by default:

- `data`
- `models`
- `cache`
- `logs`
- `external`

If you keep the default managed layout, the Workshop will place these under:

```text
<Workshop Home>/data
<Workshop Home>/models
<Workshop Home>/cache
<Workshop Home>/logs
<Workshop Home>/external
```

The Workshop also manages:

- UI app/session data under `<Workshop Home>/electron`

If you change roots from an older layout, Studio queues a migration and applies
it on restart.

## 3. Configure NVIDIA AI provider

Open `Settings` to choose the planning provider.

Supported path:

- `NVIDIA NIM / OpenAI-compatible`
  - default planning path for Kit/OpenUSD scene plans

Provider notes:

- If you choose `OpenAI-compatible`, set the base URL, model, and API key in
  Settings.
- The NVIDIA-only profile rejects Ollama, rule-based, and local CPU fallback
  providers.

## 4. Install models and tools

From `Setup`:

- verify bundled FFmpeg
- verify/install the managed 7-Zip portable tool on Windows when you need the portable ComfyUI workflow
- verify NVIDIA NIM/Riva/Triton/Omniverse readiness
- verify/download ComfyUI Portable on Windows, or point Linux Studio at an existing ComfyUI server

From `Models`:

- install curated Hugging Face models
- import supported Civitai assets
- bring your own local model files

## 5. First project proof

Use this path to confirm the install is healthy:

1. Create a project.
2. Upload a WAV or other supported audio file.
3. Run `Analyze`.
4. Generate a NVIDIA scene plan.
5. Export or open the OpenUSD stage in Kit.
6. Queue or run a fast RTX render job.
7. Verify the output appears under `Outputs`.

## 6. Upgrade and migration recovery

If you are moving from an older `C:\`-based layout or from a smaller Linux home-directory location:

1. Set the new `Workshop Home`.
2. Save and restart the Workshop.
3. Wait for the queued migration to complete.
4. Re-open `Setup` and confirm the migration status shows success.

Expected migrated categories:

- project data
- models
- cache
- logs
- external tools
- UI user data

## 7. Common recovery steps

### Setup says FFmpeg is missing

- packaged Studio should prefer its bundled FFmpeg
- if that fails, rebuild the package and re-run `Setup`

### ComfyUI is reachable but unusable

- The Workshop should reject unusable CUDA render setups instead of routing
  through proxy or CPU fallback
- verify the render recommendation in `Render` or pipeline validation

### Old data still appears split across drives

- confirm `Workshop Home` points at the desired target root
- restart the Workshop so the pending migration can run
- confirm migration status in NVIDIA runtime setup

### Packaged release proof

Operators should run:

```powershell
cd studio/edmg-studio
pnpm run check:tooling
pnpm run validate:release
```

Linux operators should run:

```bash
cd studio/edmg-studio
pnpm run check:tooling
pnpm run validate:release:linux
```

That includes:

- pnpm/package-manager and lockfile guardrails
- staged React control-surface validation
- `validate:release` on Windows additionally runs packaged customer-flow proof, packaged upgrade-proof migration test, and the zero-state setup proof for the NVIDIA setup path and 7-Zip
- `validate:release:linux` validates the React control surface and produces the Linux AppImage without invoking the Windows-only installer proofs

Canonical packaged desktop version source:

- `studio/edmg-studio/package.json#version`
