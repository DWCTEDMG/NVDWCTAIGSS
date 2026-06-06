from __future__ import annotations

import math
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


def _usd_asset_path(value: Any) -> str:
    text = str(value if value is not None else "").strip().replace("\\", "/")
    return "@" + text.replace("@", "%40").replace("\n", "") + "@"


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


def _usd_vec3_array(values: list[tuple[float, float, float]]) -> str:
    return "[" + ", ".join(_usd_color(value) for value in values) + "]"


def _usd_vec2_array(values: list[tuple[float, float]]) -> str:
    return "[" + ", ".join(f"({_usd_number(value[0])}, {_usd_number(value[1])})" for value in values) + "]"


def _wave_ribbon_mesh(
    *,
    width: float,
    height: float,
    depth: float,
    thickness: float,
    phase: float,
    segments: int = 18,
) -> tuple[list[int], list[int], list[tuple[float, float, float]], list[tuple[float, float]]]:
    points: list[tuple[float, float, float]] = []
    st: list[tuple[float, float]] = []
    for step in range(max(2, segments) + 1):
        t = step / float(max(2, segments))
        x = (t - 0.5) * width
        wave = math.sin((t * math.tau * 1.35) + phase)
        lift = math.cos((t * math.tau * 0.7) + phase) * 0.16
        z = depth + lift
        y = height + (wave * 0.22)
        points.append((x, y - thickness, z))
        points.append((x, y + thickness, z + 0.035))
        st.append((t, 0.0))
        st.append((t, 1.0))

    counts: list[int] = []
    indices: list[int] = []
    for step in range(max(2, segments)):
        lower_a = step * 2
        upper_a = lower_a + 1
        lower_b = lower_a + 2
        upper_b = lower_a + 3
        counts.append(4)
        indices.extend([lower_a, lower_b, upper_b, upper_a])
    return counts, indices, points, st


def _frame_at_seconds(seconds: Any, fps: float = 24.0) -> int:
    try:
        value = float(seconds)
    except Exception:
        value = 0.0
    return max(0, int(round(value * fps)))


def _beat_frames(duration_s: float, bpm: float | None, fps: float = 24.0, *, limit: int = 96) -> list[int]:
    if bpm is None or bpm <= 0 or duration_s <= 0:
        return []
    seconds_per_beat = 60.0 / float(bpm)
    estimated_count = max(1, int(math.floor(duration_s / seconds_per_beat)) + 1)
    stride = max(1, int(math.ceil(estimated_count / max(1, limit))))
    frames: list[int] = []
    for beat_index in range(0, estimated_count, stride):
        frame = _frame_at_seconds(beat_index * seconds_per_beat, fps)
        if frame not in frames:
            frames.append(frame)
    return frames


def _timeline_x(seconds: float, duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    return -3.4 + (max(0.0, min(seconds, duration_s)) / duration_s) * 6.8


def _dedupe_samples(samples: list[tuple[int, str]]) -> list[tuple[int, str]]:
    deduped: list[tuple[int, str]] = []
    for frame, value in samples:
        if deduped and deduped[-1][0] == frame:
            deduped[-1] = (frame, value)
        else:
            deduped.append((frame, value))
    return deduped


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _blend_color(
    base: tuple[float, float, float],
    target: tuple[float, float, float],
    amount: float,
) -> tuple[float, float, float]:
    mix = _clamp(amount, 0.0, 1.0)
    return tuple(_clamp((base[index] * (1.0 - mix)) + (target[index] * mix), 0.0, 1.0) for index in range(3))  # type: ignore[return-value]


def _scene_energy(index: int, scene: dict[str, Any], duration_s: float) -> float:
    text = " ".join(
        str(scene.get(key) or "").lower()
        for key in ("prompt", "look", "motion", "usd_variant")
    )
    accent_words = (
        "drop",
        "pulse",
        "beat",
        "rise",
        "rush",
        "storm",
        "laser",
        "haze",
        "spark",
        "fire",
        "bloom",
        "glow",
        "camera",
        "sweep",
    )
    accents = sum(1 for word in accent_words if word in text)
    start_s = float(scene.get("start_s") or 0.0)
    end_s = float(scene.get("end_s") or start_s)
    duration_ratio = _clamp((end_s - start_s) / max(1.0, duration_s) * 8.0, 0.0, 1.0)
    return _clamp(0.32 + (accents * 0.055) + ((index % 4) * 0.045) + (duration_ratio * 0.34), 0.25, 1.0)


def _scene_visibility_samples(start_frame: int, end_frame: int) -> list[tuple[int, str]]:
    hide_frame = max(start_frame + 1, end_frame + 1)
    samples: list[tuple[int, str]] = []
    if start_frame > 0:
        samples.append((0, '"invisible"'))
        samples.append((start_frame - 1, '"invisible"'))
    samples.extend([(start_frame, '"inherited"'), (hide_frame, '"invisible"')])
    return _dedupe_samples(samples)


def _scene_visual_plate(scene: dict[str, Any]) -> str:
    for key in ("visual_plate", "visual_plate_path", "generated_plate", "plate_path"):
        value = str(scene.get(key) or "").strip()
        if value:
            return value.replace("\\", "/")
    return ""


def _append_time_samples(lines: list[str], indent: str, attribute: str, samples: list[tuple[int, str]]) -> None:
    lines.append(f"{indent}{attribute}.timeSamples = {{")
    for sample_index, (frame, value) in enumerate(samples):
        suffix = "," if sample_index < len(samples) - 1 else ""
        lines.append(f"{indent}    {frame}: {value}{suffix}")
    lines.append(f"{indent}}}")


def _camera_translate_sample(index: int, total: int) -> tuple[float, float, float]:
    total = max(1, total)
    center = (total + 1) / 2.0
    x_offset = (index - center) * 0.34
    z_offset = 10.5 - min(2.4, index * 0.22)
    y_offset = 3.1 + (0.12 if index % 2 == 0 else 0.0)
    return x_offset, y_offset, z_offset


def _camera_rotate_sample(index: int, total: int) -> tuple[float, float, float]:
    total = max(1, total)
    center = (total + 1) / 2.0
    yaw = (center - index) * 1.8
    pitch = -15.5 - (0.4 if index % 2 == 0 else 0.0)
    return pitch, yaw, 0.0


def _shot_camera_translate_sample(index: int, total: int, phase: float = 0.0) -> tuple[float, float, float]:
    total = max(1, total)
    center = (total + 1) / 2.0
    x_offset = (index - center) * 0.58
    y_offset = 2.6 + (0.18 if index % 2 == 0 else 0.0)
    z_offset = 8.8 - min(1.8, index * 0.18) - (0.52 * max(0.0, min(1.0, phase)))
    return x_offset, y_offset, z_offset


def _shot_camera_rotate_sample(index: int, total: int, phase: float = 0.0) -> tuple[float, float, float]:
    total = max(1, total)
    center = (total + 1) / 2.0
    yaw = (center - index) * 2.4 + (0.9 * max(0.0, min(1.0, phase)))
    pitch = -13.0 - (0.6 if index % 2 == 0 else 0.0)
    return pitch, yaw, 0.0


def _shot_light_translate_sample(index: int, total: int) -> tuple[float, float, float]:
    total = max(1, total)
    center = (total + 1) / 2.0
    x_offset = (index - center) * 0.9
    return x_offset, 4.2, 1.8


def _slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    return slug or fallback


def _render_product_path(project_id: str, scene_id: Any, index: int) -> str:
    project_slug = _slug(project_id, "project")
    scene_slug = _slug(scene_id, f"scene-{index:03d}")
    return f"outputs/nvidia/renders/{project_slug}/{index:03d}_{scene_slug}.mp4"


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
                "visual_plate": _scene_visual_plate(raw_scene),
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
                "visual_plate": _scene_visual_plate(raw_scene),
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
        "edmg:usdGeneration": "production-immersive-plate-stage-v3",
        "edmg:renderProductCount": len(normalized["scenes"]),
    }


