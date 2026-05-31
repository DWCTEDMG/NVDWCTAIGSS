from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScenePlanSummary:
    title: str
    project_id: str
    scene_count: int
    duration_s: float | None
    provider: str
    saved_at: str
    usda_bytes: int
    export_path: str
    status_text: str


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_filename_tag(value: str, default: str = "scene_plan") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return cleaned or default


def build_generation_payload(
    *,
    title: Any,
    duration_s: Any,
    bpm: Any,
    max_scenes: Any,
    notes: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "num_variants": 1,
        "variant_index": 0,
    }
    title_text = str(title or "").strip()
    if title_text:
        payload["title"] = title_text

    parsed_duration = _float_or_none(duration_s)
    payload["duration_s"] = parsed_duration if parsed_duration is not None else 60.0

    parsed_bpm = _float_or_none(bpm)
    if parsed_bpm is not None:
        payload["bpm"] = parsed_bpm

    payload["max_scenes"] = max(1, min(64, _int_or_default(max_scenes, 6)))

    notes_text = str(notes or "").strip()
    if notes_text:
        payload["user_notes"] = notes_text
    return payload


def extract_usda_text(response: dict[str, Any]) -> str:
    stage = response.get("usd_stage") if isinstance(response.get("usd_stage"), dict) else {}
    if stage.get("format") != "usda":
        return ""
    text = stage.get("text")
    return text if isinstance(text, str) else ""


def summarize_scene_plan_response(response: dict[str, Any]) -> ScenePlanSummary:
    scene_plan = response.get("scene_plan") if isinstance(response.get("scene_plan"), dict) else {}
    planner = response.get("planner") if isinstance(response.get("planner"), dict) else {}
    export = response.get("export") if isinstance(response.get("export"), dict) else {}
    scenes = scene_plan.get("scenes") if isinstance(scene_plan.get("scenes"), list) else []
    scene_count = len(scenes) or int(export.get("scene_count") or 0)
    duration = _float_or_none(scene_plan.get("duration_s"))
    title = str(scene_plan.get("title") or response.get("title") or "EDMG NVIDIA scene plan")
    project_id = str(response.get("project_id") or scene_plan.get("project_id") or "")
    provider = str(
        scene_plan.get("provider")
        or " ".join(str(part) for part in (planner.get("provider"), planner.get("model")) if part)
        or ""
    )
    saved_at = str(response.get("saved_at") or export.get("created_at") or "")
    usda_text = extract_usda_text(response)
    export_path = str(export.get("path") or "")
    duration_text = f", {duration:g}s" if duration is not None else ""
    provider_text = f", {provider}" if provider else ""
    status_text = f"{title}: {scene_count} scenes{duration_text}{provider_text}"
    if export_path:
        status_text = f"{status_text}, exported {export_path}"
    return ScenePlanSummary(
        title=title,
        project_id=project_id,
        scene_count=scene_count,
        duration_s=duration,
        provider=provider,
        saved_at=saved_at,
        usda_bytes=len(usda_text.encode("utf-8")),
        export_path=export_path,
        status_text=status_text,
    )


def write_usda_preview(response: dict[str, Any], *, root: str | Path | None = None) -> Path:
    text = extract_usda_text(response)
    if not text.strip():
        raise ValueError("scene-plan response did not include USDA text")
    scene_plan = response.get("scene_plan") if isinstance(response.get("scene_plan"), dict) else {}
    project_id = str(response.get("project_id") or scene_plan.get("project_id") or "").strip()
    title = str(scene_plan.get("title") or response.get("title") or "scene_plan").strip()
    tag = _safe_filename_tag(project_id or title)
    out_root = Path(root) if root is not None else Path(tempfile.gettempdir()) / "edmg_nvidia_kit"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{tag}.usda"
    out_path.write_text(text, encoding="utf-8", newline="\n")
    return out_path
