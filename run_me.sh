#!/usr/bin/env bash

# Get the directory of the current script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to that directory
cd "$SCRIPT_DIR" || exit 1

# Run the NVIDIA Kit runtime first. The legacy Electron dev launcher remains
# available with: ./run_me.sh legacy-electron
if [ "${1:-}" = "legacy-electron" ]; then
  shift
  bash "$SCRIPT_DIR/studio/edmg-studio/run_me.sh" legacy-electron "$@"
  exit $?
fi

if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT_DIR/deployment/nvidia/start_nvidia_runtime_local.ps1" "$@"
fi

if command -v powershell >/dev/null 2>&1; then
  exec powershell -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT_DIR/deployment/nvidia/start_nvidia_runtime_local.ps1" "$@"
fi

echo "NVIDIA Kit launch requires PowerShell and an Omniverse Kit SDK path."
echo "Install PowerShell or run on Windows, then retry. For the old Electron dev UI:"
echo "  ./run_me.sh legacy-electron"
exit 1
