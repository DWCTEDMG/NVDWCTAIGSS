#!/usr/bin/env bash
set -euo pipefail

# EDMG Studio reinstall/bootstrap for a Vast.ai PyTorch instance.
# Supports arbitrary exposed container ports through BACKEND_PORT and UI_PORT.

REPO_URL="${REPO_URL:-https://github.com/HIMOI890/DWCTGenerativeSoundStudio.git}"
REPO_BRANCH="${REPO_BRANCH:-codex/Unified}"
REPO_DIR="${REPO_DIR:-/workspace/src/DWCTGenerativeSoundStudio}"
STUDIO_DIR="$REPO_DIR/studio/edmg-studio"
BACKEND_DIR="$STUDIO_DIR/python_backend"
STUDIO_HOME="${EDMG_STUDIO_HOME:-/workspace/studio-home}"
LOG_DIR="$STUDIO_HOME/logs"
MODELS_DIR="$STUDIO_HOME/models"
CACHE_DIR="$STUDIO_HOME/cache"
EXTERNAL_DIR="$STUDIO_HOME/external"
DATA_DIR="$STUDIO_HOME/data"
HF_TOKEN_VALUE="${HF_TOKEN:-}"
BACKEND_PORT="${BACKEND_PORT:-8080}"
UI_PORT="${UI_PORT:-1111}"
NVIDIA_NIM_URL="${EDMG_NVIDIA_NIM_URL:-${NVIDIA_NIM_URL:-http://127.0.0.1:8001}}"
NVIDIA_NIM_BASE_URL="${EDMG_AI_OPENAI_COMPAT_BASE_URL:-${NVIDIA_NIM_URL%/}/v1}"
NVIDIA_NIM_MODEL="${EDMG_AI_OPENAI_COMPAT_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1.5}"
NVIDIA_RIVA_URL="${EDMG_NVIDIA_RIVA_URL:-${NVIDIA_RIVA_URL:-127.0.0.1:50051}}"

export DEBIAN_FRONTEND=noninteractive
export EDMG_STUDIO_HOME="$STUDIO_HOME"
export EDMG_STUDIO_DATA_DIR="$DATA_DIR"
export EDMG_STUDIO_MODELS_DIR="$MODELS_DIR"
export EDMG_STUDIO_CACHE_DIR="$CACHE_DIR"
export EDMG_STUDIO_LOGS_DIR="$LOG_DIR"
export EDMG_STUDIO_EXTERNAL_DIR="$EXTERNAL_DIR"
export HF_HOME="$CACHE_DIR/huggingface"
export TORCH_HOME="$CACHE_DIR/torch"
export PATH="$PATH:/workspace/bin"
if [[ -n "$HF_TOKEN_VALUE" ]]; then
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN_VALUE"
  export HF_TOKEN="$HF_TOKEN_VALUE"
fi

mkdir -p /workspace/src /workspace/bin "$DATA_DIR" "$MODELS_DIR" "$CACHE_DIR" "$LOG_DIR" "$EXTERNAL_DIR" "$HF_HOME" "$TORCH_HOME"

cat > /etc/profile.d/dwct-edmg.sh <<DWCTPROFILEEOF
export EDMG_STUDIO_HOME=$STUDIO_HOME
export EDMG_STUDIO_DATA_DIR=$DATA_DIR
export EDMG_STUDIO_MODELS_DIR=$MODELS_DIR
export EDMG_STUDIO_CACHE_DIR=$CACHE_DIR
export EDMG_STUDIO_LOGS_DIR=$LOG_DIR
export EDMG_STUDIO_EXTERNAL_DIR=$EXTERNAL_DIR
export HF_HOME=$HF_HOME
export TORCH_HOME=$TORCH_HOME
export EDMG_NVIDIA_MODE=1
export EDMG_NVIDIA_PROFILE=omniverse
export EDMG_NVIDIA_NIM_URL=$NVIDIA_NIM_URL
export EDMG_NVIDIA_RIVA_URL=$NVIDIA_RIVA_URL
export EDMG_NVIDIA_RIVA_ASR=1
export EDMG_NVIDIA_RIVA_ASR_FALLBACK=0
export EDMG_AI_MODE=local
export EDMG_AI_PROVIDER=openai_compat
export EDMG_AI_OPENAI_COMPAT_BASE_URL=$NVIDIA_NIM_BASE_URL
export EDMG_AI_OPENAI_COMPAT_MODEL=$NVIDIA_NIM_MODEL
export PATH=\$PATH:/workspace/bin
DWCTPROFILEEOF

