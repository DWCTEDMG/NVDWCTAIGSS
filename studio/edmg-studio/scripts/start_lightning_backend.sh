#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${STUDIO_ROOT}/python_backend"
BACKEND_SETUPTOOLS_CONSTRAINT="setuptools==70.0.0"

pick_python_bin() {
  if [[ -n "${EDMG_PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${EDMG_PYTHON_BIN}"
    return 0
  fi

  local candidate
  for candidate in python3.11 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "No usable Python interpreter found. Set EDMG_PYTHON_BIN or install python3.11." >&2
  return 1
}

warn_if_unset() {
  local key="$1"
  local fallback="$2"
  if [[ -z "${!key:-}" ]]; then
    echo "[warn] ${key} is unset. Backend will fall back to ${fallback}."
  fi
}

PYTHON_BIN="$(pick_python_bin)"

export EDMG_STUDIO_HOME="${EDMG_STUDIO_HOME:-${HOME}/edmg-studio-home}"
export EDMG_STUDIO_BACKEND_HOST="${EDMG_STUDIO_BACKEND_HOST:-0.0.0.0}"
export EDMG_STUDIO_BACKEND_PORT="${EDMG_STUDIO_BACKEND_PORT:-7863}"
export EDMG_FFMPEG_PATH="${EDMG_FFMPEG_PATH:-ffmpeg}"

mkdir -p \
  "${EDMG_STUDIO_HOME}/data" \
  "${EDMG_STUDIO_HOME}/models" \
  "${EDMG_STUDIO_HOME}/cache" \
  "${EDMG_STUDIO_HOME}/logs" \
  "${EDMG_STUDIO_HOME}/external"

cd "${BACKEND_DIR}"

if [[ -d "${BACKEND_DIR}/venv" ]]; then
  # shellcheck source=/dev/null
  source "${BACKEND_DIR}/venv/bin/activate"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  echo "[setup] using active virtualenv at ${VIRTUAL_ENV}"
elif [[ -n "${CONDA_PREFIX:-}" ]]; then
  echo "[setup] using active conda environment at ${CONDA_PREFIX}"
else
  echo "[setup] creating virtualenv with ${PYTHON_BIN}"
  "${PYTHON_BIN}" -m venv venv
  # shellcheck source=/dev/null
  source "${BACKEND_DIR}/venv/bin/activate"
fi

if [[ "${EDMG_SKIP_BOOTSTRAP:-0}" != "1" ]]; then
  echo "[setup] upgrading pip tooling"
  python -m pip install -U pip "${BACKEND_SETUPTOOLS_CONSTRAINT}" wheel
  echo "[setup] installing backend bundle"
  python -m pip install -e ".[studio_bundle]"
fi

echo "[edmg] studio root: ${STUDIO_ROOT}"
echo "[edmg] backend dir: ${BACKEND_DIR}"
echo "[edmg] studio home: ${EDMG_STUDIO_HOME}"
echo "[edmg] backend url: http://${EDMG_STUDIO_BACKEND_HOST}:${EDMG_STUDIO_BACKEND_PORT}"
export EDMG_NVIDIA_MODE="${EDMG_NVIDIA_MODE:-1}"
export EDMG_NVIDIA_PROFILE="${EDMG_NVIDIA_PROFILE:-omniverse}"
export EDMG_NVIDIA_NIM_URL="${EDMG_NVIDIA_NIM_URL:-http://127.0.0.1:8001}"
export EDMG_AI_PROVIDER="${EDMG_AI_PROVIDER:-openai_compat}"
export EDMG_AI_OPENAI_COMPAT_BASE_URL="${EDMG_AI_OPENAI_COMPAT_BASE_URL:-${EDMG_NVIDIA_NIM_URL%/}/v1}"
export EDMG_AI_OPENAI_COMPAT_MODEL="${EDMG_AI_OPENAI_COMPAT_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1.5}"
export EDMG_NVIDIA_RIVA_URL="${EDMG_NVIDIA_RIVA_URL:-127.0.0.1:50051}"
export EDMG_NVIDIA_RIVA_ASR="${EDMG_NVIDIA_RIVA_ASR:-1}"
export EDMG_NVIDIA_RIVA_ASR_FALLBACK="${EDMG_NVIDIA_RIVA_ASR_FALLBACK:-0}"
echo "[edmg] ai provider: ${EDMG_AI_PROVIDER}"
echo "[edmg] comfyui url: ${EDMG_COMFYUI_URL:-http://127.0.0.1:8188}"

if [[ "${EDMG_AI_PROVIDER}" != "openai_compat" ]]; then
  echo "[error] NVIDIA-only profile requires EDMG_AI_PROVIDER=openai_compat." >&2
  exit 1
fi
warn_if_unset "EDMG_AI_OPENAI_COMPAT_BASE_URL" "http://127.0.0.1:8001/v1"
warn_if_unset "EDMG_AI_OPENAI_COMPAT_MODEL" "nvidia/llama-3.3-nemotron-super-49b-v1.5"

exec python -m edmg_studio_backend serve \
  --host "${EDMG_STUDIO_BACKEND_HOST}" \
  --port "${EDMG_STUDIO_BACKEND_PORT}"