def scene_plan_usda_text(payload: dict[str, Any]) -> str:
    normalized = normalize_scene_plan(payload)
    project_id = normalized["project_id"]
    title = normalized["title"]
    duration_s = float(normalized["duration_s"])
    bpm = normalized["bpm"]
    provider = normalized["provider"]
    end_frame = max(1, int(round(duration_s * 24.0)))
    scene_count = max(1, len(normalized["scenes"]))
    beat_frames = _beat_frames(duration_s, bpm)
    seconds_per_beat = 60.0 / float(bpm) if bpm else None
    project_slug = _slug(project_id, "project")
    render_output_dir = f"outputs/nvidia/renders/{project_slug}"

    lines = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "EDMGScenePlan"',
        "    metersPerUnit = 1",
        '    upAxis = "Y"',
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
        '    custom string edmg:usdGeneration = "production-immersive-plate-stage-v3"',
        f"    custom string edmg:renderOutputDir = {_usd_string(render_output_dir)}",
        "",
        '    def Scope "Production"',
        "    {",
        '        def Scope "RenderSettings"',
        "        {",
        '            custom string edmg:renderer = "RTX"',
        "            custom int edmg:width = 1920",
        "            custom int edmg:height = 1080",
        "            custom double edmg:framesPerSecond = 24",
        f"            custom int edmg:startFrame = 0",
        f"            custom int edmg:endFrame = {end_frame}",
        f"            custom string edmg:defaultOutputDir = {_usd_string(render_output_dir)}",
        '            custom string edmg:primaryCamera = "/EDMGScenePlan/Cameras/MainCamera"',
        "        }",
        "",
        '        def Scope "RenderProducts"',
        "        {",
    ]

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        product_path = _render_product_path(project_id, scene.get("id"), index)
        start_frame = _frame_at_seconds(scene.get("start_s"))
        end_scene_frame = _frame_at_seconds(scene.get("end_s"))
        lines.extend(
            [
                f'            def Xform "Product_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                custom int edmg:startFrame = {start_frame}",
                f"                custom int edmg:endFrame = {end_scene_frame}",
                f"                custom string edmg:filePath = {_usd_string(product_path)}",
                f'                custom string edmg:cameraPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotCameras/Camera_{prim_name}")}',
                f'                custom string edmg:shotPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotGraph/Shot_{prim_name}")}',
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "",
            '        def Scope "AssetSlots"',
            "        {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        lines.extend(
            [
                f'            def Xform "Slot_{prim_name}"',
                "            {",
                '                custom string edmg:assetRole = "generated-set-piece"',
                '                custom string edmg:assetStatus = "pending-generation"',
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                custom string edmg:prompt = {_usd_string(scene.get('prompt'))}",
                f"                custom string edmg:look = {_usd_string(scene.get('look'))}",
                f"                custom string edmg:motion = {_usd_string(scene.get('motion'))}",
                f"                custom string edmg:visualPlate = {_usd_string(_scene_visual_plate(scene))}",
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "",
            '        def Scope "ShotCameras"',
            "        {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        start_frame = _frame_at_seconds(scene.get("start_s"))
        end_scene_frame = _frame_at_seconds(scene.get("end_s"))
        translate_samples = _dedupe_samples(
            [
                (start_frame, _usd_color(_shot_camera_translate_sample(index, scene_count, 0.0))),
                (end_scene_frame, _usd_color(_shot_camera_translate_sample(index, scene_count, 1.0))),
            ]
        )
        rotate_samples = _dedupe_samples(
            [
                (start_frame, _usd_color(_shot_camera_rotate_sample(index, scene_count, 0.0))),
                (end_scene_frame, _usd_color(_shot_camera_rotate_sample(index, scene_count, 1.0))),
            ]
        )
        lines.extend(
            [
                f'            def Camera "Camera_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                custom string edmg:cameraNotes = {_usd_string(scene.get('camera'))}",
                "                double3 xformOp:translate.timeSamples = {",
            ]
        )
        for sample_index, (frame, value) in enumerate(translate_samples):
            suffix = "," if sample_index < len(translate_samples) - 1 else ""
            lines.append(f"                    {frame}: {value}{suffix}")
        lines.extend(
            [
                "                }",
                "                double3 xformOp:rotateXYZ.timeSamples = {",
            ]
        )
        for sample_index, (frame, value) in enumerate(rotate_samples):
            suffix = "," if sample_index < len(rotate_samples) - 1 else ""
            lines.append(f"                    {frame}: {value}{suffix}")
        lines.extend(
            [
                "                }",
                '                uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]',
                "                float focalLength = 38",
                "                float horizontalAperture = 20.955",
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "",
            '        def Scope "ShotLights"',
            "        {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        color = _scene_color(index)
        key_translate = _shot_light_translate_sample(index, scene_count)
        rim_translate = (-key_translate[0], 2.9, -2.2)
        lines.extend(
            [
                f'            def SphereLight "Key_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                double3 xformOp:translate = {_usd_color(key_translate)}",
                '                uniform token[] xformOpOrder = ["xformOp:translate"]',
                f"                color3f color = {_usd_color(color)}",
                "                float intensity = 950",
                "                float radius = 1.15",
                "            }",
                f'            def SphereLight "Rim_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                double3 xformOp:translate = {_usd_color(rim_translate)}",
                '                uniform token[] xformOpOrder = ["xformOp:translate"]',
                "                color3f color = (0.98, 0.78, 0.42)",
                "                float intensity = 420",
                "                float radius = 0.72",
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "",
            '        def Scope "ShotGraph"',
            "        {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        start_frame = _frame_at_seconds(scene.get("start_s"))
        end_scene_frame = _frame_at_seconds(scene.get("end_s"))
        product_path = _render_product_path(project_id, scene.get("id"), index)
        lines.extend(
            [
                f'            def Xform "Shot_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                custom int edmg:shotIndex = {index}",
                f"                custom int edmg:startFrame = {start_frame}",
                f"                custom int edmg:endFrame = {end_scene_frame}",
                f"                custom double edmg:startSeconds = {_usd_number(scene.get('start_s'))}",
                f"                custom double edmg:endSeconds = {_usd_number(scene.get('end_s'))}",
                f"                custom string edmg:prompt = {_usd_string(scene.get('prompt'))}",
                f"                custom string edmg:look = {_usd_string(scene.get('look'))}",
                f"                custom string edmg:motion = {_usd_string(scene.get('motion'))}",
                f"                custom string edmg:renderProduct = {_usd_string(product_path)}",
                f'                custom string edmg:cameraPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotCameras/Camera_{prim_name}")}',
                f'                custom string edmg:keyLightPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotLights/Key_{prim_name}")}',
                f'                custom string edmg:assetSlotPrim = {_usd_string(f"/EDMGScenePlan/Production/AssetSlots/Slot_{prim_name}")}',
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "    }",
            "",
            '    def Scope "AudioTimeline"',
            "    {",
            f"        custom double edmg:durationSeconds = {_usd_number(duration_s)}",
            f"        custom double edmg:bpm = {_usd_number(bpm)}",
            f"        custom double edmg:secondsPerBeat = {_usd_number(seconds_per_beat)}",
            f"        custom int edmg:beatMarkerCount = {len(beat_frames)}",
            "        custom double edmg:framesPerSecond = 24",
            f"        custom int edmg:endFrame = {end_frame}",
            "    }",
            "",
            '    def Scope "Cameras"',
            "    {",
            '        def Camera "MainCamera"',
            "        {",
            "            double3 xformOp:translate = (0, 3.2, 10.5)",
            "            double3 xformOp:rotateXYZ = (-16, 0, 0)",
            "            double3 xformOp:translate.timeSamples = {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        frame = _frame_at_seconds(scene.get("start_s"))
        lines.append(f"                {frame}: {_usd_color(_camera_translate_sample(index, scene_count))},")
    lines.extend(
        [
            f"                {end_frame}: {_usd_color(_camera_translate_sample(scene_count, scene_count))}",
            "            }",
            "            double3 xformOp:rotateXYZ.timeSamples = {",
        ]
    )
    for index, scene in enumerate(normalized["scenes"], start=1):
        frame = _frame_at_seconds(scene.get("start_s"))
        lines.append(f"                {frame}: {_usd_color(_camera_rotate_sample(index, scene_count))},")
    lines.extend(
        [
            f"                {end_frame}: {_usd_color(_camera_rotate_sample(scene_count, scene_count))}",
            "            }",
        ]
    )

    lines.extend(
        [
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
            '        def Material "BeatMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/BeatMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (0.78, 0.9, 1)",
            "                color3f inputs:emissiveColor = (0.18, 0.5, 1)",
            "                float inputs:roughness = 0.28",
            "                token outputs:surface",
            "            }",
            "        }",
            '        def Material "CoreGlowMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/CoreGlowMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (0.18, 0.72, 1)",
            "                color3f inputs:emissiveColor = (0.12, 0.48, 0.9)",
            "                float inputs:roughness = 0.18",
            "                float inputs:metallic = 0.04",
            "                token outputs:surface",
            "            }",
            "        }",
            '        def Material "RibbonMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/RibbonMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (0.9, 0.6, 0.22)",
            "                color3f inputs:emissiveColor = (0.42, 0.18, 0.04)",
            "                float inputs:roughness = 0.24",
            "                float inputs:metallic = 0.28",
            "                token outputs:surface",
            "            }",
            "        }",
            '        def Material "BeamMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/BeamMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (0.54, 0.92, 0.86)",
            "                color3f inputs:emissiveColor = (0.16, 0.42, 0.36)",
            "                float inputs:roughness = 0.32",
            "                token outputs:surface",
            "            }",
            "        }",
            '        def Material "ParticleMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/ParticleMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (1, 0.92, 0.62)",
            "                color3f inputs:emissiveColor = (0.8, 0.52, 0.18)",
            "                float inputs:roughness = 0.16",
            "                token outputs:surface",
            "            }",
            "        }",
            '        def Material "GreenGlassMaterial"',
            "        {",
            '            token outputs:surface.connect = </EDMGScenePlan/Looks/GreenGlassMaterial/PreviewSurface.outputs:surface>',
            '            def Shader "PreviewSurface"',
            "            {",
            '                uniform token info:id = "UsdPreviewSurface"',
            "                color3f inputs:diffuseColor = (0.3, 0.96, 0.5)",
            "                color3f inputs:emissiveColor = (0.08, 0.38, 0.1)",
            "                float inputs:roughness = 0.2",
            "                float inputs:metallic = 0.12",
            "                token outputs:surface",
            "            }",
            "        }",
        ]
    )

    for index, _scene in enumerate(normalized["scenes"], start=1):
        color = _scene_color(index)
        glow = _blend_color(color, (1.0, 0.92, 0.58), 0.28)
        lines.extend(
            [
                f'        def Material "SceneMaterial_{index:03d}"',
                "        {",
                f'            token outputs:surface.connect = </EDMGScenePlan/Looks/SceneMaterial_{index:03d}/PreviewSurface.outputs:surface>',
                '            def Shader "PreviewSurface"',
                "            {",
                '                uniform token info:id = "UsdPreviewSurface"',
                f"                color3f inputs:diffuseColor = {_usd_color(color)}",
                f"                color3f inputs:emissiveColor = {_usd_color(glow)}",
                "                float inputs:roughness = 0.38",
                "                float inputs:metallic = 0.06",
                "                token outputs:surface",
                "            }",
                "        }",
            ]
        )

    for index, scene in enumerate(normalized["scenes"], start=1):
        visual_plate = _scene_visual_plate(scene)
        if not visual_plate:
            continue
        lines.extend(
            [
                f'        def Material "PlateMaterial_{index:03d}"',
                "        {",
                f'            token outputs:surface.connect = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}/PreviewSurface.outputs:surface>',
                '            def Shader "PreviewSurface"',
                "            {",
                '                uniform token info:id = "UsdPreviewSurface"',
                f'                color3f inputs:diffuseColor.connect = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}/PlateTexture.outputs:rgb>',
                f'                color3f inputs:emissiveColor.connect = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}/PlateTexture.outputs:rgb>',
                "                float inputs:roughness = 0.58",
                "                float inputs:metallic = 0",
                "                token outputs:surface",
                "            }",
                '            def Shader "PlateTexture"',
                "            {",
                '                uniform token info:id = "UsdUVTexture"',
                f"                asset inputs:file = {_usd_asset_path(visual_plate)}",
                '                token inputs:sourceColorSpace = "sRGB"',
                f'                float2 inputs:st.connect = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}/PrimvarReader.outputs:result>',
                "                color3f outputs:rgb",
                "            }",
                '            def Shader "PrimvarReader"',
                "            {",
                '                uniform token info:id = "UsdPrimvarReader_float2"',
                '                token inputs:varname = "st"',
                "                float2 outputs:result",
                "            }",
                "        }",
            ]
        )

    lines.extend(
        [
            "    }",
            "",
            '    def Xform "ProceduralStage"',
            "    {",
            '        custom string edmg:generation = "immersive-plate-depth-stage-v3"',
            '        custom string edmg:generationSource = "Hush Now audio analysis, generated visual plates, BPM, scene prompts, and timeline windows"',
            '        def Cube "ReflectiveStageFloor"',
            "        {",
            "            rel material:binding = </EDMGScenePlan/Looks/FloorMaterial>",
            "            double3 xformOp:translate = (0, -0.06, 0)",
            "            double3 xformOp:scale = (8.8, 0.025, 5.4)",
            '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
            "        }",
            '        def Cylinder "LowEndHalo"',
            "        {",
            "            rel material:binding = </EDMGScenePlan/Looks/BeamMaterial>",
            "            double radius = 1",
            "            double height = 0.045",
            "            double3 xformOp:translate = (0, 0.035, -0.38)",
            "            double3 xformOp:scale.timeSamples = {",
            "                0: (1.85, 1, 1.85),",
            f"                {max(1, end_frame // 2)}: (2.35, 1, 2.05),",
            f"                {end_frame}: (1.95, 1, 2.28)",
            "            }",
            '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
            "        }",
        ]
    )

    if beat_frames:
        lines.extend(
            [
                '        def Scope "BeatGrid"',
                "        {",
            ]
        )
        for beat_index, frame in enumerate(beat_frames, start=1):
            seconds = float(frame) / 24.0
            x_offset = _timeline_x(seconds, duration_s)
            height = 0.36 if beat_index % 4 == 1 else 0.18
            radius = 0.026 if beat_index % 4 == 1 else 0.016
            lines.extend(
                [
                    f'            def Cylinder "BeatPulse_{beat_index:03d}"',
                    "            {",
                    "                rel material:binding = </EDMGScenePlan/Looks/BeatMaterial>",
                    f"                double radius = {_usd_number(radius)}",
                    f"                double height = {_usd_number(height)}",
                    f"                double3 xformOp:translate = ({_usd_number(x_offset)}, {_usd_number(height / 2.0)}, -2.08)",
                    '                uniform token[] xformOpOrder = ["xformOp:translate"]',
                    f"                custom int edmg:frame = {frame}",
                    f"                custom double edmg:seconds = {_usd_number(seconds)}",
                    "            }",
                ]
            )
        lines.extend(["        }", ""])

    visual_plate_scenes = [
        (index, scene)
        for index, scene in enumerate(normalized["scenes"], start=1)
        if _scene_visual_plate(scene)
    ]
    if visual_plate_scenes:
        lines.extend(
            [
                '        def Scope "VisualPlates"',
                "        {",
            ]
        )
        for index, scene in visual_plate_scenes:
            prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
            start_frame = _frame_at_seconds(scene.get("start_s"))
            end_scene_frame = _frame_at_seconds(scene.get("end_s"))
            mid_frame = _frame_at_seconds((float(scene.get("start_s") or 0) + float(scene.get("end_s") or 0)) / 2.0)
            visibility_samples = _scene_visibility_samples(start_frame, end_scene_frame)
            drift = 0.08 if index % 2 == 0 else -0.08
            translate_samples = _dedupe_samples(
                [
                    (start_frame, _usd_color((0.0, 2.08, -4.45))),
                    (mid_frame, _usd_color((drift, 2.12, -4.65))),
                    (end_scene_frame, _usd_color((drift * -0.6, 2.08, -4.82))),
                ]
            )
            scale_samples = _dedupe_samples(
                [
                    (start_frame, "(1.0, 1.0, 1)"),
                    (mid_frame, "(1.035, 1.035, 1)"),
                    (end_scene_frame, "(1.06, 1.06, 1)"),
                ]
            )
            lines.extend(
                [
                    f'            def Xform "Environment_{prim_name}"',
                    "            {",
                    f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                    f"                custom string edmg:visualPlate = {_usd_string(_scene_visual_plate(scene))}",
                    '                custom string edmg:environmentMode = "image-plate-depth-room"',
                ]
            )
            _append_time_samples(lines, "                ", "token visibility", visibility_samples)
            lines.extend(
                [
                    f'                def Mesh "Plate_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                    int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-5.35, -2.75, 0), (5.35, -2.75, 0), (5.35, 2.75, 0), (-5.35, 2.75, 0)]",
                    "                    texCoord2f[] primvars:st = [(0, 0), (1, 0), (1, 1), (0, 1)] (",
                    '                        interpolation = "faceVarying"',
                    "                    )",
                ]
            )
            _append_time_samples(lines, "                    ", "double3 xformOp:translate", translate_samples)
            _append_time_samples(lines, "                    ", "double3 xformOp:scale", scale_samples)
            lines.extend(
                [
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                    "                }",
                    f'                def Mesh "FloorReflection_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                    int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-5.45, 0.015, -0.45), (5.45, 0.015, -0.45), (5.45, 0.015, -5.9), (-5.45, 0.015, -5.9)]",
                    "                    texCoord2f[] primvars:st = [(0, 1), (1, 1), (1, 0.12), (0, 0.12)] (",
                    '                        interpolation = "faceVarying"',
                    "                    )",
                    "                }",
                    f'                def Mesh "LeftWall_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                    int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-2.8, -2.25, 0), (2.8, -2.25, 0), (2.8, 2.25, 0), (-2.8, 2.25, 0)]",
                    "                    texCoord2f[] primvars:st = [(0.02, 0), (0.48, 0), (0.48, 1), (0.02, 1)] (",
                    '                        interpolation = "faceVarying"',
                    "                    )",
                    "                    double3 xformOp:translate = (-4.62, 1.86, -2.95)",
                    "                    double3 xformOp:rotateXYZ = (0, 62, 0)",
                    "                    double3 xformOp:scale = (1.08, 1.08, 1)",
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                    f'                def Mesh "RightWall_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                    int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-2.8, -2.25, 0), (2.8, -2.25, 0), (2.8, 2.25, 0), (-2.8, 2.25, 0)]",
                    "                    texCoord2f[] primvars:st = [(0.52, 0), (0.98, 0), (0.98, 1), (0.52, 1)] (",
                    '                        interpolation = "faceVarying"',
                    "                    )",
                    "                    double3 xformOp:translate = (4.62, 1.86, -2.95)",
                    "                    double3 xformOp:rotateXYZ = (0, -62, 0)",
                    "                    double3 xformOp:scale = (1.08, 1.08, 1)",
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                    f'                def Mesh "DepthCard_A_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-1.8, -1.35, 0), (1.8, -1.35, 0), (1.8, 1.35, 0), (-1.8, 1.35, 0)]",
                    "                    texCoord2f[] primvars:st = [(0.18, 0.16), (0.82, 0.16), (0.82, 0.84), (0.18, 0.84)] (",
                    '                        interpolation = "faceVarying"',
                    "                )",
                    f"                    double3 xformOp:translate = ({_usd_number(-1.15 + (0.18 if index % 2 else -0.18))}, 1.55, -2.22)",
                    f"                    double3 xformOp:rotateXYZ = (0, {_usd_number(5.0 if index % 2 else -5.0)}, 0)",
                    "                    double3 xformOp:scale = (0.9, 0.9, 1)",
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                    f'                def Mesh "DepthCard_B_{prim_name}"',
                    "                {",
                    f"                    rel material:binding = </EDMGScenePlan/Looks/PlateMaterial_{index:03d}>",
                    "                    uniform bool doubleSided = 1",
                    "                    int[] faceVertexCounts = [4]",
                    "                    int[] faceVertexIndices = [0, 1, 2, 3]",
                    "                    point3f[] points = [(-1.25, -0.95, 0), (1.25, -0.95, 0), (1.25, 0.95, 0), (-1.25, 0.95, 0)]",
                    "                    texCoord2f[] primvars:st = [(0.28, 0.24), (0.72, 0.24), (0.72, 0.76), (0.28, 0.76)] (",
                    '                        interpolation = "faceVarying"',
                    "                    )",
                    f"                    double3 xformOp:translate = ({_usd_number(1.35 + (-0.16 if index % 2 else 0.16))}, 1.78, -3.18)",
                    f"                    double3 xformOp:rotateXYZ = (0, {_usd_number(-8.0 if index % 2 else 8.0)}, 0)",
                    "                    double3 xformOp:scale = (0.78, 0.78, 1)",
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                    "            }",
                ]
            )
        lines.extend(["        }", ""])

    lines.extend(
        [
            '        def Scope "GeneratedAssets"',
            "        {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        scene_duration = max(0.001, float(scene.get("end_s") or 0) - float(scene.get("start_s") or 0))
        energy = _scene_energy(index, scene, duration_s)
        anchor_x = (index - (scene_count + 1) / 2.0) * 0.95
        anchor_z = -0.18 * (index % 4)
        core_radius = 0.09 + (energy * 0.045)
        ribbon_height = 0.92 + (energy * 0.72)
        beam_height = 2.2 + (energy * 1.2)
        gate_height = 1.05 + (energy * 0.55)
        start_frame = _frame_at_seconds(scene.get("start_s"))
        end_scene_frame = _frame_at_seconds(scene.get("end_s"))
        mid_frame = _frame_at_seconds((float(scene.get("start_s") or 0) + float(scene.get("end_s") or 0)) / 2.0)
        visibility_samples = _scene_visibility_samples(start_frame, end_scene_frame)
        core_translate_samples = _dedupe_samples(
            [
                (start_frame, _usd_color((anchor_x, 0.52 + energy * 0.08, anchor_z - 0.18))),
                (mid_frame, _usd_color((anchor_x + math.sin(index) * 0.18, 0.62 + energy * 0.16, anchor_z - 0.42))),
                (end_scene_frame, _usd_color((anchor_x + math.cos(index) * 0.12, 0.54 + energy * 0.1, anchor_z - 0.64))),
            ]
        )
        core_scale_samples = _dedupe_samples(
            [
                    (start_frame, f"({_usd_number(0.78 + energy * 0.1)}, {_usd_number(0.78 + energy * 0.1)}, {_usd_number(0.78 + energy * 0.1)})"),
                    (mid_frame, f"({_usd_number(1.0 + energy * 0.16)}, {_usd_number(1.0 + energy * 0.16)}, {_usd_number(1.0 + energy * 0.16)})"),
                    (end_scene_frame, f"({_usd_number(0.84 + energy * 0.08)}, {_usd_number(0.84 + energy * 0.08)}, {_usd_number(0.84 + energy * 0.08)})"),
            ]
        )
        ribbon_scale_samples = _dedupe_samples(
            [
                    (start_frame, f"(0.8, {_usd_number(0.68 + energy * 0.1)}, 0.8)"),
                    (mid_frame, f"(0.92, {_usd_number(0.88 + energy * 0.12)}, 0.92)"),
                    (end_scene_frame, f"(0.76, {_usd_number(0.76 + energy * 0.08)}, 0.76)"),
            ]
        )
        ribbon_rotate_a = _dedupe_samples(
            [
                (start_frame, _usd_color((68.0, index * 7.0, 24.0 + index * 11.0))),
                (mid_frame, _usd_color((76.0, index * 12.0, 98.0 + index * 9.0))),
                (end_scene_frame, _usd_color((62.0, index * 16.0, 164.0 + index * 7.0))),
            ]
        )
        ribbon_rotate_b = _dedupe_samples(
            [
                (start_frame, _usd_color((104.0, index * -6.0, -38.0 - index * 8.0))),
                (mid_frame, _usd_color((92.0, index * -11.0, -112.0 - index * 7.0))),
                (end_scene_frame, _usd_color((112.0, index * -15.0, -182.0 - index * 5.0))),
            ]
        )
        lines.extend(
            [
                f'            def Xform "Generated_{prim_name}"',
                "            {",
                f"                custom string edmg:sceneId = {_usd_string(scene.get('id'))}",
                f"                custom int edmg:startFrame = {start_frame}",
                f"                custom int edmg:endFrame = {end_scene_frame}",
                f"                custom double edmg:energy = {_usd_number(energy)}",
                f"                custom double edmg:sourceDurationSeconds = {_usd_number(scene_duration)}",
            ]
        )
        _append_time_samples(lines, "                ", "token visibility", visibility_samples)
        lines.extend(
            [
                '                def Sphere "Core"',
                "                {",
                f"                    rel material:binding = </EDMGScenePlan/Looks/SceneMaterial_{index:03d}>",
                f"                    double radius = {_usd_number(core_radius)}",
            ]
        )
        _append_time_samples(lines, "                    ", "double3 xformOp:translate", core_translate_samples)
        _append_time_samples(lines, "                    ", "double3 xformOp:scale", core_scale_samples)
        lines.extend(
            [
                '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                "                }",
                '                def Sphere "VoiceBloom"',
                "                {",
                "                    rel material:binding = </EDMGScenePlan/Looks/CoreGlowMaterial>",
                f"                    double radius = {_usd_number(core_radius * 1.55)}",
            ]
        )
        _append_time_samples(lines, "                    ", "double3 xformOp:translate", core_translate_samples)
        _append_time_samples(
            lines,
            "                    ",
            "double3 xformOp:scale",
            _dedupe_samples(
                [
                    (start_frame, "(0.72, 0.72, 0.72)"),
                    (mid_frame, f"({_usd_number(1.04 + energy * 0.34)}, {_usd_number(1.04 + energy * 0.34)}, {_usd_number(1.04 + energy * 0.34)})"),
                    (end_scene_frame, "(0.82, 0.82, 0.82)"),
                ]
            ),
        )
        lines.extend(
            [
                '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                "                }",
            ]
        )

        for mesh_name, phase_offset, height_bias, depth_bias, material_path in (
            ("WaveRibbon_A", index * 0.42, 1.18, -1.12, f"</EDMGScenePlan/Looks/SceneMaterial_{index:03d}>"),
            ("WaveRibbon_B", index * 0.42 + 1.65, 1.55, -1.72, "</EDMGScenePlan/Looks/RibbonMaterial>"),
        ):
            counts, indices, points, st = _wave_ribbon_mesh(
                width=4.2 + energy * 1.4,
                height=height_bias + energy * 0.25,
                depth=depth_bias,
                thickness=0.08 + energy * 0.035,
                phase=phase_offset,
                segments=18,
            )
            mesh_translate = _dedupe_samples(
                [
                    (start_frame, _usd_color((anchor_x * 0.22, 0.0, 0.0))),
                    (mid_frame, _usd_color((anchor_x * 0.16 + math.sin(index) * 0.16, 0.06, -0.18))),
                    (end_scene_frame, _usd_color((anchor_x * 0.12 + math.cos(index) * 0.12, 0.0, -0.34))),
                ]
            )
            mesh_rotate = _dedupe_samples(
                [
                    (start_frame, _usd_color((0.0, index * 2.8, -3.0 + index * 0.8))),
                    (mid_frame, _usd_color((0.0, index * 3.6, 5.5 - index * 0.5))),
                    (end_scene_frame, _usd_color((0.0, index * 4.2, -1.0 + index * 0.7))),
                ]
            )
            mesh_scale = _dedupe_samples(
                [
                    (start_frame, "(0.92, 0.92, 0.92)"),
                    (mid_frame, f"({_usd_number(1.0 + energy * 0.12)}, {_usd_number(1.0 + energy * 0.12)}, {_usd_number(1.0 + energy * 0.12)})"),
                    (end_scene_frame, "(0.96, 0.96, 0.96)"),
                ]
            )
            lines.extend(
                [
                    f'                def Mesh "{mesh_name}"',
                    "                {",
                    f"                    rel material:binding = {material_path}",
                    "                    uniform bool doubleSided = 1",
                    f"                    int[] faceVertexCounts = [{', '.join(str(value) for value in counts)}]",
                    f"                    int[] faceVertexIndices = [{', '.join(str(value) for value in indices)}]",
                    f"                    point3f[] points = {_usd_vec3_array(points)}",
                    f"                    texCoord2f[] primvars:st = {_usd_vec2_array(st)} (",
                    '                        interpolation = "vertex"',
                    "                    )",
                ]
            )
            _append_time_samples(lines, "                    ", "double3 xformOp:translate", mesh_translate)
            _append_time_samples(lines, "                    ", "double3 xformOp:rotateXYZ", mesh_rotate)
            _append_time_samples(lines, "                    ", "double3 xformOp:scale", mesh_scale)
            lines.extend(
                [
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                ]
            )

        for ribbon_name, rotate_samples, z_bias in (
            ("OrbitRibbon_A", ribbon_rotate_a, -0.08),
            ("OrbitRibbon_B", ribbon_rotate_b, 0.18),
        ):
            lines.extend(
                [
                    f'                def Cylinder "{ribbon_name}"',
                    "                {",
                    "                    rel material:binding = </EDMGScenePlan/Looks/RibbonMaterial>",
                    "                    double radius = 0.008",
                    f"                    double height = {_usd_number(ribbon_height)}",
                    f"                    double3 xformOp:translate = ({_usd_number(anchor_x)}, {_usd_number(1.05 + energy * 0.22)}, {_usd_number(anchor_z + z_bias)})",
                ]
            )
            _append_time_samples(lines, "                    ", "double3 xformOp:rotateXYZ", rotate_samples)
            _append_time_samples(lines, "                    ", "double3 xformOp:scale", ribbon_scale_samples)
            lines.extend(
                [
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                ]
            )

        for beam_name, x_bias, rot_z in (("LeftBeam", -0.32, -19.0), ("RightBeam", 0.32, 19.0)):
            beam_scale = _dedupe_samples(
                [
                    (start_frame, "(0.82, 0.82, 0.82)"),
                    (mid_frame, f"({_usd_number(1.0 + energy * 0.18)}, {_usd_number(1.0 + energy * 0.18)}, {_usd_number(1.0 + energy * 0.18)})"),
                    (end_scene_frame, "(0.9, 0.9, 0.9)"),
                ]
            )
            lines.extend(
                [
                    f'                def Cone "{beam_name}"',
                    "                {",
                    "                    rel material:binding = </EDMGScenePlan/Looks/BeamMaterial>",
                    f"                    double radius = {_usd_number(0.08 + energy * 0.055)}",
                    f"                    double height = {_usd_number(beam_height)}",
                    f"                    double3 xformOp:translate = ({_usd_number(anchor_x + x_bias)}, {_usd_number(1.6 + energy * 0.28)}, {_usd_number(anchor_z - 0.62)})",
                    f"                    double3 xformOp:rotateXYZ = (-58, 0, {_usd_number(rot_z)})",
                ]
            )
            _append_time_samples(lines, "                    ", "double3 xformOp:scale", beam_scale)
            lines.extend(
                [
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    "                }",
                ]
            )

        for gate_name, x_bias in (("GreenGate_Left", -0.62), ("GreenGate_Right", 0.62)):
            lines.extend(
                [
                    f'                def Cylinder "{gate_name}"',
                    "                {",
                    "                    rel material:binding = </EDMGScenePlan/Looks/GreenGlassMaterial>",
                    "                    double radius = 0.038",
                    f"                    double height = {_usd_number(gate_height)}",
                    f"                    double3 xformOp:translate = ({_usd_number(anchor_x + x_bias)}, {_usd_number(gate_height / 2.0)}, {_usd_number(anchor_z + 0.42)})",
                    '                    uniform token[] xformOpOrder = ["xformOp:translate"]',
                    "                }",
                ]
            )

        for particle_index in range(1, 7):
            angle = (index * 0.62) + (particle_index * math.tau / 6.0)
            orbit_radius = 0.72 + (energy * 0.32) + (0.06 * (particle_index % 3))
            start_pos = (
                anchor_x + math.cos(angle) * orbit_radius,
                1.08 + energy * 0.4 + (particle_index % 2) * 0.22,
                anchor_z + math.sin(angle) * orbit_radius * 0.58,
            )
            mid_pos = (
                anchor_x + math.cos(angle + 1.2) * orbit_radius * 1.05,
                1.35 + energy * 0.48 + (particle_index % 3) * 0.14,
                anchor_z + math.sin(angle + 1.2) * orbit_radius * 0.66,
            )
            end_pos = (
                anchor_x + math.cos(angle + 2.2) * orbit_radius * 0.94,
                1.12 + energy * 0.36 + (particle_index % 2) * 0.16,
                anchor_z + math.sin(angle + 2.2) * orbit_radius * 0.62,
            )
            particle_samples = _dedupe_samples(
                [
                    (start_frame, _usd_color(start_pos)),
                    (mid_frame, _usd_color(mid_pos)),
                    (end_scene_frame, _usd_color(end_pos)),
                ]
            )
            particle_scale = 0.7 + energy * 0.36 + (particle_index % 2) * 0.12
            lines.extend(
                [
                    f'                def Sphere "Particle_{particle_index:02d}"',
                    "                {",
                    "                    rel material:binding = </EDMGScenePlan/Looks/ParticleMaterial>",
                    f"                    double radius = {_usd_number(0.018 + energy * 0.012)}",
                ]
            )
            _append_time_samples(lines, "                    ", "double3 xformOp:translate", particle_samples)
            _append_time_samples(
                lines,
                "                    ",
                "double3 xformOp:scale",
                _dedupe_samples(
                    [
                        (start_frame, f"({_usd_number(particle_scale)}, {_usd_number(particle_scale)}, {_usd_number(particle_scale)})"),
                        (mid_frame, f"({_usd_number(particle_scale * 1.28)}, {_usd_number(particle_scale * 1.28)}, {_usd_number(particle_scale * 1.28)})"),
                        (end_scene_frame, f"({_usd_number(particle_scale * 0.92)}, {_usd_number(particle_scale * 0.92)}, {_usd_number(particle_scale * 0.92)})"),
                    ]
                ),
            )
            lines.extend(
                [
                    '                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                    "                }",
                ]
            )

        lines.extend(
            [
                "            }",
            ]
        )

    lines.extend(
        [
            "        }",
            "    }",
            "",
            '    def Scope "Scenes"',
            "    {",
        ]
    )

    for index, scene in enumerate(normalized["scenes"], start=1):
        prim_name = _usd_identifier(scene.get("id"), f"Scene_{index:03d}")
        product_path = _render_product_path(project_id, scene.get("id"), index)
        lines.extend(
            [
                f'        def Xform "{prim_name}"',
                "        {",
                f"            custom string edmg:id = {_usd_string(scene.get('id'))}",
                f"            custom double edmg:startSeconds = {_usd_number(scene.get('start_s'))}",
                f"            custom double edmg:endSeconds = {_usd_number(scene.get('end_s'))}",
                f"            custom int edmg:startFrame = {_frame_at_seconds(scene.get('start_s'))}",
                f"            custom int edmg:endFrame = {_frame_at_seconds(scene.get('end_s'))}",
                f"            custom string edmg:prompt = {_usd_string(scene.get('prompt'))}",
                f"            custom string edmg:camera = {_usd_string(scene.get('camera'))}",
                f"            custom string edmg:look = {_usd_string(scene.get('look'))}",
                f"            custom string edmg:motion = {_usd_string(scene.get('motion'))}",
                f"            custom string edmg:variant = {_usd_string(scene.get('usd_variant'))}",
                f"            custom string edmg:visualPlate = {_usd_string(_scene_visual_plate(scene))}",
                f"            custom string edmg:renderProduct = {_usd_string(product_path)}",
                f'            custom string edmg:cameraPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotCameras/Camera_{prim_name}")}',
                f'            custom string edmg:keyLightPrim = {_usd_string(f"/EDMGScenePlan/Production/ShotLights/Key_{prim_name}")}',
                f'            custom string edmg:assetSlotPrim = {_usd_string(f"/EDMGScenePlan/Production/AssetSlots/Slot_{prim_name}")}',
                "        }",
            ]
        )

    lines.extend(["    }", "}"])
    return "\n".join(lines) + "\n"