printf '\n[1/8] Installing system packages...\n'
apt-get update
apt-get install -y \
  git git-lfs curl wget ca-certificates unzip rsync ffmpeg \
  libsndfile1 libsndfile1-dev libgomp1 build-essential pkg-config \
  cmake ninja-build tmux htop nvtop python3-venv python3-pip python-is-python3 \
  p7zip-full jq

git lfs install || true

printf '\n[2/8] Installing Node 20 + pnpm...\n'
if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v2[0-9]\.'; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
corepack enable || true
corepack prepare pnpm@10.33.0 --activate || npm install -g pnpm@10.33.0

printf '\n[3/8] Syncing repo branch %s...\n' "$REPO_BRANCH"
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" fetch origin "$REPO_BRANCH" --prune
  git -C "$REPO_DIR" checkout "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --hard "origin/$REPO_BRANCH"
else
  rm -rf "$REPO_DIR"
  git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
fi

git -C "$REPO_DIR" rev-parse --short HEAD | tee "$LOG_DIR/repo-head.txt"

printf '\n[4/8] Installing backend bundle...\n'
cd "$BACKEND_DIR"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[studio_bundle]"
python - <<'PY'
try:
    import torch
    print('torch=' + str(torch.__version__))
    print('cuda_build=' + str(torch.version.cuda))
    print('cuda_available=' + str(torch.cuda.is_available()))
    if torch.cuda.is_available():
        print('device_0=' + str(torch.cuda.get_device_name(0)))
except Exception as exc:
    print('torch_check_error=' + repr(exc))
PY

printf '\n[5/8] Installing frontend deps...\n'
cd "$STUDIO_DIR"
pnpm install --frozen-lockfile || pnpm install

printf '\n[6/8] Configuring NVIDIA planner runtime...\n'
printf 'NVIDIA NIM endpoint: %s\n' "$NVIDIA_NIM_BASE_URL"
printf 'NVIDIA Riva endpoint: %s\n' "$NVIDIA_RIVA_URL"

printf '\n[7/8] Writing service helper scripts...\n'
cat > /workspace/bin/dwct-start-backend <<DWCTBACKENDEOF
#!/usr/bin/env bash
set -euo pipefail
source /etc/profile.d/dwct-edmg.sh
export EDMG_AI_MODE=local
export EDMG_AI_PROVIDER=openai_compat
export EDMG_AI_OPENAI_COMPAT_BASE_URL=$NVIDIA_NIM_BASE_URL
export EDMG_AI_OPENAI_COMPAT_MODEL=$NVIDIA_NIM_MODEL
cd /workspace/src/DWCTGenerativeSoundStudio/studio/edmg-studio/python_backend
. .venv/bin/activate
exec edmg-studio-backend serve --host 0.0.0.0 --port $BACKEND_PORT
DWCTBACKENDEOF

cat > /workspace/bin/dwct-start-ui <<DWCTUIEOF
#!/usr/bin/env bash
set -euo pipefail
source /etc/profile.d/dwct-edmg.sh
cd /workspace/src/DWCTGenerativeSoundStudio/studio/edmg-studio
corepack enable >/dev/null 2>&1 || true
corepack prepare pnpm@10.33.0 --activate >/dev/null 2>&1 || true
exec pnpm exec vite --host 0.0.0.0 --port $UI_PORT --strictPort
DWCTUIEOF

