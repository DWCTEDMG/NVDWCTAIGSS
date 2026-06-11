from __future__ import annotations

from .config import Settings
from .providers.base import PlanProvider
from .providers.openai_compat import OpenAICompatPlanner


def build_provider(settings: Settings) -> PlanProvider:
    if settings.provider in ("openai_compat", "openai-compatible", "openai", "nvidia", "nvidia_nim", "nim"):
        return OpenAICompatPlanner(
            base_url=settings.openai_compat_base_url,
            api_key=settings.openai_compat_api_key,
            model=settings.openai_compat_model,
        )

    raise ValueError(
        f"Unsupported AI provider for NVIDIA profile: {settings.provider!r}. "
        "Set EDMG_AI_PROVIDER=openai_compat and configure EDMG_NVIDIA_NIM_URL."
    )
