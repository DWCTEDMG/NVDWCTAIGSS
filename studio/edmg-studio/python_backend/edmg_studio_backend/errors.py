from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UserFacingError(Exception):
    message: str
    hint: str | None = None
    code: str | None = None
    status_code: int = 400

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "hint": self.hint,
            "code": self.code,
        }


def hint_from_exception(e: Exception) -> str | None:
    s = str(e)
    sl = s.lower()

    if "401" in sl or "403" in sl or "unauthorized" in sl or "forbidden" in sl:
        if "civitai" in sl:
            return "Set CIVITAI_API_KEY in Settings → Tokens (some downloads require auth), then retry."
        if "huggingface" in sl or "hf.co" in sl:
            return "Set a Hugging Face token in Settings → Tokens (needed for gated models), then retry."
        return "Check your API token/permissions in Settings → Tokens, then retry."

    if "ffmpeg" in sl and ("not found" in sl or "edmg_ffmpeg_path" in sl or "on path" in sl):
        return "Install FFmpeg, add it to PATH, or set EDMG_FFMPEG_PATH. Then re-run Assemble."

    if "connection refused" in sl or "failed to establish a new connection" in sl or "max retries exceeded" in sl:
        if "8001" in sl or "nim" in sl or "openai-compatible" in sl:
            return "Start the NVIDIA NIM/OpenAI-compatible planner endpoint and ensure EDMG_NVIDIA_NIM_URL is correct (default http://127.0.0.1:8001)."
        return "Start ComfyUI and make sure EDMG_COMFYUI_URL points to it (default http://127.0.0.1:8188)."

    if "missing_node_classes" in sl or "ade_" in sl:
        return "Install ComfyUI-AnimateDiff-Evolved nodes, or switch Render preset/mode to Stills."

    if "svd" in sl and "missing" in sl:
        return "Install ComfyUI Stable Video Diffusion nodes (SVDSimpleImg2Vid), or switch to Stills/AnimateDiff."

    if "timed out" in sl and "comfyui" in sl:
        return "ComfyUI may be busy or stuck. Check ComfyUI console, then retry the job."

    if "symlink loop" in sl or "too many symbolic links" in sl or "winerror 1921" in sl:
        return "EDMG NVIDIA Workshop detected a broken storage junction. Restart EDMG NVIDIA Workshop so it can repair the models path, then retry."

    return None
