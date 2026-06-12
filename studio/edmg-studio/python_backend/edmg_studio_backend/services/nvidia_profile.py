from __future__ import annotations

import os
from typing import Any


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured(value: str) -> bool:
    return bool(str(value or "").strip())


def _service(base_url_env: str, *, model_env: str = "") -> dict[str, Any]:
    base_url = _env(base_url_env)
    service: dict[str, Any] = {
        "base_url": base_url,
        "configured": _configured(base_url),
    }
    if model_env:
        service["model"] = _env(model_env)
    return service


DEFAULT_NVIDIA_NIM_URL = "http://127.0.0.1:8001/v1"
DEFAULT_NVIDIA_NIM_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"


def nvidia_profile_status() -> dict[str, Any]:
    """Return NVIDIA service-profile configuration without exposing secrets."""

    nim_url = _env("EDMG_NVIDIA_NIM_URL") or _env("EDMG_AI_OPENAI_COMPAT_BASE_URL", DEFAULT_NVIDIA_NIM_URL)
    riva_url = _env("EDMG_NVIDIA_RIVA_URL") or _env("EDMG_RIVA_URL")
    omniverse_url = _env("EDMG_NVIDIA_OMNIVERSE_URL")
    ngc_api_key = _env("NGC_API_KEY")
    asr_provider = _env("EDMG_AI_ASR_PROVIDER").lower()
    riva_asr_enabled = (
        asr_provider in {"riva", "nvidia_riva"}
        or _truthy(_env("EDMG_NVIDIA_RIVA_ASR"))
        or _truthy(_env("EDMG_RIVA_ASR"))
    )

    return {
        "enabled": _truthy(_env("EDMG_NVIDIA_MODE", "1")),
        "profile": _env("EDMG_NVIDIA_PROFILE", "omniverse") or "omniverse",
        "credentials": {
            "ngc_api_key_configured": _configured(ngc_api_key),
        },
        "services": {
            "nim": {
                "base_url": nim_url,
                "model": _env("EDMG_AI_OPENAI_COMPAT_MODEL", DEFAULT_NVIDIA_NIM_MODEL),
                "configured": _configured(nim_url),
            },
            "riva": {
                "base_url": riva_url,
                "configured": _configured(riva_url),
                "asr_enabled": riva_asr_enabled,
                "language_code": _env("EDMG_NVIDIA_RIVA_LANGUAGE_CODE", "en-US") or "en-US",
                "model": _env("EDMG_NVIDIA_RIVA_MODEL"),
                "ssl": _truthy(_env("EDMG_NVIDIA_RIVA_USE_SSL")),
            },
            "omniverse": {
                "base_url": omniverse_url,
                "configured": _configured(omniverse_url),
            },
            "nemo": _service("EDMG_NVIDIA_NEMO_URL"),
            "triton": _service("EDMG_NVIDIA_TRITON_URL", model_env="EDMG_NVIDIA_TRITON_MODEL"),
            "audio2face": {
                **_service("EDMG_NVIDIA_AUDIO2FACE_URL"),
                "generate_path": _env("EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH"),
            },
            "ace": {
                **_service("EDMG_NVIDIA_ACE_URL"),
                "generate_path": _env("EDMG_NVIDIA_ACE_GENERATE_PATH"),
            },
            "cosmos": {
                **_service("EDMG_NVIDIA_COSMOS_URL", model_env="EDMG_NVIDIA_COSMOS_MODEL"),
                "infer_path": _env("EDMG_NVIDIA_COSMOS_INFER_PATH", "/v1/infer") or "/v1/infer",
            },
        },
    }
