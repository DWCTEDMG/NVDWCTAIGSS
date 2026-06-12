from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_SCENE_FIELDS = ("id", "start_s", "end_s", "prompt")


def _as_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a number") from exc


def validate_scene_plan(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if not str(payload.get("project_id") or "").strip():
        errors.append("project_id is required")
    if not str(payload.get("title") or "").strip():
        errors.append("title is required")

    try:
        duration_s = _as_number(payload.get("duration_s"), "duration_s")
        if duration_s <= 0:
            errors.append("duration_s must be greater than 0")
    except ValueError as exc:
        duration_s = 0.0
        errors.append(str(exc))

    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("scenes must be a non-empty array")
        return errors

    previous_end = 0.0
    seen_ids: set[str] = set()
    for index, raw_scene in enumerate(scenes):
        prefix = f"scenes[{index}]"
        if not isinstance(raw_scene, dict):
            errors.append(f"{prefix} must be an object")
            continue

        for field in REQUIRED_SCENE_FIELDS:
            if raw_scene.get(field) in (None, ""):
                errors.append(f"{prefix}.{field} is required")

        scene_id = str(raw_scene.get("id") or "").strip()
        if scene_id:
            if scene_id in seen_ids:
                errors.append(f"{prefix}.id must be unique")
            seen_ids.add(scene_id)

        try:
            start_s = _as_number(raw_scene.get("start_s"), f"{prefix}.start_s")
            end_s = _as_number(raw_scene.get("end_s"), f"{prefix}.end_s")
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if start_s < 0:
            errors.append(f"{prefix}.start_s must be >= 0")
        if end_s <= start_s:
            errors.append(f"{prefix}.end_s must be greater than start_s")
        if start_s < previous_end:
            errors.append(f"{prefix}.start_s overlaps the previous scene")
        if duration_s and end_s > duration_s:
            errors.append(f"{prefix}.end_s exceeds duration_s")
        previous_end = max(previous_end, end_s)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an EDMG NVIDIA Workshop scene_plan.json file.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("scene plan root must be an object")

    errors = validate_scene_plan(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

