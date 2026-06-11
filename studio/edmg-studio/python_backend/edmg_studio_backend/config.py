from __future__ import annotations
import os
import json
import sys
from dataclasses import dataclass
from pathlib import Path

def _split_csv(s: str) -> list[str]:
    return [p.strip().rstrip("/") for p in (s or "").split(",") if p.strip()]


def _ffmpeg_binary_name() -> str:
    return "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def _default_ffmpeg_path() -> str:
    explicit = os.getenv("EDMG_FFMPEG_PATH", "").strip()
    if explicit:
        if not os.path.isabs(explicit) or Path(explicit).exists():
            return explicit

    candidates: list[Path] = []
    exe_name = _ffmpeg_binary_name()

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        candidates.extend([
            exe_path.parent.parent / "bin" / exe_name,
            exe_path.parent / exe_name,
        ])

    studio_root = Path(__file__).resolve().parents[2]
    candidates.append(studio_root / "electron-resources" / "bin" / exe_name)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    if explicit:
        return explicit

    return "ffmpeg"


def _resolve_path_env(primary: str, fallback: str) -> Path:
    explicit = os.getenv(primary, "").strip()
    value = explicit or fallback
    return Path(value).expanduser().resolve()


def _default_studio_home(data_dir: Path) -> Path:
    explicit = os.getenv("EDMG_STUDIO_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return data_dir.parent.resolve()

@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve()
    studio_home: Path = _default_studio_home(Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve())
    models_dir: Path = _resolve_path_env(
        "EDMG_STUDIO_MODELS_DIR",
        str((_default_studio_home(Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve()) / "models").resolve()),
    )
    cache_dir: Path = _resolve_path_env(
        "EDMG_STUDIO_CACHE_DIR",
        str((_default_studio_home(Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve()) / "cache").resolve()),
    )
    logs_dir: Path = _resolve_path_env(
        "EDMG_STUDIO_LOGS_DIR",
        str((_default_studio_home(Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve()) / "logs").resolve()),
    )
    external_dir: Path = _resolve_path_env(
        "EDMG_STUDIO_EXTERNAL_DIR",
        str((_default_studio_home(Path(os.getenv("EDMG_STUDIO_DATA_DIR", "./data")).resolve()) / "external").resolve()),
    )
    # AI mode:
    #  - local (default): run AI in-process using services/ai/edmg_ai_service and NVIDIA NIM/OpenAI-compat directly.
    #  - http: talk to an external AI service via EDMG_AI_BASE_URL.
    ai_mode: str = os.getenv("EDMG_AI_MODE", "local").strip().lower()
    ai_base_url: str = os.getenv("EDMG_AI_BASE_URL", "http://127.0.0.1:7862").rstrip("/")
    ai_timeout_s: float = float(os.getenv("EDMG_AI_TIMEOUT_S", "180"))
    # Back-compat single ComfyUI endpoint:
    comfyui_url: str = os.getenv("EDMG_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    # Multi-node ComfyUI endpoints (comma-separated). If unset, falls back to comfyui_url.
    comfyui_urls: tuple[str, ...] = tuple(_split_csv(os.getenv("EDMG_COMFYUI_URLS", "")) or [])
    comfyui_checkpoint: str = os.getenv("EDMG_COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors")
    ffmpeg_path: str = _default_ffmpeg_path()

    # Worker settings
    worker_autostart: bool = os.getenv("EDMG_WORKER_AUTOSTART", "1").strip() not in ("0","false","False","no","NO")
    worker_concurrency: int = int(os.getenv("EDMG_WORKER_CONCURRENCY", "1"))
    worker_poll_interval_s: float = float(os.getenv("EDMG_WORKER_POLL_INTERVAL_S", "0.5"))

    # Per ComfyUI node max in-flight prompts (simple throttle, per backend process).
    comfyui_node_concurrency: int = int(os.getenv("EDMG_COMFYUI_NODE_CONCURRENCY", "1"))

    # Optional JSON config describing nodes (capabilities, cost, tags, per-node concurrency override).
    # Either provide a JSON string via EDMG_COMFYUI_NODES_JSON, or a path via EDMG_COMFYUI_NODES_CONFIG.
    comfyui_nodes_json: str = os.getenv("EDMG_COMFYUI_NODES_JSON", "").strip()
    comfyui_nodes_config: str = os.getenv("EDMG_COMFYUI_NODES_CONFIG", "").strip()


    def resolved_comfyui_urls(self) -> tuple[str, ...]:
        urls = self.comfyui_urls or ()
        if urls:
            return urls
        return (self.comfyui_url,)


    def load_comfyui_nodes(self) -> list[dict]:
        """Return list of node config dicts.

        Schema (each element):
          - url (required)
          - max_inflight (optional int; overrides comfyui_node_concurrency)
          - cost (optional float; lower preferred)
          - tags (optional list[str])
          - checkpoints (optional list[str]) - exact checkpoint filenames supported
          - checkpoint_regex (optional list[str]) - regex patterns for supported checkpoints
          - requires_nodes (optional list[str]) - node class names expected to exist (capability hint)
        """
        # 1) explicit JSON string
        if self.comfyui_nodes_json:
            try:
                data = json.loads(self.comfyui_nodes_json)
                if isinstance(data, dict) and "nodes" in data:
                    data = data["nodes"]
                if isinstance(data, list):
                    return data
            except Exception:
                pass

        # 2) file path
        path = self.comfyui_nodes_config
        if path:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "nodes" in data:
                        data = data["nodes"]
                    if isinstance(data, list):
                        return data
                except Exception:
                    pass

        # 3) fall back to simple URL list
        return [{"url": u} for u in self.resolved_comfyui_urls()]
