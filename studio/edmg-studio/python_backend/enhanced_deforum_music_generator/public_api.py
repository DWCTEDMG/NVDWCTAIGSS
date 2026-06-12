"""Lightweight public API.

This module provides a fast-importing surface for:
  - DeforumMusicGenerator
  - AudioAnalysis

It deliberately avoids importing the full standalone implementation at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .deforum_defaults import deep_merge_dicts, make_deforum_settings_template


@dataclass
class AudioAnalysis:
    """Minimal analysis container used by tests and lightweight call-sites.

    The full standalone pipeline can populate richer fields, but this class stays
    import-cheap and dependency-light.
    """

    filepath: str = ""
    duration: float = 0.0
    tempo_bpm: float = 0.0

    # Canonical fields used across the codebase
    beats: List[float] = field(default_factory=list)
    energy: List[float] = field(default_factory=list)

    # Back-compat aliases seen in older call-sites / earlier prototypes
    beat_frames: List[float] = field(default_factory=list, repr=False)
    energy_segments: List[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        # Coerce legacy names into the canonical ones when the new ones are empty.
        if (not self.beats) and self.beat_frames:
            self.beats = list(self.beat_frames)
        if (not self.energy) and self.energy_segments:
            self.energy = list(self.energy_segments)


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _normalize_user_overrides(user_settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Map common synonyms into Deforum schema keys."""
    overrides: Dict[str, Any] = {}

    if "W" in user_settings or "width" in user_settings:
        overrides["W"] = _coerce_int(user_settings.get("W", user_settings.get("width")), 1280)

    if "H" in user_settings or "height" in user_settings:
        overrides["H"] = _coerce_int(user_settings.get("H", user_settings.get("height")), 720)

    if "fps" in user_settings:
        overrides["fps"] = _coerce_int(user_settings.get("fps"), 30)

    if "steps" in user_settings:
        overrides["steps"] = _coerce_int(user_settings.get("steps"), 30)

    if "seed" in user_settings:
        overrides["seed"] = _coerce_int(user_settings.get("seed"), -1)

    if "sampler" in user_settings:
        overrides["sampler"] = str(user_settings.get("sampler"))

    if "scale" in user_settings or "cfg_scale" in user_settings:
        overrides["scale"] = _coerce_float(user_settings.get("scale", user_settings.get("cfg_scale")), 7.0)

    # Convenience schedule overrides (pass-through if provided)
    for k in (
        "strength_schedule",
        "zoom",
        "angle",
        "translation_x",
        "translation_y",
        "translation_z",
        "rotation_3d_x",
        "rotation_3d_y",
        "rotation_3d_z",
        "noise_schedule",
        "contrast_schedule",
        "cfg_scale_schedule",
    ):
        if k in user_settings:
            overrides[k] = user_settings[k]

    # Prompts / negatives
    if "negative_prompt" in user_settings and "negative_prompts" not in user_settings:
        overrides["negative_prompts"] = {"0": str(user_settings["negative_prompt"])}

    if "negative_prompts" in user_settings:
        overrides["negative_prompts"] = user_settings["negative_prompts"]

    return overrides


class DeforumMusicGenerator:
    """Lightweight generator that always returns a full Deforum settings dict."""

    def build_deforum_settings(self, analysis: AudioAnalysis, user_settings: Dict[str, Any]) -> Dict[str, Any]:
        base_prompt = str(user_settings.get("base_prompt", "")).strip()
        style_prompt = str(user_settings.get("style_prompt", "")).strip()
        combined = " ".join([p for p in [base_prompt, style_prompt] if p]).strip() or "cinematic"

        # Start from full template (all Deforum keys)
        template = make_deforum_settings_template()

        # Apply normalized user overrides (W/H/fps/etc)
        overrides = _normalize_user_overrides(user_settings)
        settings = deep_merge_dicts(template, overrides)

        # Prompts keyed by frame index (Deforum expects string keys)
        settings["prompts"] = {"0": combined}

        # Optional: AI prompt orchestration (kept off by default)
        if bool(user_settings.get("ai_orchestrate") or user_settings.get("enable_prompt_orchestration") or user_settings.get("prompt_orchestrate")):
            try:
                from .core.prompt_orchestrator import PromptOrchestrator
                provider = None
                if bool(user_settings.get("ai_use_provider")):
                    from .core.ai_providers import AIProviderConfig, build_ai_provider
                    cfg = AIProviderConfig(
                        provider=str(user_settings.get("ai_provider") or "nvidia_nim"),
                        model=str(
                            user_settings.get("ai_model")
                            or os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL")
                            or "nvidia/llama-3.3-nemotron-super-49b-v1.5"
                        ),
                        base_url=(
                            user_settings.get("ai_base_url")
                            or os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL")
                            or os.getenv("EDMG_NVIDIA_NIM_URL")
                            or "http://127.0.0.1:8001/v1"
                        ),
                        api_key=user_settings.get("ai_api_key") or None,
                    )
                    provider = build_ai_provider(cfg)
                orch = PromptOrchestrator(provider=provider)
                out = orch.orchestrate(
                    analysis,
                    base_prompt=base_prompt,
                    style_prompt=style_prompt,
                    negative_prompt=str(user_settings.get("negative_prompt") or ""),
                    use_ai=bool(user_settings.get("ai_use_provider") and provider is not None),
                )
                prompts = out.get("prompts") or {"0": combined}
                settings["prompts"] = {str(k): str(v) for k, v in prompts.items()}
                negs = out.get("negative_prompts") or {}
                if negs:
                    settings["negative_prompts"] = {str(k): str(v) for k, v in negs.items()}
                settings["_edmg_scene_plan"] = out.get("scene_plan") or []
            except Exception:
                pass

        # Optional: motion orchestration -> Deforum schedules
        if bool(user_settings.get("motion_orchestrate") or user_settings.get("enable_motion_orchestration") or user_settings.get("audio_reactive_motion")):
            try:
                from .core.motion_orchestrator import MotionConfig, motion_schedules
                mc = MotionConfig(fps=int(settings.get("fps", 24)))
                schedules = motion_schedules(analysis, cfg=mc)
                for k, v in schedules.items():
                    if v:
                        settings[k] = v
                settings["_edmg_motion_enabled"] = True
            except Exception:
                pass


        # Optional: derive max_frames from analysis.duration if provided
        try:
            dur = float(getattr(analysis, "duration", 0.0) or 0.0)
            fps = int(settings.get("fps", 24))
            if dur > 0 and fps > 0:
                settings["max_frames"] = int(dur * fps)
        except Exception:
            pass

        return settings
