from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STABILITY_SERVICES = ("core", "ultra", "sd3")
STABILITY_SD3_MODELS = ("sd3.5-large", "sd3.5-large-turbo", "sd3.5-medium")
STABILITY_STYLE_PRESETS = (
    "none",
    "enhance",
    "anime",
    "photographic",
    "digital-art",
    "comic-book",
    "fantasy-art",
    "line-art",
    "analog-film",
    "neon-punk",
    "isometric",
    "low-poly",
    "origami",
    "modeling-compound",
    "cinematic",
    "3d-model",
    "pixel-art",
    "tile-texture",
)

DEFAULT_RENDER_PROVIDER_SETTINGS: dict[str, Any] = {
    "stability": {
        "enabled": False,
        "allow_auto_fallback": False,
        "service": "sd3",
        "model": "sd3.5-large-turbo",
        "style_preset": "none",
        "output_format": "png",
        "strength": 0.55,
        "cfg_scale": 6.5,
    },
    "directml": {
        "enabled": False,
        "allow_auto_selection": False,
        "preferred_model": "auto",
    },
}


def _config_dir(data_dir: Path) -> Path:
    p = (data_dir / "config").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _clone_defaults() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_RENDER_PROVIDER_SETTINGS))


class RenderSettingsStore:
    def __init__(self, data_dir: Path):
        self._path = _config_dir(data_dir) / "render_providers.json"

    def get(self) -> dict[str, Any]:
        current = _read_json(self._path, default={})
        if not isinstance(current, dict):
            current = {}
        merged = _clone_defaults()
        for key, value in current.items():
            if key not in merged or not isinstance(value, dict):
                continue
            merged[key].update(value)
        return self._sanitize(merged)

    def update(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        current = self.get()
        incoming = payload if isinstance(payload, dict) else {}
        for key in ("stability", "directml"):
            value = incoming.get(key)
            if isinstance(value, dict):
                current[key].update(value)
        cleaned = self._sanitize(current)
        _write_json(self._path, cleaned)
        return cleaned

    def _sanitize(self, payload: dict[str, Any]) -> dict[str, Any]:
        out = _clone_defaults()

        stability = payload.get("stability") if isinstance(payload.get("stability"), dict) else {}
        directml = payload.get("directml") if isinstance(payload.get("directml"), dict) else {}

        service = str(stability.get("service") or out["stability"]["service"]).strip().lower()
        if service not in STABILITY_SERVICES:
            service = out["stability"]["service"]

        model = str(stability.get("model") or out["stability"]["model"]).strip().lower()
        if model not in STABILITY_SD3_MODELS:
            model = out["stability"]["model"]

        style_preset = str(stability.get("style_preset") or out["stability"]["style_preset"]).strip().lower()
        if style_preset not in STABILITY_STYLE_PRESETS:
            style_preset = out["stability"]["style_preset"]

        output_format = str(stability.get("output_format") or out["stability"]["output_format"]).strip().lower()
        if output_format not in {"png", "jpeg", "webp"}:
            output_format = out["stability"]["output_format"]

        preferred_model = str(directml.get("preferred_model") or out["directml"]["preferred_model"]).strip().lower()
        if preferred_model not in {"auto", "hf_sdxl_internal", "hf_sd15_internal"}:
            preferred_model = out["directml"]["preferred_model"]

        out["stability"] = {
            "enabled": False,
            "allow_auto_fallback": False,
            "service": service,
            "model": model,
            "style_preset": style_preset,
            "output_format": output_format,
            "strength": max(0.1, min(1.0, float(stability.get("strength", out["stability"]["strength"])))),
            "cfg_scale": max(1.0, min(10.0, float(stability.get("cfg_scale", out["stability"]["cfg_scale"])))),
        }
        out["directml"] = {
            "enabled": False,
            "allow_auto_selection": False,
            "preferred_model": preferred_model,
        }
        return out