cat > /workspace/bin/dwct-check <<DWCTCHECKEOF
#!/usr/bin/env bash
set -euo pipefail
source /etc/profile.d/dwct-edmg.sh
printf '=== GPU ===\n'
nvidia-smi || true
printf '\n=== CUDA/PyTorch ===\n'
cd /workspace/src/DWCTGenerativeSoundStudio/studio/edmg-studio/python_backend
. .venv/bin/activate
python - <<'PY'
import torch
print(f'torch={torch.__version__}')
print(f'cuda_build={torch.version.cuda}')
print(f'cuda_available={torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'device_0={torch.cuda.get_device_name(0)}')
PY
printf '\n=== Backend ===\n'
curl -fsS http://127.0.0.1:$BACKEND_PORT/health || true
printf '\n\n=== UI ===\n'
curl -fsSI http://127.0.0.1:$UI_PORT | head || true
printf '\n=== NVIDIA NIM ===\n'
curl -fsS "$NVIDIA_NIM_BASE_URL/models" || true
printf '\n'
DWCTCHECKEOF

cat > /workspace/bin/dwct-install-models <<DWCTMODELSEOF
#!/usr/bin/env bash
set -euo pipefail
source /etc/profile.d/dwct-edmg.sh
LOG_DIR="\$EDMG_STUDIO_LOGS_DIR"
mkdir -p "\$LOG_DIR"
models=(
  hf_sd15_internal
  hf_sdxl_internal
  hf_sdxl_base_1_0
  hf_sdxl_refiner_1_0
  hf_sd15_controlnet_canny_internal
  hf_sd15_controlnet_depth_internal
  hf_sdxl_controlnet_canny_internal
  hf_sdxl_controlnet_depth_internal
  hf_sd35_medium_internal
  hf_sd35_large_turbo_ckpt
  hf_sd35_controlnet_blur
  hf_sd35_controlnet_canny
  hf_sd35_controlnet_depth
  hf_svd_xt_1_1
)
for mid in "\${models[@]}"; do
  echo "[\$(date -Is)] queue \$mid"
  curl -fsS -X POST http://127.0.0.1:$BACKEND_PORT/v1/models/accept \
    -H 'Content-Type: application/json' \
    -d "{\"model_id\":\"\$mid\",\"license_id\":\"accepted-via-vast-bootstrap\"}" || true
  curl -fsS -X POST http://127.0.0.1:$BACKEND_PORT/v1/models/install \
    -H 'Content-Type: application/json' \
    -d "{\"model_id\":\"\$mid\"}" || true
  sleep 1
done
curl -fsS http://127.0.0.1:$BACKEND_PORT/v1/models/tasks | tee "\$LOG_DIR/model-tasks-after-queue.json" || true
DWCTMODELSEOF

chmod +x /workspace/bin/dwct-start-backend /workspace/bin/dwct-start-ui /workspace/bin/dwct-check /workspace/bin/dwct-install-models

printf '\n[8/8] Starting backend and UI...\n'
pkill -f 'edmg-studio-backend serve' || true
pkill -f 'vite --host 0.0.0.0' || true
nohup /workspace/bin/dwct-start-backend > "$LOG_DIR/backend.log" 2>&1 &
nohup /workspace/bin/dwct-start-ui > "$LOG_DIR/ui.log" 2>&1 &

for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/tmp/edmg-backend-health.json 2>/dev/null; then break; fi
  sleep 2
done
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$UI_PORT" >/tmp/edmg-ui-index.html 2>/dev/null; then break; fi
  sleep 2
done

nohup /workspace/bin/dwct-install-models > "$LOG_DIR/install-models.log" 2>&1 &

printf '\n=== CHECK ===\n'
/workspace/bin/dwct-check || true
printf '\n=== LOGS ===\nbackend: %s\nui: %s\nmodels: %s\n' "$LOG_DIR/backend.log" "$LOG_DIR/ui.log" "$LOG_DIR/install-models.log"
