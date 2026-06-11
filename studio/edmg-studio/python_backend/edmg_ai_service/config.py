from __future__ import annotations

import os
from dataclasses import dataclass, field


def _uses_hosted_nvidia_api() -> bool:
    combined = " ".join(
        (
            os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", ""),
            os.getenv("EDMG_NVIDIA_NIM_URL", ""),
        )
    ).lower()
    return "integrate.api.nvidia.com" in combined


def _openai_compat_api_key() -> str | None:
    explicit = (os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY") or "").strip()
    if explicit:
        return explicit
    if not _uses_hosted_nvidia_api():
        return None
    for name in (
        "NVIDIA_API_KEY",
        "EDMG_NVIDIA_API_KEY",
        "EDMG_NVIDIA_NIM_API_KEY",
        "EDMG_NVIDIA_AUDIO2FACE_API_KEY",
        "NGC_API_KEY",
    ):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _openai_compat_base_url() -> str:
    raw = (
        os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL")
        or os.getenv("EDMG_NVIDIA_NIM_URL")
        or "http://127.0.0.1:8001"
    ).strip()
    raw = raw.rstrip("/")
    if raw.endswith("/v1"):
        return raw
    return f"{raw}/v1"


@dataclass(frozen=True)
class Settings:
    # planning provider
    provider: str = field(default_factory=lambda: os.getenv("EDMG_AI_PROVIDER", "openai_compat").strip().lower())

    # NVIDIA NIM / OpenAI-compatible planner
    openai_compat_base_url: str = field(default_factory=_openai_compat_base_url)
    openai_compat_api_key: str | None = field(default_factory=_openai_compat_api_key)
    openai_compat_model: str = field(
        default_factory=lambda: os.getenv(
            "EDMG_AI_OPENAI_COMPAT_MODEL",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        ).strip()
    )
