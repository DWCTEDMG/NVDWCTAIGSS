#!/usr/bin/env bash
set -euo pipefail

# Bootstrap EDMG Studio on an Ubuntu GPU VM after the instance, firewall, and
# NVIDIA driver are already in place.

REPO_URL="${REPO_URL:-https://github.com/HIMOI890/DWCTGenerativeSoundStudio.git}"
REPO_BRANCH="${REPO_BRANCH:-codex/Unified}"
REPO_PARENT="${REPO_PARENT:-/opt/edmg}"
REPO_DIR="${REPO_DIR:-${REPO_PARENT}/DWCTGenerativeSoundStudio}"
STUDIO_DIR="${REPO_DIR}/studio/edmg-studio"
BACKEND_DIR="${STUDIO_DIR}/python_backend"
STUDIO_HOME="${EDMG_STUDIO_HOME:-/mnt/edmg-studio-home}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-7863}"
UI_HOST="${UI_HOST:-0.0.0.0}"
UI_PORT="${UI_PORT:-5173}"
START_UI="${START_UI:-1}"
ENABLE_BACKEND_SERVICE="${ENABLE_BACKEND_SERVICE:-1}"
QUEUE_DEFAULT_MODELS="${QUEUE_DEFAULT_MODELS:-0}"
PIP_TORCH_INDEX_URL="${PIP_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu121}"
HF_TOKEN_VALUE="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"

BOOTSTRAP_USER="${SUDO_USER:-$USER}"
USER_HOME="${HOME}"
EDMG_ENV_FILE="${USER_HOME}/.edmg-env"
BIN_DIR="${USER_HOME}/bin"
DATA_DIR="${STUDIO_HOME}/data"
MODELS_DIR="${STUDIO_HOME}/models"
CACHE_DIR="${STUDIO_HOME}/cache"
LOG_DIR="${STUDIO_HOME}/logs"
EXTERNAL_DIR="${STUDIO_HOME}/external"
HF_HOME_DIR="${CACHE_DIR}/huggingface"
TORCH_HOME_DIR="${CACHE_DIR}/torch"
UI_LOG_PATH="${LOG_DIR}/ui.log"
MODEL_QUEUE_LOG_PATH="${LOG_DIR}/queue-default-models.log"
BACKEND_SERVICE_NAME="edmg-backend"

AI_MODE="${AI_MODE:-local}"
AI_PROVIDER="${AI_PROVIDER:-openai_compat}"
NVIDIA_NIM_URL="${EDMG_NVIDIA_NIM_URL:-${NVIDIA_NIM_URL:-http://127.0.0.1:8001}}"
NVIDIA_NIM_BASE_URL="${EDMG_AI_OPENAI_COMPAT_BASE_URL:-${NVIDIA_NIM_URL%/}/v1}"
NVIDIA_NIM_MODEL="${EDMG_AI_OPENAI_COMPAT_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1.5}"
NVIDIA_RIVA_URL="${EDMG_NVIDIA_RIVA_URL:-${NVIDIA_RIVA_URL:-127.0.0.1:50051}}"

