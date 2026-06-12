from __future__ import annotations

from typing import Any


def _riva_only_error() -> RuntimeError:
    return RuntimeError(
        "Local ASR is disabled by the NVIDIA-only profile. "
        "Use NVIDIA Riva ASR through the NVIDIA Workshop runtime."
    )


def transcribe_detailed(path: str, model_size: str = "turbo") -> dict[str, Any]:
    raise _riva_only_error()


def transcribe(path: str, model_size: str = "turbo") -> str:
    raise _riva_only_error()
