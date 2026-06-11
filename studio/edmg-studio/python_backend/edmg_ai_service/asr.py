from __future__ import annotations

from functools import lru_cache
from typing import Any

NO_SPEECH_AFTER_VAD_NOTE = "No speech detected after VAD."
DEFAULT_MODEL_SIZE = "turbo"
DEFAULT_MODEL_FALLBACK_CHAIN = ("turbo", "large-v3", "medium", "small")


@lru_cache(maxsize=4)
def _load_model(model_size: str):
    raise RuntimeError(
        "Local faster-whisper ASR is disabled by the NVIDIA-only profile. "
        "Use NVIDIA Riva ASR through the NVIDIA runtime profile."
    )


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _fallback_model_candidates(model_size: str) -> list[str]:
    preferred = str(model_size or DEFAULT_MODEL_SIZE).strip().lower() or DEFAULT_MODEL_SIZE
    ordered: list[str] = []
    if preferred and preferred not in ordered:
        ordered.append(preferred)
    for candidate in DEFAULT_MODEL_FALLBACK_CHAIN:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _transcribe_once(path: str, model_size: str, *, vad_filter: bool) -> dict[str, Any]:
    model = _load_model(str(model_size or "small"))
    segments_iter, info = model.transcribe(
        path,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        without_timestamps=False,
        vad_filter=vad_filter,
        no_speech_threshold=0.7,
    )
    raw_segments = list(segments_iter)
    duration_s = _coerce_float(getattr(info, "duration", 0.0) or 0.0)
    duration_after_vad = _coerce_float(
        getattr(info, "duration_after_vad", duration_s if not vad_filter else 0.0)
        or (duration_s if not vad_filter else 0.0)
    )
    result = {
        "text": "",
        "segments": [],
        "language": str(getattr(info, "language", "") or ""),
        "duration_s": duration_s,
        "duration_after_vad_s": duration_after_vad,
        "segment_count": 0,
        "word_count": 0,
        "model_size": str(model_size or "small"),
        "source": "faster_whisper",
    }
    if not raw_segments or (vad_filter and duration_after_vad <= 0.0):
        return result

    lines: list[str] = []
    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        no_speech_prob = getattr(seg, "no_speech_prob", None)
        avg_logprob = getattr(seg, "avg_logprob", None)
        if isinstance(no_speech_prob, (int, float)) and float(no_speech_prob) >= 0.8 and len(text) < 24:
            continue
        if isinstance(avg_logprob, (int, float)) and float(avg_logprob) <= -1.2 and len(text) < 24:
            continue
        lines.append(text)
        segments.append(
            {
                "start": _coerce_float(getattr(seg, "start", 0.0) or 0.0),
                "end": _coerce_float(getattr(seg, "end", 0.0) or 0.0),
                "text": text,
                "avg_logprob": _coerce_float(avg_logprob, 0.0) if isinstance(avg_logprob, (int, float)) else None,
                "no_speech_prob": _coerce_float(no_speech_prob, 0.0) if isinstance(no_speech_prob, (int, float)) else None,
            }
        )

    text = "\n".join(lines).strip()
    result["text"] = text
    result["segments"] = segments
    result["segment_count"] = len(segments)
    result["word_count"] = len(text.split())
    return result


def transcribe_detailed(path: str, model_size: str = DEFAULT_MODEL_SIZE) -> dict[str, Any]:
    """Transcribe audio with long-form metadata and timestamped segments."""
    candidates = _fallback_model_candidates(model_size)
    last_error: Exception | None = None
    last_result: dict[str, Any] | None = None
    last_successful_model: str | None = None

    for candidate in candidates:
        try:
            attempt = _transcribe_once(path, candidate, vad_filter=True)
        except Exception as exc:
            last_error = exc
            continue
        last_result = attempt
        last_successful_model = candidate
        if attempt.get("text") or attempt.get("segments"):
            return attempt

    if last_successful_model is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Whisper models could be loaded for transcription.")

    if _coerce_float((last_result or {}).get("duration_after_vad_s"), 0.0) <= 0.0:
        without_vad = _transcribe_once(path, last_successful_model, vad_filter=False)
        if without_vad.get("text") or without_vad.get("segments"):
            return without_vad
        without_vad["note"] = NO_SPEECH_AFTER_VAD_NOTE
        return without_vad

    return last_result or {
        "text": "",
        "segments": [],
        "language": "",
        "duration_s": 0.0,
        "duration_after_vad_s": 0.0,
        "segment_count": 0,
        "word_count": 0,
        "model_size": last_successful_model,
        "source": "faster_whisper",
    }


def transcribe(path: str, model_size: str = DEFAULT_MODEL_SIZE) -> str:
    """Transcribe audio through the configured NVIDIA ASR path."""
    return str(transcribe_detailed(path, model_size=model_size).get("text") or "")