if [[ $EUID -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

step() {
  echo "[edmg-gcp] $*"
}

warn() {
  echo "[edmg-gcp][warn] $*" >&2
}

fail() {
  echo "[edmg-gcp][error] $*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Required command not found: $1"
  fi
}

append_once() {
  local file="$1"
  local line="$2"
  touch "$file"
  if ! grep -qxF "$line" "$file"; then
    printf '%s\n' "$line" >>"$file"
  fi
}

pick_python_bin() {
  local candidate
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

write_env_file() {
  step "Writing ${EDMG_ENV_FILE}"
  cat >"${EDMG_ENV_FILE}" <<EOF
export EDMG_STUDIO_HOME=${STUDIO_HOME}
export EDMG_STUDIO_DATA_DIR=${DATA_DIR}
export EDMG_STUDIO_MODELS_DIR=${MODELS_DIR}
export EDMG_STUDIO_CACHE_DIR=${CACHE_DIR}
export EDMG_STUDIO_LOGS_DIR=${LOG_DIR}
export EDMG_STUDIO_EXTERNAL_DIR=${EXTERNAL_DIR}
export HF_HOME=${HF_HOME_DIR}
export TORCH_HOME=${TORCH_HOME_DIR}
export EDMG_STUDIO_BACKEND_HOST=${BACKEND_HOST}
export EDMG_STUDIO_BACKEND_PORT=${BACKEND_PORT}
export EDMG_NVIDIA_MODE=1
export EDMG_NVIDIA_PROFILE=omniverse
export EDMG_NVIDIA_NIM_URL=${NVIDIA_NIM_URL}
export EDMG_NVIDIA_RIVA_URL=${NVIDIA_RIVA_URL}
export EDMG_NVIDIA_RIVA_ASR=1
export EDMG_NVIDIA_RIVA_ASR_FALLBACK=0
export EDMG_AI_MODE=${AI_MODE}
export EDMG_AI_PROVIDER=${AI_PROVIDER}
export EDMG_AI_OPENAI_COMPAT_BASE_URL=${NVIDIA_NIM_BASE_URL}
export EDMG_AI_OPENAI_COMPAT_MODEL=${NVIDIA_NIM_MODEL}
export PATH=${BIN_DIR}:\$PATH
EOF
  append_once "${USER_HOME}/.bashrc" "source ${EDMG_ENV_FILE}"
}

ensure_directories() {
  step "Creating runtime directories under ${STUDIO_HOME}"
  "${SUDO[@]}" mkdir -p "${REPO_PARENT}" "${DATA_DIR}" "${MODELS_DIR}" "${CACHE_DIR}" "${LOG_DIR}" "${EXTERNAL_DIR}" "${HF_HOME_DIR}" "${TORCH_HOME_DIR}" "${BIN_DIR}"
  "${SUDO[@]}" chown -R "${BOOTSTRAP_USER}:${BOOTSTRAP_USER}" "${REPO_PARENT}" "${STUDIO_HOME}" "${BIN_DIR}"
}

install_system_packages() {
  step "Installing system packages"
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y \
    git git-lfs curl wget ca-certificates ffmpeg libsndfile1 libsndfile1-dev libgomp1 \
    build-essential pkg-config cmake ninja-build python3-venv python3-pip python-is-python3 \
    tmux htop nvtop unzip p7zip-full jq
  git lfs install || true
}

ensure_gpu_ready() {
  step "Checking NVIDIA driver state"
  require_cmd nvidia-smi
  nvidia-smi >/dev/null
}

install_node_and_pnpm() {
  local current_node=""
  if command -v node >/dev/null 2>&1; then
    current_node="$(node --version || true)"
  fi
  if [[ ! "$current_node" =~ ^v20\. ]]; then
    step "Installing Node.js 20"
    if [[ ${#SUDO[@]} -gt 0 ]]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | "${SUDO[@]}" -E bash -
    else
      curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    fi
    "${SUDO[@]}" apt-get install -y nodejs
  fi
  step "Activating pnpm"
  corepack enable
  corepack prepare pnpm@10.33.0 --activate
}

sync_repo() {
  step "Syncing ${REPO_BRANCH} into ${REPO_DIR}"
  if [[ -d "${REPO_DIR}/.git" ]]; then
    git -C "${REPO_DIR}" fetch origin "${REPO_BRANCH}" --prune
    if git -C "${REPO_DIR}" show-ref --verify --quiet "refs/heads/${REPO_BRANCH}"; then
      git -C "${REPO_DIR}" checkout "${REPO_BRANCH}"
    else
      git -C "${REPO_DIR}" checkout -b "${REPO_BRANCH}" "origin/${REPO_BRANCH}"
    fi
    git -C "${REPO_DIR}" pull --ff-only origin "${REPO_BRANCH}"
  else
    rm -rf "${REPO_DIR}"
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${REPO_DIR}"
  fi
}

install_backend() {
  local python_bin
  python_bin="$(pick_python_bin)" || fail "No usable Python interpreter found."

  step "Installing backend bundle into ${BACKEND_DIR}/.venv"
  cd "${BACKEND_DIR}"
  "${python_bin}" -m venv .venv
  # shellcheck source=/dev/null
  source .venv/bin/activate
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install torch torchvision torchaudio --index-url "${PIP_TORCH_INDEX_URL}"
  python -m pip install -e ".[studio_bundle]"

  step "Validating CUDA visibility from PyTorch"
  python - <<'PY'
import sys
import torch

print("torch", torch.__version__)
print("cuda_build", torch.version.cuda)
print("cuda_available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0))
else:
    sys.exit("PyTorch installed but CUDA is unavailable. Fix the NVIDIA driver before continuing.")
PY
}

install_frontend() {
  step "Installing frontend dependencies and running validation"
  cd "${STUDIO_DIR}"
  corepack enable
  corepack prepare pnpm@10.33.0 --activate
  pnpm install --frozen-lockfile || pnpm install
  pnpm run typecheck
  pnpm run test:ui
  pnpm run build
}

write_helper_scripts() {
  step "Writing helper scripts into ${BIN_DIR}"

  cat >"${BIN_DIR}/edmg-start-backend" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${EDMG_ENV_FILE}"
cd "${BACKEND_DIR}"
source .venv/bin/activate
exec edmg-studio-backend serve --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
EOF

  cat >"${BIN_DIR}/edmg-start-ui" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${EDMG_ENV_FILE}"
cd "${STUDIO_DIR}"
corepack enable >/dev/null 2>&1 || true
corepack prepare pnpm@10.33.0 --activate >/dev/null 2>&1 || true
exec pnpm exec vite --host "${UI_HOST}" --port "${UI_PORT}" --strictPort
EOF

  cat >"${BIN_DIR}/edmg-check-cuda" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${EDMG_ENV_FILE}"
cd "${BACKEND_DIR}"
source .venv/bin/activate
python - <<'PY'
import torch
print(f"torch={torch.__version__}")
print(f"cuda_build={torch.version.cuda}")
print(f"cuda_available={torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"device_0={torch.cuda.get_device_name(0)}")
PY
EOF

  cat >"${BIN_DIR}/edmg-queue-default-models" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "${EDMG_ENV_FILE}"
cd "${BACKEND_DIR}"
source .venv/bin/activate
python - <<'PY'
import json
import urllib.request

backend_url = f"http://127.0.0.1:${BACKEND_PORT}"
model_ids = [
    "hf_sd15_internal",
    "hf_sdxl_internal",
    "hf_sdxl_base_1_0",
    "hf_sd35_large_turbo_ckpt",
    "hf_sd35_controlnet_blur",
    "hf_sd35_controlnet_canny",
    "hf_sd35_controlnet_depth",
    "hf_svd_xt_1_1",
]

with urllib.request.urlopen(f"{backend_url}/v1/models/catalog", timeout=30) as response:
    payload = json.load(response)

catalog = {entry.get("id"): entry for entry in payload.get("catalog", [])}

def post_json(path: str, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{backend_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        print(response.read().decode("utf-8", errors="replace"))

for model_id in model_ids:
    entry = catalog.get(model_id)
    if not entry:
        print(f"skip {model_id}: not found in catalog")
        continue
    license_id = str(entry.get("license_id") or entry.get("licenseId") or "accepted-via-gcp-bootstrap")
    print(f"queue {model_id}")
    post_json("/v1/models/accept", {"model_id": model_id, "license_id": license_id})
    post_json("/v1/models/install", {"model_id": model_id})
PY
EOF

  chmod +x "${BIN_DIR}/edmg-start-backend" "${BIN_DIR}/edmg-start-ui" "${BIN_DIR}/edmg-check-cuda" "${BIN_DIR}/edmg-queue-default-models"
}

validate_nvidia_profile() {
  step "Using NVIDIA-only profile"
  if [[ "${AI_PROVIDER}" != "openai_compat" ]]; then
    fail "AI_PROVIDER must be openai_compat for the NVIDIA-only profile."
  fi
  step "NVIDIA NIM endpoint: ${NVIDIA_NIM_BASE_URL}"
  step "NVIDIA Riva endpoint: ${NVIDIA_RIVA_URL}"
}

install_backend_service() {
  if [[ "${ENABLE_BACKEND_SERVICE}" != "1" ]]; then
    step "Starting backend without systemd"
    pkill -f "edmg-studio-backend serve --host ${BACKEND_HOST} --port ${BACKEND_PORT}" || true
    nohup "${BIN_DIR}/edmg-start-backend" >"${LOG_DIR}/backend.log" 2>&1 &
    return
  fi

  step "Installing systemd service ${BACKEND_SERVICE_NAME}"
  "${SUDO[@]}" tee "/etc/systemd/system/${BACKEND_SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=EDMG Studio Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOOTSTRAP_USER}
WorkingDirectory=${BACKEND_DIR}
ExecStart=/bin/bash -lc 'source "${EDMG_ENV_FILE}" && source .venv/bin/activate && exec edmg-studio-backend serve --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable "${BACKEND_SERVICE_NAME}"
  "${SUDO[@]}" systemctl restart "${BACKEND_SERVICE_NAME}"
}

start_ui_if_requested() {
  if [[ "${START_UI}" != "1" ]]; then
    warn "Skipping Vite UI startup."
    return
  fi

  step "Starting Vite UI"
  pkill -f "vite --host ${UI_HOST} --port ${UI_PORT}" || true
  nohup "${BIN_DIR}/edmg-start-ui" >"${UI_LOG_PATH}" 2>&1 &
}

wait_for_health() {
  step "Validating backend health"
  for _ in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health" >"${LOG_DIR}/backend-health.json" 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  fail "Backend did not become healthy on port ${BACKEND_PORT}."
}

wait_for_ui_if_requested() {
  if [[ "${START_UI}" != "1" ]]; then
    return
  fi
  step "Validating Vite UI"
  for _ in $(seq 1 60); do
    if curl -fsSI "http://127.0.0.1:${UI_PORT}" >"${LOG_DIR}/ui-head.txt" 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  fail "Vite UI did not become reachable on port ${UI_PORT}."
}

queue_models_if_requested() {
  if [[ "${QUEUE_DEFAULT_MODELS}" != "1" ]]; then
    return
  fi
  if [[ -z "${HF_TOKEN_VALUE}" ]]; then
    warn "QUEUE_DEFAULT_MODELS=1 was requested, but HF_TOKEN is empty. Skipping auto-queue so gated downloads fail explicitly only when you choose them."
    return
  fi

  step "Queueing default model installs in the background"
  HF_TOKEN="${HF_TOKEN_VALUE}" HUGGING_FACE_HUB_TOKEN="${HF_TOKEN_VALUE}" nohup "${BIN_DIR}/edmg-queue-default-models" >"${MODEL_QUEUE_LOG_PATH}" 2>&1 &
}

print_summary() {
  local external_ip=""
  external_ip="$(curl -fsS -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" || true)"

  echo
  step "Bootstrap complete"
  echo "Backend health: http://127.0.0.1:${BACKEND_PORT}/health"
  if [[ -n "${external_ip}" ]]; then
    echo "External backend: http://${external_ip}:${BACKEND_PORT}"
    if [[ "${START_UI}" == "1" ]]; then
      echo "Browser frontend: http://${external_ip}:${UI_PORT}/?backendUrl=http://${external_ip}:${BACKEND_PORT}"
    fi
  fi
  echo "Studio Home: ${STUDIO_HOME}"
  echo "Backend log dir: ${LOG_DIR}"
  echo "UI log: ${UI_LOG_PATH}"
  if [[ "${QUEUE_DEFAULT_MODELS}" == "1" ]]; then
    echo "Model queue log: ${MODEL_QUEUE_LOG_PATH}"
  fi
}

main() {
  require_cmd curl
  require_cmd git
  install_system_packages
  ensure_gpu_ready
  ensure_directories
  write_env_file
  # shellcheck source=/dev/null
  source "${EDMG_ENV_FILE}"
  install_node_and_pnpm
  sync_repo
  install_backend
  install_frontend
  write_helper_scripts
  validate_nvidia_profile
  install_backend_service
  start_ui_if_requested
  wait_for_health
  wait_for_ui_if_requested
  queue_models_if_requested
  print_summary
}

main "$@"
