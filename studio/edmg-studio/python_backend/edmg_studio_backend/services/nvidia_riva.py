from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class RivaTranscriptionError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def riva_base_url() -> str:
    return _env("EDMG_NVIDIA_RIVA_URL") or _env("EDMG_RIVA_URL")


def riva_asr_enabled() -> bool:
    provider = _env("EDMG_AI_ASR_PROVIDER").lower()
    return provider in {"riva", "nvidia_riva"} or _truthy(_env("EDMG_NVIDIA_RIVA_ASR")) or _truthy(_env("EDMG_RIVA_ASR"))


def riva_fallback_enabled() -> bool:
    raw = _env("EDMG_NVIDIA_RIVA_ASR_FALLBACK", "1")
    return raw == "" or _truthy(raw)


def riva_language_code() -> str:
    return _env("EDMG_NVIDIA_RIVA_LANGUAGE_CODE", "en-US") or "en-US"


def riva_model_name() -> str:
    return _env("EDMG_NVIDIA_RIVA_MODEL")


def riva_use_ssl() -> bool:
    return _truthy(_env("EDMG_NVIDIA_RIVA_USE_SSL"))


def normalize_riva_uri(value: str | None = None) -> str:
    raw = str(value if value is not None else riva_base_url()).strip()
    if not raw:
        return ""
    if "://" not in raw:
        return raw.strip("/")
    parsed = urlparse(raw)
    return parsed.netloc or parsed.path.strip("/")


def riva_asr_status() -> dict[str, Any]:
    base_url = riva_base_url()
    return {
        "enabled": riva_asr_enabled(),
        "configured": bool(base_url),
        "base_url": base_url,
        "uri": normalize_riva_uri(base_url),
        "language_code": riva_language_code(),
        "model": riva_model_name(),
        "ssl": riva_use_ssl(),
        "fallback_enabled": riva_fallback_enabled(),
    }


def _load_riva_client():
    try:
        import riva.client as riva_client  # type: ignore
    except Exception as exc:
        raise RivaTranscriptionError(
            "NVIDIA Riva Python client is not installed; install the Riva client package in the backend environment."
        ) from exc
    return riva_client


def _best_alternative(result: Any) -> Any | None:
    alternatives = getattr(result, "alternatives", None)
    if alternatives:
        try:
            return alternatives[0]
        except Exception:
            return None
    return None


def _response_text_and_segments(response: Any) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    segments: list[dict[str, Any]] = []
    for index, result in enumerate(list(getattr(response, "results", []) or [])):
        alternative = _best_alternative(result)
        transcript = str(getattr(alternative, "transcript", "") or "").strip() if alternative is not None else ""
        if not transcript:
            continue
        text_parts.append(transcript)
        segments.append({"start": 0.0, "end": 0.0, "text": transcript, "index": index})
    return " ".join(text_parts).strip(), segments


def transcribe_with_riva(
    audio_path: str | Path,
    *,
    language_code: str | None = None,
    require_enabled: bool = True,
) -> dict[str, Any]:
    status = riva_asr_status()
    if require_enabled and not status["enabled"]:
        raise RivaTranscriptionError("NVIDIA Riva ASR is not enabled.")
    if not status["configured"]:
        raise RivaTranscriptionError("NVIDIA Riva endpoint is not configured.")
    uri = str(status["uri"] or "").strip()
    if not uri:
        raise RivaTranscriptionError("NVIDIA Riva endpoint did not include a host:port URI.")

    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        raise RivaTranscriptionError(f"Audio file not found: {path}")

    riva_client = _load_riva_client()
    try:
        auth = riva_client.Auth(uri=uri, use_ssl=bool(status["ssl"]))
        asr_service = riva_client.ASRService(auth)
        config = riva_client.RecognitionConfig()
        config.language_code = language_code or str(status["language_code"] or "en-US")
        config.max_alternatives = 1
        config.enable_automatic_punctuation = True
        config.audio_channel_count = 1
        if status["model"]:
            try:
                config.model = status["model"]
            except Exception:
                pass
        response = asr_service.offline_recognize(path.read_bytes(), config)
    except Exception as exc:
        raise RivaTranscriptionError(f"NVIDIA Riva ASR request failed: {exc}") from exc

    text, segments = _response_text_and_segments(response)
    return {
        "text": text,
        "segments": segments,
        "language": language_code or str(status["language_code"] or "en-US"),
        "segment_count": len(segments),
        "word_count": len([part for part in text.split() if part.strip()]),
        "source": "nvidia_riva",
        "model": status["model"] or None,
        "endpoint": uri,
    }
