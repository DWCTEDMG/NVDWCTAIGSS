from __future__ import annotations

import re
from typing import Any


REQUIRED_SCENE_FIELDS = ("id", "start_s", "end_s", "prompt")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _as_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a number") from exc


def _usd_string(value: Any) -> str:
    text = str(value if value is not None else "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _usd_identifier(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char == "_":
            chars.append(char)
        elif char in ("-", " ", "."):
            chars.append("_")
    identifier = "".join(chars).strip("_")
    if not identifier:
        identifier = fallback
    if not (identifier[0].isalpha() or identifier[0] == "_"):
        identifier = f"_{identifier}"
    return identifier


def _usd_number(value: float | int | None) -> str:
    if value is None:
        return "0"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _scene_color(index: int) -> tuple[float, float, float]:
    palette = (
        (0.05, 0.62, 1.0),
        (1.0, 0.56, 0.16),
        (0.62, 0.96, 0.42),
        (0.82, 0.42, 1.0),
        (1.0, 0.22, 0.38),
        (0.28, 0.92, 0.86),
    )
    return palette[(max(1, index) - 1) % len(palette)]


def _usd_color(values: tuple[float, float, float]) -> str:
    return "(" + ", ".join(_usd_number(value) for value in values) + ")"


def _slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    return slug or fallback


def _as_optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _as_number(value, "value")


def _variant_at(plan: dict[str, Any], variant_index: int) -> dict[str, Any]:
    variants = plan.get("variants")
    if not isinstance(variants, list) or not variants:
        return {}
    index = max(0, min(int(variant_index or 0), len(variants) - 1))
    variant = variants[index]
    return variant if isinstance(variant, dict) else {}


def _scene_window(raw_scene: dict[str, Any], index: int, total: int, duration_s: float) -> tuple[float, float]:
    start = _as_optional_number(raw_scene.get("start_s"))
    end = _as_optional_number(raw_scene.get("end_s"))
    if start is not None and end is not None and end > start:
        return start, end

    total = max(1, total)
    scene_len = duration_s / float(total)
    return round(index * scene_len, 3), round(duration_s if index == total - 1 else (index + 1) * scene_len, 3)


def plan_response_to_scene_plan(
    plan: dict[str, Any],
    request_payload: dict[str, Any],
    *,
    variant_index: int = 0,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Convert the Studio/AI planner response into the NVIDIA USD scene-plan contract."""

    if not isinstance(plan, dict):
        plan = {}
    if not isinstance(request_payload, dict):
        request_payload = {}

    variant = _variant_at(plan, variant_index)
    title = str(request_payload.get("title") or plan.get("title") or variant.get("name") or "EDMG NVIDIA Scene Plan").strip()
    duration_s = (
        _as_optional_number(request_payload.get("duration_s"))
        or _as_optional_number(plan.get("duration_s"))
        or _as_optional_number(variant.get("duration_s"))
        or 60.0
    )
    bpm = _as_optional_number(request_payload.get("bpm"))
    provider = str(plan.get("provider") or "configured-ai").strip()
    model = str(plan.get("model") or "").strip()
    plan_provider = f"{provider}:{model}" if model else provider

    raw_scenes = variant.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raw_scenes = [
            {
                "start_s": 0,
                "end_s": duration_s,
                "prompt": str(request_payload.get("user_notes") or request_payload.get("style_prefs") or title).strip(),
                "camera": "wide establishing move",
                "motion": "audio-reactive light pulse",
            }
        ]

    palette = variant.get("color_palette") if isinstance(variant.get("color_palette"), list) else []
    mood = str(variant.get("mood") or "").strip()
    variant_slug = _slug(variant.get("name") or f"variant-{variant_index + 1}", f"variant-{variant_index + 1}")

    scenes: list[dict[str, Any]] = []
    for index, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            continue
        start_s, end_s = _scene_window(raw_scene, index, len(raw_scenes), duration_s)
        prompt = str(raw_scene.get("prompt") or raw_scene.get("notes") or title).strip()
        scene_slug = _slug(raw_scene.get("id") or raw_scene.get("name") or f"scene-{index + 1}", f"scene-{index + 1}")
        look_parts = [mood, ", ".join(str(item) for item in palette[:4] if str(item).strip())]
        scenes.append(
            {
                "id": scene_slug,
                "start_s": start_s,
                "end_s": end_s,
                "prompt": prompt,
                "camera": str(raw_scene.get("camera") or "").strip(),
                "look": " | ".join(part for part in look_parts if part),
                "motion": str(raw_scene.get("motion") or raw_scene.get("notes") or "").strip(),
                "usd_variant": _slug(raw_scene.get("usd_variant") or f"{variant_slug}-{scene_slug}", f"{variant_slug}-{index + 1}"),
            }
        )

    scene_plan = {
        "project_id": project_id or _slug(request_payload.get("project_id") or title, "nvidia-scene-plan"),
        "title": title,
        "duration_s": duration_s,
        "bpm": bpm,
        "provider": f"nvidia-profile:{plan_provider}",
        "scenes": scenes,
    }

    errors = validate_scene_plan(scene_plan)
    if errors:
        raise ValueError("; ".join(errors))
    return scene_plan


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


def normalize_scene_plan(payload: dict[str, Any]) -> dict[str, Any]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        scenes = []

    normalized_scenes: list[dict[str, Any]] = []
    for raw_scene in scenes:
        if not isinstance(raw_scene, dict):
            continue
        normalized_scenes.append(
            {
                "id": str(raw_scene.get("id") or "").strip(),
                "start_s": _as_number(raw_scene.get("start_s"), "scene.start_s"),
                "end_s": _as_number(raw_scene.get("end_s"), "scene.end_s"),
                "prompt": str(raw_scene.get("prompt") or "").strip(),
                "camera": str(raw_scene.get("camera") or "").strip(),
                "look": str(raw_scene.get("look") or "").strip(),
                "motion": str(raw_scene.get("motion") or "").strip(),
                "usd_variant": str(raw_scene.get("usd_variant") or "").strip(),
            }
        )

    return {
        "project_id": str(payload.get("project_id") or "").strip(),
        "title": str(payload.get("title") or "").strip(),
        "duration_s": _as_number(payload.get("duration_s"), "duration_s"),
        "bpm": _as_number(payload.get("bpm"), "bpm") if payload.get("bpm") not in (None, "") else None,
        "provider": str(payload.get("provider") or "").strip(),
        "scenes": normalized_scenes,
    }


def scene_plan_usd_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_scene_plan(payload)
    return {
        "edmg:projectId": normalized["project_id"],
        "edmg:title": normalized["title"],
        "edmg:durationSeconds": normalized["duration_s"],
        "edmg:bpm": normalized["bpm"],
        "edmg:provider": normalized["provider"],
        "edmg:sceneCount": len(normalized["scenes"]),
    }


def scene_plan_usda_text(payload: dict[str, Any]) -> str:
    normalized = normalize_scene_plan(payload)
    project_id = normalized["project_id"]
    title = normalized["title"]
    duration_s = float(normalized["duration_s"])
    bpm = normalized["bpm"]
    provider = normalized["provider"]
    end_frame = max(1, int(round(duration_s * 24.0)))

    lines = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "EDMGScenePlan"',
        "    metersPerUnit = 1",
        "    timeCodesPerSecond = 24",
        "    startTimeCode = 0",
        f"    endTimeCode = {end_frame}",
        ")",
        "",
        'def Xform "EDMGScenePlan"',
        "{",
        f"    custom string edmg:projectId = {_usd_string(project_id)}",
        f"    custom string edmg:title = {_usd_string(title)}",
        f"    custom double edmg:durationSeconds = {_usd_number(duration_s)}",
        f"    custom double edmg:bpm = {_usd_number(bpm)}",
        f"    custom string edmg:provider = {_usd_string(provider)}",
        f"    custom int edmg:sceneCount = {len(normalized['scenes'])}",
        "",
        '    def Scope "Cameras"',
        "    {",
        '        def Camera "MainCamera"',
        "        {",
        "            double3 xformOp:translate = (0, 3.2, 10.5)",
        "            double3 xformOp:rotateXYZ = (-16, 0, 0)",
        '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]',
        "            float focalLength = 35",
        "            float horizontalAperture = 20.955",
        "        }",
        "    }",
        "",
        '    def Scope "Lighting"',
        "    {",
        '        def DistantLight "KeyLight"',
        "        {",
        "            double3 xformOp:rotateXYZ = (-45, 25, 0)",
        '            uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]',
        "            float intensity = 850",
        "            color3f color = (0.86, 0.92, 1)",
        "        }",
        '        def DistantLight "RimLight"',
        "        {",
        "            double3 xformOp:rotateXYZ = (-20, -45, 0)",
        '            uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]',
        "            float intensity = 420",
        "            color3f color = (1, 0.64, 0.32)",
        "        }",
        "    }",
        "",
        '    def Scope "Looks"',
        "    {",
        '        def Material "FloorMaterial"',
        "        {",
        '            token outputs:surface.connect = </EDMGScenePlan/Looks/FloorMaterial/PreviewSurface.outputs:surface>',
        '            def Shader "PreviewSurface"',
        "            {",
        '                uniform token info:id = "UsdPreviewSurface"',
        "                color3f inputs:diffuseColor = (0.015, 0.017, 0.024)",
        "                float inputs:roughness = 0.72",
        "                token outputs:surface",
        "            }",
        "        }",
    ]

    for index, _scene in enumerate(normalized["scenes"], start=1):
        color = _scene_color(index)
        lines.extend(
            [
                f'        def Material "SceneMaterial_{index:03d}"',
                "        {",
                f'            token outputs:surface.connect = </EDMGScenePlan/Looks/SceneMaterial_{index:03d}/PreviewSurface.outputs:surface>',
                '            def Shader "PreviewSurface"',
                "            {",
                '                uniform token info:id = "UsdPreviewSurface"',
                f"                color3f inputs:diffuseColor = {_usd_color(color)}",
                "                float inputs:roughness = 0.38",
                "                float inputs:metallic = 0.06",
                "                token outputs:surface",
                "            }",
                "        }",
            ]
        )

    lines.extend(
        [
            "    }",
            "",
            '    def Xform "PreviewRig"',
            "    {",
            '        def Cube "Floor"',
            "        {",
            "            rel material:binding = </EDMGScenePlan/Looks/FloorMaterial>",
            "            double3 xformOp:translate = (0, -0.06, 0)",
            "            double3 xformOp:scale = (7.4, 0.05, 4.2)",
            '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
            "        }",
        ]
    )

    scene_count = max(1, len(normalized["scenes"]))
    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        x_offset = (index - (scene_count + 1) / 2.0) * 1.15
        y_offset = 1.0 + (0.22 if index % 2 == 0 else 0.0)
        z_offset = -0.16 * (index % 3)
        scene_duration = float(scene.get("end_s") or 0) - float(scene.get("start_s") or 0)
        scale_y = max(0.8, min(1.8, scene_duration / max(duration_s, 1.0) * 6.0))
        lines.extend(
            [
                f'        def Cube "Preview_{prim_name}"',
                "        {",
                f"            rel material:binding = </EDMGScenePlan/Looks/SceneMaterial_{index:03d}>",
                f"            double3 xformOp:translate = ({_usd_number(x_offset)}, {_usd_number(y_offset)}, {_usd_number(z_offset)})",
                f"            double3 xformOp:scale = (0.46, {_usd_number(scale_y)}, 0.05)",
                '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                f"            custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                "        }",
            ]
        )

    lines.extend(
        [
            "    }",
            "",
            '    def Scope "Scenes"',
            "    {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        lines.extend(
            [
                f'        def Xform "{prim_name}"',
                "        {",
                f"            custom string edmg:id = {_usd_string(scene.get('id'))}",
                f"            custom double edmg:startSeconds = {_usd_number(scene.get('start_s'))}",
                f"            custom double edmg:endSeconds = {_usd_number(scene.get('end_s'))}",
                f"            custom string edmg:prompt = {_usd_string(scene.get('prompt'))}",
                f"            custom string edmg:camera = {_usd_string(scene.get('camera'))}",
                f"            custom string edmg:look = {_usd_string(scene.get('look'))}",
                f"            custom string edmg:motion = {_usd_string(scene.get('motion'))}",
                f"            custom string edmg:variant = {_usd_string(scene.get('usd_variant'))}",
                "        }",
            ]
        )

    lines.extend(["    }", "}"])
    return "\n".join(lines) + "\n"
