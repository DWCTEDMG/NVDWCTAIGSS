from __future__ import annotations

import json
from typing import Any

import numpy as np
import triton_python_backend_utils as pb_utils


ENERGY_KEYWORDS = {
    "ambient": 0.18,
    "calm": 0.2,
    "minimal": 0.24,
    "intro": 0.32,
    "pulse": 0.52,
    "groove": 0.58,
    "build": 0.68,
    "drop": 0.92,
    "strobe": 0.95,
    "laser": 0.82,
    "explosion": 0.9,
    "climax": 0.96,
    "finale": 0.88,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _decode_string(value: Any) -> str:
    if isinstance(value, np.bytes_):
        value = bytes(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value or "")


def _walk_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_walk_text(item))
        return parts
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(_walk_text(item))
        return parts
    return []


def _scene_duration(scene: dict[str, Any]) -> float:
    duration = _number(scene.get("duration_s") or scene.get("duration_seconds") or scene.get("duration"), 0.0)
    if duration > 0:
        return duration
    start = _number(scene.get("start_s") or scene.get("start"), 0.0)
    end = _number(scene.get("end_s") or scene.get("end"), 0.0)
    return max(0.0, end - start)


def _scene_energy(scene: dict[str, Any], bpm: float) -> tuple[float, list[str]]:
    explicit = _number(scene.get("energy") or scene.get("intensity") or scene.get("motion_intensity"), -1.0)
    text = " ".join(_walk_text(scene)).lower()
    tags = sorted({word for word in ENERGY_KEYWORDS if word in text})
    keyword_score = max((ENERGY_KEYWORDS[word] for word in tags), default=0.0)
    bpm_score = _clamp((bpm - 70.0) / 110.0) if bpm > 0 else 0.0
    base = max(keyword_score, 0.28 + (0.32 * bpm_score))
    if explicit >= 0:
        base = max(base, explicit / 100.0 if explicit > 1.0 else explicit)
    return _clamp(base), tags


def _features(scene_plan: dict[str, Any]) -> dict[str, Any]:
    scenes = scene_plan.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
    bpm = _number(scene_plan.get("bpm") or scene_plan.get("tempo_bpm"), 0.0)
    shot_features: list[dict[str, Any]] = []
    total_duration = 0.0
    all_tags: set[str] = set()

    for index, raw_scene in enumerate(scenes):
        scene = raw_scene if isinstance(raw_scene, dict) else {"description": str(raw_scene)}
        duration = _scene_duration(scene)
        total_duration += duration
        energy, tags = _scene_energy(scene, bpm)
        all_tags.update(tags)
        shot_features.append(
            {
                "index": index,
                "id": str(scene.get("id") or scene.get("scene_id") or f"scene_{index + 1:03d}"),
                "title": str(scene.get("title") or scene.get("name") or f"Scene {index + 1}"),
                "duration_s": round(duration, 3),
                "energy_score": round(energy, 4),
                "tags": tags,
            }
        )

    scene_count = len(shot_features)
    if total_duration <= 0:
        total_duration = _number(scene_plan.get("duration_s") or scene_plan.get("duration_seconds"), 0.0)
    average_scene_s = total_duration / scene_count if scene_count else 0.0
    aggregate_energy = (
        sum(float(scene["energy_score"]) for scene in shot_features) / scene_count
        if scene_count
        else _clamp(0.28 + (0.32 * _clamp((bpm - 70.0) / 110.0)) if bpm > 0 else 0.25)
    )
    max_scene = max(shot_features, key=lambda item: float(item["energy_score"]), default={})
    render_tier = "quality"
    if aggregate_energy >= 0.72 or scene_count >= 8:
        render_tier = "performance"
    elif aggregate_energy <= 0.35 and total_duration <= 45:
        render_tier = "hero"

    return {
        "schema_version": "edmg.triton.shot_features.v1",
        "title": str(scene_plan.get("title") or scene_plan.get("name") or "EDMG NVIDIA Scene Plan"),
        "bpm": bpm,
        "scene_count": scene_count,
        "duration_s": round(total_duration, 3),
        "average_scene_s": round(average_scene_s, 3),
        "energy_score": round(aggregate_energy, 4),
        "peak_scene_id": max_scene.get("id", ""),
        "tags": sorted(all_tags),
        "render_hints": {
            "tier": render_tier,
            "target_fps": 24 if render_tier == "hero" else 30,
            "path_tracing": render_tier in {"hero", "quality"},
            "motion_blur": aggregate_energy >= 0.45,
        },
        "shots": shot_features,
    }


class TritonPythonModel:
    def execute(self, requests):
        responses = []
        for request in requests:
            scene_plan = pb_utils.get_input_tensor_by_name(request, "scene_plan")
            if scene_plan is None:
                error = pb_utils.TritonError("Missing required input tensor: scene_plan")
                responses.append(pb_utils.InferenceResponse(error=error))
                continue

            values = scene_plan.as_numpy().reshape(-1)
            if values.size != 1:
                error = pb_utils.TritonError("scene_plan must contain exactly one JSON string")
                responses.append(pb_utils.InferenceResponse(error=error))
                continue

            try:
                payload = json.loads(_decode_string(values[0]))
            except Exception as exc:
                error = pb_utils.TritonError(f"scene_plan JSON is invalid: {exc}")
                responses.append(pb_utils.InferenceResponse(error=error))
                continue
            if not isinstance(payload, dict):
                error = pb_utils.TritonError("scene_plan JSON must be an object")
                responses.append(pb_utils.InferenceResponse(error=error))
                continue

            features = _features(payload)
            output_json = json.dumps(features, ensure_ascii=False, separators=(",", ":"))
            output_tensors = [
                pb_utils.Tensor("features_json", np.asarray([output_json], dtype=object)),
                pb_utils.Tensor("energy_score", np.asarray([features["energy_score"]], dtype=np.float32)),
                pb_utils.Tensor("scene_count", np.asarray([features["scene_count"]], dtype=np.int32)),
            ]
            responses.append(pb_utils.InferenceResponse(output_tensors=output_tensors))
        return responses
