# NVIDIA Kit Runtime Packaging Decision

Decision date: 2026-06-01

## Decision

EDMG NVIDIA Studio uses a thin Kit runtime distribution policy by default.

- Public/external packages must not embed the local Kit SDK tree.
- The Windows app bundle carries EDMG-owned Kit app config, extensions, tools,
  helper scripts, and a launcher that resolves an installed Kit SDK.
- Internal development builds may use `-PackageMode internal-fat` only for
  licensed/internal testing where copying the selected Kit SDK is allowed.
- The local staging artifact is `dist/nvidia-kit-app/edmg-nvidia-kit`; it is a
  source/app bundle, not a claim that the Kit SDK is redistributable.

## Rationale

NVIDIA's current Kit App Template packaging docs distinguish fat packages, which
contain the Kit Kernel and extensions and are suitable for internal team sharing,
from thin packages, which carry the minimum installer payload and download or
resolve Kit components at install time. The same docs describe thin packages as
the shape organizations should wrap in their own installer.

NVIDIA's redistributable Omniverse software docs also scope Kit Kernel and
extension redistribution to specific licensing and Configurator Runtime cases.
The current product-specific terms further distinguish enterprise distribution
grants from developer-program access, with developer-program software limited to
internal evaluation, development, or test non-production purposes.

Official references checked on 2026-06-01:

- NVIDIA Kit App Template packaging:
  <https://docs.omniverse.nvidia.com/kit/docs/kit-app-template/latest/docs/packaging_app.html>
- NVIDIA Redistributable Omniverse Software:
  <https://docs.omniverse.nvidia.com/install-guide/latest/common/redistributable-ov-software.html>
- NVIDIA Omniverse product-specific terms:
  <https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/Product-Specific-Terms-for-NVIDIA-Omniverse-14Oct2025.pdf>

The conservative implementation is therefore:

1. Stage thin app bundles by default.
2. Make SDK inclusion explicit and named `internal-fat`.
3. Keep secrets, model weights, accepted license artifacts, and SDK binaries out
   of tracked source control.
4. Move to an installer-managed thin Kit App Template package only after the
   exact SDK/template version and license path are locked.

## Implemented Boundary

`deployment/nvidia/stage_nvidia_kit_app.ps1` now supports:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/stage_nvidia_kit_app.ps1 `
  -PackageMode thin `
  -BackendUrl "http://127.0.0.1:7863" `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -ProjectId "febd7dcbeb464b79bf4db78a7b6151c4" `
  -FfmpegPath "C:\Users\lanak\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe" `
  -Clean
```

The generated `bundle-manifest.json` records:

- `package_mode`
- `kit_sdk_included`
- `kit_sdk_policy`
- resolved Kit SDK, FFmpeg, backend, and project paths

For internal SDK-included bundles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File deployment/nvidia/stage_nvidia_kit_app.ps1 `
  -PackageMode internal-fat `
  -KitSdkRoot "D:\NVIDIA\Omniverse\kit-sdk-110.1.1" `
  -Clean
```

Use `internal-fat` only after confirming the intended audience and license allow
copying that SDK tree.

## Next Installer Step

The next Windows installer implementation should wrap the thin staged bundle and
perform these checks:

1. Confirm a Kit SDK is already installed or download/pull it through the
   selected NVIDIA-approved mechanism.
2. Write the chosen Kit SDK path into app settings or the generated launcher.
3. Validate `kit.exe`, FFmpeg, backend health, and `/v1/nvidia/diagnostics`.
4. Run `deployment/nvidia/run_live_gpu_smoke.ps1` on a self-hosted Windows
   NVIDIA runner before promoting a release.
