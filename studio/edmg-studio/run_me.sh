#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "$ROOT_DIR/deployment/nvidia/start_nvidia_runtime_local.ps1" "$@"
fi

if command -v powershell >/dev/null 2>&1; then
  exec powershell -NoProfile -ExecutionPolicy Bypass -File "$ROOT_DIR/deployment/nvidia/start_nvidia_runtime_local.ps1" "$@"
fi

echo "NVIDIA Kit launch requires PowerShell and an Omniverse Kit SDK path."
echo "Install PowerShell or run on Windows, then retry."
  exit 1
