from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # planning provider
    provider: str = os.getenv("EDMG_AI_PROVIDER", "openai_compat").strip().lower()

    # ollama
    ollama_url: str = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434").strip()
    ollama_model: str = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b").strip()

    # openai-compatible
    openai_compat_base_url: str = os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8001/v1").strip()
    openai_compat_api_key: str | None = (os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY") or None)
    openai_compat_model: str = os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5").strip()
